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
    def create_task(self, **values):
        status, task = self.web('POST', '/api/tasks', {'title': 'Тестовая таска', **values}, role='Другой участник')
        self.assertEqual(status, 201, task)
        return task

    def edit(self, path, data):
        return self.web('PATCH', path, data, role='Другой участник')

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
        self.edit(f"/api/tasks/{task['id']}",{'version':1,'description':'Detail only'})
        updates = self.claim()
        self.assertEqual([e['kind'] for e in updates], ['updated'])
        self.assertIn('Detail only', updates[0]['text'])
        self.ack(updates[0])
        self.edit(f"/api/tasks/{task['id']}", {'version': 2, 'description': 'Detail only'})
        self.assertEqual(self.claim(), [])
        status,changed=self.edit(f"/api/tasks/{task['id']}",{'version':2,'assignee_ids':[]})
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
        self.edit(f"/api/tasks/{task['id']}",{'version':1,'deadline':'2030-06-12'})
        self.assertEqual(len(self.claim(datetime(2030,6,11,7,0))),1)
        self.edit(f"/api/tasks/{task['id']}",{'version':2,'completed':True})
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
        self.edit(f"/api/tasks/{task['id']}",{'version':1,'assignee_ids':[]})
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

    def test_creator_gets_external_edit_and_actor_does_not(self):
        self.bind()
        self.bind(tid=102, role='Другой участник')
        task = self.create_task(assignee_ids=[self.users['Участник'], self.users['Другой участник']])
        self.assertEqual([r['telegram_user_id'] for r in self.claim()], [101])
        self.web('PATCH', f"/api/tasks/{task['id']}", {'version': 1, 'title': 'Новое название', 'completed': True})
        rows = self.claim()
        self.assertEqual([(r['telegram_user_id'], r['kind']) for r in rows], [(102, 'updated')])
        self.assertIn('Новое название', rows[0]['text'])
        self.assertIn('Статус', rows[0]['text'])

    def test_mute_at_claim_and_enqueue_including_deadline(self):
        self.bind()
        uid = self.users['Участник']
        task = self.create_task(assignee_ids=[uid], deadline='2030-06-11')
        mute = models.TaskNotificationMute(user_id=uid, scope=task['id'])
        self.db.add(mute)
        self.db.commit()
        self.assertEqual(self.claim(), [])
        self.edit(f"/api/tasks/{task['id']}", {'version': 1, 'description': 'Hidden'})
        self.assertEqual(self.claim(datetime(2030,6,10,7)), [])
        self.assertEqual(self.db.query(models.TelegramNotification).count(), 1)
        self.db.delete(mute)
        self.db.add(models.TaskNotificationMute(user_id=uid, scope=0))
        self.db.commit()
        self.create_task(assignee_ids=[uid])
        self.assertEqual(self.db.query(models.TelegramNotification).count(), 1)

    def test_creator_deadline_is_deduplicated_and_delivered_without_assignment(self):
        self.bind(tid=102, role='Другой участник')
        self.bind()
        creator = self.users['Другой участник']
        first = self.create_task(assignee_ids=[creator], deadline='2030-06-11')
        second = self.create_task(assignee_ids=[self.users['Участник']], deadline='2030-06-11')
        deadlines = self.claim(datetime(2030, 6, 10, 7))
        self.assertCountEqual([(r['task_id'], r['telegram_user_id']) for r in deadlines],
                              [(first['id'], 102), (second['id'], 102), (second['id'], 101)])

    def test_sequential_edits_keep_event_values_and_recheck_old_card_acl(self):
        self.bind()
        task = self.create_task(assignee_ids=[self.users['Участник']], event_card_id=self.cards['Посвящение'])
        self.ack(self.claim()[0])
        path = f"/api/tasks/{task['id']}"
        self.edit(path, {'version': 1, 'title': 'Version two'})
        self.edit(path, {'version': 2, 'title': 'Version three'})
        first, second = self.claim()
        self.assertIn('Version two', first['text'])
        self.assertNotIn('Version three', first['text'])
        self.assertIn('Version three', second['text'])
        self.ack(first)
        self.ack(second)
        self.edit(path, {'version': 3, 'event_card_id': None})
        self.db.get(models.Card,self.cards['Посвящение']).access_logins = 'nobody'
        self.db.commit()
        change, = self.claim()
        self.assertNotIn('Посвящение', change['text'])

    def test_assignment_on_completed_edit_reaches_confirmed_account(self):
        self.bind(tid=303)
        self.db.get(models.User, self.users['Участник']).telegram = '@another_account'
        self.db.commit()
        task = self.create_task()
        path = f"/api/tasks/{task['id']}"
        response = self.edit(path, {'version': 1, 'assignee_ids': [self.users['Участник']], 'completed': True})
        self.assertEqual(response[0], 200)
        entries = self.claim()
        self.assertEqual([(r['kind'], r['chat_id']) for r in entries], [('assigned', 303)])
        self.assertIn('выполнена', entries[0]['text'])

    def validate(self, entry):
        status, result = self.bot('POST', f"service/notifications/{entry['id']}/validate",
                                  {'lease_token': entry['lease_token']})
        self.assertEqual(status, 200, result)
        return result

    def test_validate_rechecks_removed_title_and_assignment_after_claim(self):
        self.bind()
        uid = self.users['Участник']
        card = self.db.get(models.Card, self.cards['Посвящение'])
        task = self.create_task(assignee_ids=[uid], event_card_id=card.id)
        path = f"/api/tasks/{task['id']}"
        assigned, = self.claim()
        self.assertEqual(self.validate(assigned), {'valid':True, 'text':assigned['text']})
        self.edit(path, {'version': 1, 'assignee_ids': []})
        self.assertEqual(self.validate(assigned), {'valid':False})
        removed, = self.claim()
        self.assertIn(task['title'], removed['text'])
        card.access_logins = 'nobody'
        self.db.commit()
        checked = self.validate(removed)
        self.assertTrue(checked['valid'])
        self.assertNotIn(task['title'], checked['text'])
        row = self.db.get(models.TelegramNotification, removed['id'])
        self.assertEqual((row.attempts, row.lease_token), (1, removed['lease_token']))
        card.access_logins = None
        self.db.commit()
        self.edit(path, {'version': 2, 'assignee_ids': [uid]})
        self.assertEqual(self.validate(removed), {'valid':False})

    def test_validate_redacts_historical_card_removed_from_current_task(self):
        self.bind()
        card = self.db.get(models.Card, self.cards['Посвящение'])
        task = self.create_task(assignee_ids=[self.users['Участник']], event_card_id=card.id)
        self.ack(self.claim()[0])
        self.edit(f"/api/tasks/{task['id']}", {'version': 1, 'event_card_id': None})
        entry, = self.claim()
        self.assertIn('Посвящение', entry['text'])
        card.access_logins = 'nobody'
        self.db.commit()
        checked = self.validate(entry)
        self.assertTrue(checked['valid'])
        self.assertNotIn('Посвящение', checked['text'])
        self.assertEqual(self.validate({**entry, 'lease_token': 'invalid-token-' * 2}), {'valid':False})
        with patch('telegram_notifications.now',return_value=datetime.utcnow()+timedelta(minutes=3)):
            self.assertEqual(self.validate(entry), {'valid':False})
