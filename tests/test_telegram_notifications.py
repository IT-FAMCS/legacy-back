from datetime import datetime, timedelta
from unittest.mock import patch
import unittest
import test_telegram as identity
import models
import database

class NotificationTests(unittest.TestCase):
    setUp=identity.TelegramTests.setUp
    tearDown=identity.TelegramTests.tearDown
    tearDownClass=classmethod(lambda cls: database.engine.dispose())
    web=identity.TelegramTests.web
    bot=identity.TelegramTests.bot
    issue=identity.TelegramTests.issue
    bind=identity.TelegramTests.bind
    create_task=identity.TelegramTests.create_task

    def claim(self, at=None):
        if at is None:
            status,result=self.bot('POST','service/notifications/claim',{'limit':20})
        else:
            with patch('telegram_notifications.now',return_value=at):
                status,result=self.bot('POST','service/notifications/claim',{'limit':20})
        self.assertEqual(status,200,result)
        return result

    def ack(self, entry, outcome='delivered', **values):
        return self.bot('POST',f"service/notifications/{entry['id']}/ack",
                        {'lease_token':entry['lease_token'],'outcome':outcome,**values})

    def test_assignment_creation_and_removal_are_atomic_and_noop_edits_do_not_notify(self):
        self.bind()
        task=self.create_task(assignee_ids=[self.users['Участник']])
        self.assertEqual(self.db.query(models.TelegramNotification).count(),1)
        deliveries=self.claim()
        self.assertEqual(len(deliveries),1)
        self.assertEqual(deliveries[0]['kind'],'assigned')
        self.assertIn('назначена',deliveries[0]['text'])
        self.assertEqual(self.ack(deliveries[0])[0],200)
        self.assertEqual(self.ack(deliveries[0])[0],200)
        self.web('PATCH',f"/api/tasks/{task['id']}",{'version':1,'description':'Detail only'})
        self.assertEqual(self.claim(),[])
        status,changed=self.web('PATCH',f"/api/tasks/{task['id']}",{'version':2,'assignee_ids':[]})
        self.assertEqual(status,200)
        removed=self.claim()
        self.assertEqual([x['kind'] for x in removed],['removed'])
        self.assertIn('сняли',removed[0]['text'])

    def test_no_historical_assignments_are_backfilled_on_first_link(self):
        self.create_task(assignee_ids=[self.users['Участник']])
        self.bind()
        self.assertEqual(self.claim(),[])

    def test_deadline_timezone_dedup_and_completed_or_archived_suppression(self):
        self.bind()
        # 07:00 UTC is 10:00 Moscow, the day before the deadline.
        task=self.create_task(assignee_ids=[self.users['Участник']],deadline='2030-06-11')
        for entry in self.claim():self.ack(entry)
        self.assertEqual(self.claim(datetime(2030,6,10,6,59)),[])
        entries=self.claim(datetime(2030,6,10,7,0))
        self.assertEqual([e['kind'] for e in entries],['deadline'])
        self.assertIn('Завтра',entries[0]['text'])
        self.ack(entries[0])
        self.assertEqual(self.claim(datetime(2030,6,10,12,0)),[])
        self.web('PATCH',f"/api/tasks/{task['id']}",{'version':1,'deadline':'2030-06-12'})
        self.assertEqual(len(self.claim(datetime(2030,6,11,7,0))),1)
        self.web('PATCH',f"/api/tasks/{task['id']}",{'version':2,'completed':True})
        self.assertEqual(self.claim(datetime(2030,6,11,8,0)),[])

    def test_expired_lease_is_reclaimed_old_ack_rejected_and_retry_is_delayed(self):
        self.bind()
        self.create_task(assignee_ids=[self.users['Участник']])
        first=self.claim()[0]
        self.assertEqual(self.claim(),[])
        row=self.db.get(models.TelegramNotification,first['id'])
        row.lease_until=datetime.utcnow()-timedelta(seconds=1)
        self.db.commit()
        second=self.claim()[0]
        self.assertNotEqual(first['lease_token'],second['lease_token'])
        self.assertEqual(self.ack(first)[0],409)
        self.assertEqual(self.ack(second,'retry',retry_after=120)[0],200)
        self.assertEqual(self.claim(),[])

    def test_unlink_relink_cannot_deliver_old_generation_and_disabled_users_are_skipped(self):
        self.bind()
        self.create_task(assignee_ids=[self.users['Участник']])
        self.web('DELETE')
        self.bind(tid=303)
        self.assertEqual(self.claim(),[])
        self.create_task(assignee_ids=[self.users['Участник']])
        self.db.get(models.User,self.users['Участник']).is_deactivated=True
        self.db.commit()
        self.assertEqual(self.claim(),[])

    def test_superseded_assignment_and_restricted_removal_do_not_leak_details(self):
        self.bind()
        task=self.create_task(assignee_ids=[self.users['Участник']],event_card_id=self.cards['Посвящение'])
        self.web('PATCH',f"/api/tasks/{task['id']}",{'version':1,'assignee_ids':[]})
        self.db.get(models.Card,self.cards['Посвящение']).access_logins='nobody'
        self.db.commit()
        entries=self.claim()
        self.assertEqual([e['kind'] for e in entries],['removed'])
        self.assertNotIn(task['title'],entries[0]['text'])
        self.assertNotIn('Посвящение',entries[0]['text'])

    def test_blocked_chat_suspended_until_contact_resumes(self):
        self.bind()
        self.create_task(assignee_ids=[self.users['Участник']])
        entry=self.claim()[0]
        self.assertEqual(self.ack(entry,'blocked')[0],200)
        self.db.expire_all()
        binding=self.db.query(models.TelegramBinding).filter_by(user_id=self.users['Участник']).one()
        self.assertTrue(binding.blocked)
        self.bot('GET','bot/me?contact=true')
        self.db.expire_all()
        self.assertFalse(self.db.get(models.TelegramBinding,binding.id).blocked)
