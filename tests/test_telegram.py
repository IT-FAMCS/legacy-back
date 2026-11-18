import asyncio
import os
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import test_tasks as fixtures
from test_trainee_access import asgi_request
import models
from sqlalchemy import select
import database

KEY = 'test-service-key-' + 'a' * 40
class TelegramTests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        database.engine.dispose()

    def setUp(self):
        fixtures.TaskTests.setUp(self)
        self.env = patch.dict(os.environ, {'TELEGRAM_SERVICE_KEY': KEY, 'TELEGRAM_BOT_USERNAME': 'LegacyTestBot'})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.db.close()

    def web(self, method, path='/api/telegram/link', body=None, role='Участник'):
        return asyncio.run(asgi_request(method, path, body, self.tokens.get(role)))

    def bot(self, method, path, body=None, tid=101, key=KEY, request_id=None, generation=None):
        headers = {'X-Telegram-Service-Key': key, 'X-Telegram-User-ID': tid}
        if request_id is not None:
            headers['X-Telegram-Request-ID'] = request_id
        generation = generation or self.db.execute(select(models.TelegramBinding.generation).where(models.TelegramBinding.telegram_user_id==tid, models.TelegramBinding.active.is_(True))).scalar()
        if generation: headers['X-Telegram-Binding'] = generation
        return asyncio.run(asgi_request(method, '/api/telegram/' + path, body, extra_headers=headers))

    def issue(self, role='Участник'):
        status, data = self.web('POST', role=role)
        self.assertEqual(status, 201, data)
        self.assertIn('https://t.me/LegacyTestBot?start=link_', data['url'])
        return data

    def bind(self, tid=101, role='Участник'):
        challenge = self.issue(role)
        status, pending = self.bot('POST', 'bot/claim', {'token': challenge['token'], 'telegram_user_id': tid,
            'chat_id': tid, 'username': 'claimed_name', 'display_name': 'Telegram Member'}, tid=tid)
        self.assertEqual(status, 200, pending)
        status, binding = self.web('POST', '/api/telegram/link/confirm', {'challenge_id': challenge['id']}, role=role)
        self.assertEqual(status, 200, binding)
        return binding

    def create_task(self, **values):
        status, data = self.web('POST', '/api/tasks', {'title': 'Таска для Telegram', **values})
        self.assertEqual(status, 201, data)
        return data

    def test_binding_requires_explicit_website_confirmation_and_no_contact_match(self):
        self.db.get(models.User, self.users['Участник']).telegram = '@claimed_name'
        self.db.commit()
        self.assertEqual(self.bot('GET', 'bot/me')[0], 403)
        c = self.issue()
        self.assertEqual(self.bot('POST', 'bot/claim', {'token': c['token'], 'telegram_user_id': 101, 'chat_id': 101})[0], 200)
        self.assertEqual(self.bot('GET', 'bot/me')[0], 403)
        state = self.web('GET')[1]
        self.assertEqual(state['pending']['telegram_user_id'], 101)
        self.assertNotIn(c['token'], str(state))
        self.assertEqual(self.web('POST', '/api/telegram/link/confirm', {'challenge_id': c['id']}, role='Другой участник')[0], 404)
        self.assertEqual(self.web('POST', '/api/telegram/link/confirm', {'challenge_id': c['id']})[0], 200)
        status, me = self.bot('GET', 'bot/me')
        self.assertEqual(status, 200)
        self.assertEqual(me['user_id'], self.users['Участник'])
        self.assertTrue(me['generation'])
        self.assertEqual(self.bot('POST', 'bot/claim', {'token': c['token'], 'telegram_user_id': 101, 'chat_id': 101})[0], 410)

    def test_claim_expiry_cancellation_and_other_telegram_cannot_replace_claim(self):
        c = self.issue()
        self.assertEqual(self.bot('POST','bot/claim',{'token':c['token'],'telegram_user_id':101,'chat_id':101})[0],200)
        self.assertEqual(self.bot('POST','bot/claim',{'token':c['token'],'telegram_user_id':102,'chat_id':102},tid=102)[0],409)
        self.assertEqual(self.web('DELETE')[0],200)
        self.assertEqual(self.web('POST','/api/telegram/link/confirm',{'challenge_id':c['id']})[0],410)
        c = self.issue()
        row = self.db.get(models.TelegramLinkChallenge,c['id'])
        row.expires_at = datetime.utcnow() - timedelta(seconds=1)
        self.db.commit()
        self.assertEqual(self.bot('POST','bot/claim',{'token':c['token'],'telegram_user_id':101,'chat_id':101})[0],410)

    def test_numeric_identity_uniqueness_and_unlink_deactivation(self):
        self.bind()
        c=self.issue('Другой участник')
        self.assertEqual(self.bot('POST','bot/claim',{'token':c['token'],'telegram_user_id':101,'chat_id':101})[0],409)
        self.db.get(models.User,self.users['Участник']).is_deactivated=True
        self.db.commit()
        self.assertEqual(self.bot('GET','bot/me')[0],403)
        self.db.get(models.User,self.users['Участник']).is_deactivated=False
        self.db.commit()
        self.assertEqual(self.web('DELETE')[0],200)
        self.assertEqual(self.bot('GET','bot/me')[0],403)

    def test_invalid_service_key_group_identity_and_disabled_integration(self):
        self.assertEqual(self.bot('GET','bot/me',key='bad')[0],401)
        c=self.issue()
        self.assertEqual(self.bot('POST','bot/claim',{'token':c['token'],'telegram_user_id':101,'chat_id':-999})[0],422)
        with patch.dict(os.environ, {'TELEGRAM_SERVICE_KEY':''}):
            self.assertFalse(self.web('GET')[1]['enabled'])
            self.assertEqual(self.bot('GET','bot/me')[0],503)

    def test_own_task_scope_completion_policy_and_receipt(self):
        self.bind()
        own=self.create_task(assignee_ids=[self.users['Участник']])
        other=self.create_task(assignee_ids=[self.users['Другой участник']])
        status, rows=self.bot('GET','bot/tasks')
        self.assertEqual(status,200)
        self.assertEqual([r['id'] for r in rows],[own['id']])
        self.assertEqual(self.bot('GET',f"bot/tasks/{other['id']}")[0],200)
        self.assertEqual({row['id'] for row in self.bot('GET','bot/tasks?include_created=true')[1]}, {own['id'],other['id']})
        self.assertEqual(self.bot('PATCH',f"bot/tasks/{other['id']}",{'version':1,'completed':True},request_id='creator-only')[0],200)
        self.assertEqual(self.bot('PATCH',f"bot/tasks/{other['id']}",{'version':2,'completed':False},request_id='creator-reopen')[0],200)
        path=f"bot/tasks/{own['id']}"
        self.assertEqual(self.bot('PATCH',path,{'version':1,'completed':True})[0],422)
        status,done=self.bot('PATCH',path,{'version':1,'completed':True},request_id='complete-1')
        self.assertEqual(status,200,done)
        self.assertTrue(done['completed'])
        self.assertEqual(self.bot('PATCH',path,{'version':1,'completed':True},request_id='complete-1')[1],done)
        self.assertEqual(self.bot('GET','bot/tasks')[1],[])
        self.assertEqual(self.db.query(models.TaskHistory).filter_by(task_id=own['id']).count(),2)
        self.assertEqual(self.bot('DELETE',path,{'version':done['version']},request_id='delete')[0],405)

    def test_bot_task_creation_and_shared_edits_with_receipts(self):
        self.bind()
        options = self.bot('GET', 'bot/tasks/options')
        self.assertEqual(options, self.web('GET', '/api/tasks/options'))
        uid = self.users['Другой участник']
        data = {'title':'Первая', 'description':'Текст', 'event_card_id':self.cards['Посвящение'],
                'department_card_ids':[self.cards['IT']], 'assignee_ids':[uid], 'deadline':'2030-01-01'}
        self.assertEqual(self.bot('POST','bot/tasks',{**data,'creator_id':uid},request_id='spoof')[0],422)
        status, task = self.bot('POST','bot/tasks',data,request_id='create')
        self.assertEqual(status,201,task)
        self.assertEqual(task['creator_id'],self.users['Участник'])
        self.assertEqual(self.bot('POST','bot/tasks',data,request_id='create'), (201,task))
        self.assertEqual(self.bot('POST','bot/tasks',{**data,'title':'Другая'},request_id='create')[0],409)
        self.assertEqual(self.db.query(models.Task).count(),1)
        self.assertEqual(self.db.query(models.TaskHistory).count(),1)

        # Unassigned users have the same ordinary edit permissions as on the website.
        self.bind(tid=202,role='Председатель')
        self.assertEqual(self.bot('GET','bot/tasks',tid=202)[1],[])
        status, rows = self.bot('GET','bot/tasks?scope=all',tid=202)
        self.assertEqual(status,200)
        self.assertEqual([row['id'] for row in rows],[task['id']])
        path = f"bot/tasks/{task['id']}"
        self.assertFalse(self.bot('GET',path,tid=202)[1]['can_change_status'])
        edit = {'version':1,'title':'Исправленная','description':'Новый текст','event_card_id':None,
                'department_card_ids':[], 'assignee_ids':[], 'deadline':None}
        self.assertEqual(self.bot('PATCH',path,{**edit,'completed':True},tid=202,request_id='deny')[0],403)
        status, result = self.bot('PATCH',path,edit,tid=202,request_id='edit')
        self.assertEqual(status,200,result)
        for field in set(edit) - {'version'}:
            self.assertEqual(result[field],edit[field])
        self.assertEqual(self.bot('PATCH',path,edit,tid=202,request_id='edit')[1],result)
        self.assertEqual(self.bot('PATCH',path,edit,tid=202,request_id='stale')[0],409)
        self.assertEqual(self.db.query(models.TaskHistory).count(),2)

    def test_bot_task_scope_applies_card_permissions_to_lists_options_writes_and_replay(self):
        self.bind()
        cid = self.cards['Посвящение']
        data = {'title':'Скрытая','event_card_id':cid}
        status, task = self.bot('POST','bot/tasks',data,request_id='original')
        self.assertEqual(status,201,task)
        self.db.get(models.Card,cid).access_logins='nobody'
        self.db.commit()
        self.assertEqual(self.bot('GET','bot/tasks?scope=all')[1],[])
        self.assertEqual(self.bot('GET','bot/tasks/options')[1]['events'],[])
        path = f"bot/tasks/{task['id']}"
        self.assertEqual(self.bot('GET',path)[0],404)
        self.assertEqual(self.bot('PATCH',path,{'version':1,'event_card_id':None},request_id='edit')[0],404)
        self.assertEqual(self.bot('POST','bot/tasks',data,request_id='original')[0],404)
        self.assertEqual(self.bot('POST','bot/tasks',data,request_id='new')[0],404)
        self.assertEqual(self.db.query(models.Task).count(),1)

    def test_equipment_roles_and_atomic_replay_of_create_take_location(self):
        self.bind()
        self.assertEqual(self.bot('POST','bot/equipment',{'name':'Mic'},request_id='create')[0],403)
        self.bind(tid=202,role='Заместитель председателя по IT')
        status,item=self.bot('POST','bot/equipment',{'name':'Mic'},tid=202,request_id='create')
        self.assertEqual(status,201,item)
        self.assertEqual(self.bot('POST','bot/equipment',{'name':'Mic'},tid=202,request_id='create')[1],item)
        self.assertEqual(self.db.query(models.EquipmentItem).count(),1)
        self.assertEqual(self.bot('POST','bot/equipment',{'name':'Changed'},tid=202,request_id='create')[0],409)
        path=f"bot/equipment/{item['id']}/take"
        status,taken=self.bot('POST',path,{'version':item['version']},request_id='take')
        self.assertEqual(status,200,taken)
        self.assertEqual(taken['holder_user']['id'],self.users['Участник'])
        self.assertEqual(self.bot('POST',path,{'version':item['version']},request_id='take')[1],taken)
        self.assertEqual(len(self.bot('GET',f"bot/equipment/{item['id']}/history")[1]),2)
        self.assertEqual(self.bot('DELETE',f"bot/equipment/{item['id']}",{'version':taken['version']},request_id='delete')[0],403)

    def test_callback_cannot_complete_after_assignment_or_card_access_revoked(self):
        self.bind()
        task=self.create_task(assignee_ids=[self.users['Участник']],event_card_id=self.cards['Посвящение'])
        self.db.get(models.Card,self.cards['Посвящение']).access_logins='nobody'
        self.db.commit()
        self.assertEqual(self.bot('PATCH',f"bot/tasks/{task['id']}",{'version':1,'completed':True},request_id='denied')[0],404)

    def test_link_codes_are_hashed_and_issuance_limited(self):
        c=self.issue()
        row=self.db.get(models.TelegramLinkChallenge,c['id'])
        self.assertNotEqual(row.token_hash,c['token'])
        for _ in range(9):self.issue()
        self.assertEqual(self.web('POST')[0],429)

    def test_old_binding_generation_cannot_mutate_after_relink(self):
        self.bind()
        old=self.bot('GET','bot/me')[1]['generation']
        task=self.create_task(assignee_ids=[self.users['Участник']])
        self.web('DELETE')
        self.bind()
        status,_=self.bot('PATCH',f"bot/tasks/{task['id']}",{'version':1,'completed':True},request_id='stale-generation',generation=old)
        self.assertEqual(status,403)
        self.db.expire_all()
        self.assertFalse(self.db.get(models.Task,task['id']).completed)
