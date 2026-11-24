import asyncio
import os
import unittest
from unittest.mock import patch
import test_budget as fixture
from test_trainee_access import asgi_request
import main

SETTINGS = {
    'PERSONAL_DATA_OPERATOR': 'Test operator',
    'PERSONAL_DATA_ADDRESS': 'Test address',
    'PERSONAL_DATA_CONTACT': 'privacy@example.org',
    'PERSONAL_DATA_PROCESSORS': 'Test hosting in Belarus; Telegram for bot messages',
    'PERSONAL_DATA_RETENTION': 'For membership duration; requests 12 months after settlement',
}

class PersonalDataTests(unittest.TestCase):
    seed_budget_consent=False
    setUp = fixture.BudgetTests.setUp
    tearDown = fixture.BudgetTests.tearDown
    def call(self, method, path, body=None, role='member', bot=False, rid='pd'):
        headers = {'X-Telegram-Service-Key':'b'*40,'X-Telegram-User-ID':100+list(self.users).index(role),'X-Telegram-Binding':role,'X-Telegram-Request-ID':rid} if bot else None
        return asyncio.run(asgi_request(method, ('/api/telegram/bot' if bot else '/api')+path, body, None if bot else main.create_access_token({'sub':role}), headers))
    def accept(self, scope='service', role='member', bot=False):
        code, policy = self.call('GET','/personal-data/policy',role=role,bot=bot)
        self.assertEqual(code,200,policy)
        code,data=self.call('POST','/personal-data/consents',{'scope':scope,'version':policy['version']},role,bot,scope)
        self.assertEqual(code,200,data)
        return policy
    @patch.dict(os.environ, SETTINGS)
    def test_limit_ownership_and_revoke(self):
        self.assertEqual(self.call('POST','/payout-details',{'label':'Bank','details':'123'})[0],403)
        self.accept(); self.accept('saved_details')
        rows=[]
        for i in range(3):
            code,row=self.call('POST','/payout-details',{'label':f'Bank {i}','details':str(i)})
            self.assertEqual(code,201,row);rows.append(row)
        self.assertEqual(self.call('POST','/payout-details',{'label':'Fourth','details':'4'})[0],409)
        self.assertEqual(self.call('DELETE',f"/payout-details/{rows[0]['id']}",role='cashier')[0],404)
        self.assertEqual(self.call('GET','/payout-details',role='cashier')[1],[])
        self.assertEqual(len(self.call('GET','/payout-details')[1]),3)
        self.assertEqual(self.call('DELETE','/personal-data/consents/saved_details')[0],200)
        self.assertEqual(self.call('GET','/payout-details')[1],[])
    @patch.dict(os.environ, SETTINGS)
    def test_bot_replay_shared_storage_and_request_snapshot(self):
        self.accept();self.accept('saved_details')
        payload={'label':'Bank','details':'Account 123'}
        code,row=self.call('POST','/payout-details',payload,bot=True,rid='save')
        self.assertEqual(code,201,row)
        self.assertEqual(self.call('POST','/payout-details',payload,bot=True,rid='save')[1],row)
        self.assertEqual(len(self.call('GET','/payout-details')[1]),1)
        headers={'X-Telegram-Service-Key':'b'*40,'X-Telegram-User-ID':101,'X-Telegram-Binding':'member','X-Telegram-Request-ID':'request-saved'}
        code,request=asyncio.run(asgi_request('POST','/api/telegram/bot/budget/requests',{'request_type':'reimbursement','description':'Supplies','amount':'12.34','saved_payout_id':row['id'],'telegram_file_id':'receipt'},extra_headers=headers))
        self.assertEqual(code,201,request);self.assertEqual(request['payout_details'],'Account 123')
        self.assertEqual(self.call('DELETE',f"/payout-details/{row['id']}")[0],200)
        import models
        self.assertEqual(self.db.get(models.BudgetRequest,request['id']).payout_details,'Account 123')
    @patch.dict(os.environ, SETTINGS)
    def test_policy_version_and_service_revoke(self):
        policy=self.accept();self.accept('saved_details')
        self.assertTrue(self.call('GET','/personal-data/me')[1]['service'])
        with patch.dict(os.environ, {'PERSONAL_DATA_RETENTION':'Changed retention'}):
            self.assertFalse(self.call('GET','/personal-data/me')[1]['service'])
            self.assertEqual(self.call('POST','/personal-data/consents',{'scope':'service','version':policy['version']})[0],409)
        self.assertEqual(self.call('DELETE','/personal-data/consents/service')[0],200)
        self.assertFalse(self.call('GET','/personal-data/me')[1]['saved_details'])
    @patch.dict(os.environ, {'PERSONAL_DATA_OPERATOR':'','PERSONAL_DATA_ADDRESS':'','PERSONAL_DATA_CONTACT':'','PERSONAL_DATA_PROCESSORS':'','PERSONAL_DATA_RETENTION':''})
    def test_default_policy_uses_admin_contact_and_accepts_without_operator_fields(self):
        code,policy=self.call('GET','/personal-data/policy')
        self.assertEqual(code,200,policy);self.assertTrue(policy['configured'])
        self.assertEqual(policy['operator'],'');self.assertEqual(policy['address'],'')
        self.assertEqual(policy['contact'],'https://famcs-legacy.by/user/admin')
        self.assertIn('Республика Беларусь',policy['processors'])
        self.assertIn('не ограничен',policy['retention'])
        self.assertEqual(self.call('POST','/personal-data/consents',{'scope':'service','version':policy['version']})[0],200)

    @patch.dict(os.environ, SETTINGS)
    def test_concurrent_limit_is_atomic(self):
        from concurrent.futures import ThreadPoolExecutor
        self.accept();self.accept('saved_details')
        for i in range(2):self.assertEqual(self.call('POST','/payout-details',{'label':str(i),'details':str(i)})[0],201)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda i:self.call('POST','/payout-details',{'label':str(i),'details':str(i)}),range(2)))
        self.assertEqual(sorted(code for code,_ in results),[201,409],results)
        self.assertEqual(len(self.call('GET','/payout-details')[1]),3)

    @patch.dict(os.environ, SETTINGS)
    def test_foreign_selection_and_deleted_replay(self):
        import models
        self.accept();self.accept('saved_details')
        code,row=self.call('POST','/payout-details',{'label':'Private bank','details':'secret-bank-account'},bot=True,rid='private-save')
        self.assertEqual(code,201,row)
        self.accept(role='cashier');self.accept('saved_details',role='cashier')
        headers={'X-Telegram-Service-Key':'b'*40,'X-Telegram-User-ID':102,'X-Telegram-Binding':'cashier','X-Telegram-Request-ID':'foreign-selection'}
        body={'request_type':'reimbursement','description':'Supplies','amount':'1','saved_payout_id':row['id'],'telegram_file_id':'receipt'}
        code,result=asyncio.run(asgi_request('POST','/api/telegram/bot/budget/requests',body,extra_headers=headers))
        self.assertEqual(code,404,result)
        self.assertEqual(self.db.query(models.BudgetRequest).count(),0)
        self.assertEqual(self.call('DELETE',f"/payout-details/{row['id']}")[0],200)
        self.assertEqual(self.call('POST','/payout-details',{'label':'Private bank','details':'secret-bank-account'},bot=True,rid='private-save')[0],409)
        self.assertFalse(any('secret-bank-account' in r.response_json for r in self.db.query(models.TelegramRequestReceipt).all()))

    @patch.dict(os.environ, SETTINGS)
    def test_manual_request_requires_service_consent(self):
        headers={'X-Telegram-Service-Key':'b'*40,'X-Telegram-User-ID':101,'X-Telegram-Binding':'member','X-Telegram-Request-ID':'unconsented'}
        body={'request_type':'reimbursement','description':'Supplies','amount':'1','payout_details':'new account','telegram_file_id':'receipt'}
        self.assertEqual(asyncio.run(asgi_request('POST','/api/telegram/bot/budget/requests',body,extra_headers=headers))[0],403)
        self.accept()
        self.assertEqual(asyncio.run(asgi_request('POST','/api/telegram/bot/budget/requests',body,extra_headers=headers))[0],201)

    @patch.dict(os.environ, SETTINGS)
    def test_deleted_template_identity_is_never_reused(self):
        self.accept();self.accept('saved_details')
        code,first=self.call('POST','/payout-details',{'label':'Old','details':'old-account'},bot=True,rid='old-template')
        self.assertEqual(code,201,first)
        self.assertEqual(self.call('DELETE',f"/payout-details/{first['id']}")[0],200)
        code,second=self.call('POST','/payout-details',{'label':'New','details':'new-account'},bot=True,rid='new-template')
        self.assertEqual(code,201,second);self.assertNotEqual(first['id'],second['id'])
        self.assertEqual(self.call('POST','/payout-details',{'label':'Old','details':'old-account'},bot=True,rid='old-template')[0],409)
        self.assertEqual(self.call('DELETE',f"/payout-details/{first['id']}")[0],404)
        self.assertEqual(self.call('GET','/payout-details')[1],[second])
