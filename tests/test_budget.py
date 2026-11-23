import asyncio
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from test_trainee_access import asgi_request
import main, models, database

class BudgetTests(unittest.TestCase):
    def setUp(self):
        models.Base.metadata.drop_all(database.engine)
        models.Base.metadata.create_all(database.engine)
        os.environ['TELEGRAM_SERVICE_KEY']='b'*40
        self.db=database.SessionLocal()
        self.users={}
        for i, role in enumerate(['admin','member','cashier','cashier2','председатель','заместитель председателя','secretary']):
            pos=models.Position(name=role,is_kaznachey=role.startswith('cashier'))
            self.db.add(pos); self.db.flush()
            user=models.User(login=role,first_name='Test',last_name=role,password_hash='unused',position_id=pos.id)
            self.db.add(user); self.db.flush()
            self.users[role]=user.id
            self.db.add(models.TelegramBinding(user_id=user.id,telegram_user_id=100+i,chat_id=100+i,generation=role,active=True))
        self.db.commit()
    def tearDown(self):
        self.db.close()
        database.engine.dispose()
    def req(self,method,path='',body=None,role='admin',bot=False,rid='test'):
        headers={'X-Telegram-Service-Key':'b'*40,'X-Telegram-User-ID':100+list(self.users).index(role),'X-Telegram-Binding':role,'X-Telegram-Request-ID':rid} if bot else None
        return asyncio.run(asgi_request(method,('/api/telegram/bot/budget' if bot else '/api/budget')+path,body,main.create_access_token({'sub':role}) if not bot else None,headers))
    def submit(self,rid='submit',**kw):
        body={'request_type':'reimbursement','description':'Supplies','amount':'12.34','payout_details':'card 123','telegram_file_id':'private-receipt',**kw}
        code,result=self.req('POST','/requests',body,'member',True,rid)
        self.assertEqual(code,201,result)
        return result
    def test_permissions_and_zero_balances(self):
        self.assertEqual(self.req('GET',role='member')[0],403)
        for role in ['admin','cashier','cashier2','председатель','заместитель председателя']:
            code,body=self.req('GET',role=role)
            self.assertEqual(code,200,body)
            self.assertEqual(body['balances'],{'cash':'0.00','card':'0.00'})
    def test_validation_and_manual_edit_recalculate(self):
        body={'direction':'income','account':'cash','amount':'10.00','description':'Opening','operation_at':'2026-01-31T22:00:00Z'}
        for amount in ['0','-1','NaN','1.234','10000000000',True]:
            self.assertEqual(self.req('POST','/entries',{**body,'amount':amount})[0],422)
        code,row=self.req('POST','/entries',body)
        self.assertEqual(code,201,row)
        self.assertEqual(self.req('PATCH',f"/entries/{row['id']}",{'amount':'25.50'})[0],200)
        data=self.req('GET')[1]
        self.assertEqual(data['balances']['cash'],'25.50')
        self.assertEqual(data['years'][0]['months'][0]['month'],2)
    def test_request_receipt_privacy_correction_and_atomic_decision(self):
        row=self.submit()
        self.assertEqual(self.req('POST','/requests',{'request_type':'reimbursement','description':'a','amount':'1','payout_details':'x'},'member',True,'no-photo')[0],422)
        code,listed=self.req('GET','/requests')
        self.assertEqual(code,200,listed)
        self.assertNotIn('private-receipt',str(listed))
        self.assertNotIn('telegram_file_id',str(listed))
        self.assertEqual(self.req('PATCH',f"/requests/{row['id']}",{'amount':'15.00'})[0],200)
        self.finish_notifications()
        # Old cashier buttons must be rejected after a correction.
        self.assertEqual(self.req('POST',f"/requests/{row['id']}/decision",{'decision':'approved','account':'card','expected_amount':'12.34'},'cashier',True,'stale')[0],409)
        def decide(role): return self.req('POST',f"/requests/{row['id']}/decision",{'decision':'approved','account':'card','expected_amount':'15.00'},role,True,'approve-'+role)
        with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(decide,['cashier','cashier2']))
        self.assertEqual(sorted(x[0] for x in results),[200,409],results)
        self.assertEqual(self.db.query(models.BudgetEntry).count(),1)
        self.assertEqual(self.req('GET')[1]['balances']['card'],'-15.00')
        self.assertEqual(self.req('GET','/requests')[1],[])
    def test_replay_rejection_and_missing_cashiers(self):
        first=self.submit(); second=self.submit()
        self.assertEqual(first['id'],second['id'])
        code,result=self.req('POST',f"/requests/{first['id']}/decision",{'decision':'rejected'},'cashier',True,'reject')
        self.assertEqual(code,200,result)
        self.assertEqual(self.db.query(models.BudgetEntry).count(),0)
        for role in ['cashier','cashier2']:
            self.db.get(models.User,self.users[role]).is_active=False
        self.db.commit()
        code,result=self.req('POST','/requests',{'request_type':'add_funds','description':'Donation','amount':'5'},'member',True,'no-cashier')
        self.assertEqual(code,409,result)
        self.assertEqual(result['detail'],'Заявку пока не удалось отправить. Черновик сохранён; попробуйте позже.')

    def finish_notifications(self):
        for _ in range(10):
            events=self.service('/claim',{'limit':20})[1]
            if not events: return
            for event in events:
                self.service(f"/{event['id']}/ack",{'lease_token':event['lease_token'],'outcome':'delivered'})

    def service(self,path,body):
        return asyncio.run(asgi_request('POST','/api/telegram/budget/notifications'+path,body,extra_headers={'X-Telegram-Service-Key':'b'*40}))

    def test_delivery_roundtrip_two_cashiers_correction_and_button_removal(self):
        import sys, tempfile
        from pathlib import Path
        bot_path=Path(__file__).resolve().parents[2]/'tg-bot'
        if not bot_path.is_dir(): self.skipTest('Optional cross-repository bot integration requires sibling tg-bot')
        sys.path.insert(0,str(bot_path))
        from worker import DeliveryWorker
        from store import Store
        owner=self
        class Backend:
            def request(self,method,path,body=None,**kw):
                if path=='bot/me':
                    role=list(owner.users)[kw['tid']-100]
                    return {'generation':role}
                code,result=owner.service(path.removeprefix('budget/notifications'),body)
                if code!=200: raise AssertionError((code,result))
                return result
        class Telegram:
            def __init__(self):self.messages={};self.calls=[];self.counter=0
            def call(self,method,payload):
                self.calls.append((method,payload));self.counter+=1
                mid=payload.get('message_id',self.counter)
                self.messages[(payload['chat_id'],mid)]=payload
                return {'message_id':mid}
        row=self.submit()
        with tempfile.TemporaryDirectory() as temp:
            store=Store(str(Path(temp)/'bot.db'));telegram=Telegram();worker=DeliveryWorker(Backend(),telegram,store)
            try:
                for _ in range(2):worker.budget_notifications()
                self.assertEqual(sum(method=='sendPhoto' for method,_ in telegram.calls),2)
                self.req('PATCH',f"/requests/{row['id']}",{'amount':'20'})
                for _ in range(2):worker.budget_notifications()
                for (chat,mid),payload in telegram.messages.items():
                    if payload.get('reply_markup',{}).get('inline_keyboard'):
                        self.assertIn('20.00',payload['text'])
                code,result=self.req('POST',f"/requests/{row['id']}/decision",{'decision':'approved','account':'cash','expected_amount':'20'},'cashier',True,'decision')
                self.assertEqual(code,200,result)
                for _ in range(4):worker.budget_notifications()
                for payload in telegram.messages.values():
                    self.assertFalse(payload.get('reply_markup',{}).get('inline_keyboard'))
                self.assertTrue(any(p['chat_id']==101 and '20.00' in p.get('text',p.get('caption','')) for _,p in telegram.calls))
                self.assertEqual(self.req('GET')[1]['balances']['cash'],'-20.00')
            finally:store.close()

    def test_failed_photo_retry_does_not_block_processed_keyboard_removal(self):
        row=self.submit()
        event=self.service('/claim',{'limit':1})[1][0]
        code,result=self.service(f"/{event['id']}/ack",{'lease_token':event['lease_token'],'outcome':'retry','detail_message_id':42,'retry_after':3600})
        self.assertEqual(code,200,result)
        code,result=self.req('POST',f"/requests/{row['id']}/decision",{'decision':'rejected'},'cashier',True,'reject')
        self.assertEqual(code,200,result)
        claimed=self.service('/claim',{'limit':20})[1]
        self.assertTrue(any(e['event_type']=='processed' and e['detail_message_id']==42 for e in claimed),claimed)

    def test_notification_lease_binding_and_permission_checks(self):
        self.submit()
        event=self.service('/claim',{'limit':1})[1][0]
        self.assertEqual(self.service(f"/{event['id']}/ack",{'lease_token':'wrong','outcome':'delivered'})[0],409)
        # Revoke the other cashier before its queued event is claimed.
        user=self.db.get(models.User,self.users['cashier2']);user.position_ref.is_kaznachey=False;self.db.commit()
        self.assertEqual(self.service('/claim',{'limit':20})[1],[])

    def test_approval_waits_until_all_cashier_corrections_are_delivered(self):
        row=self.submit()
        self.req('PATCH',f"/requests/{row['id']}",{'amount':'20'})
        code,result=self.req('POST',f"/requests/{row['id']}/decision",{'decision':'approved','account':'cash','expected_amount':'20'},'cashier',True,'early')
        self.assertEqual(code,409,result)
        self.assertEqual(self.db.query(models.BudgetEntry).count(),0)

    def test_delivery_progress_is_durable_and_blocked_cashier_is_not_available(self):
        self.submit()
        event=self.service('/claim',{'limit':1})[1][0]
        code,result=self.service(f"/{event['id']}/ack",{'lease_token':event['lease_token'],'outcome':'progress','detail_message_id':42})
        self.assertEqual(code,200,result)
        self.db.expire_all()
        self.assertEqual(self.db.get(models.BudgetNotification,event['id']).state,'leased')
        self.assertEqual(self.db.query(models.BudgetNotificationDelivery).one().detail_message_id,42)
        self.assertEqual(self.service(f"/{event['id']}/ack",{'lease_token':event['lease_token'],'outcome':'blocked'})[0],200)
        binding=self.db.query(models.TelegramBinding).filter_by(telegram_user_id=event['telegram_user_id']).one()
        self.db.refresh(binding);self.assertTrue(binding.blocked)

    def test_pre_send_validation_revokes_sensitive_payload_on_role_loss(self):
        self.submit();event=self.service('/claim',{'limit':1})[1][0]
        user=self.db.get(models.User,self.users['cashier']);user.position_ref.is_kaznachey=False;self.db.commit()
        code,result=self.service(f"/{event['id']}/validate",{'lease_token':event['lease_token']})
        self.assertEqual(code,200,result);self.assertFalse(result['valid']);self.assertNotIn('request',result)

    def test_income_approval_and_manual_edit_leave_processed_request_immutable(self):
        row=self.submit(request_type='add_funds',payout_details=None,telegram_file_id=None)
        self.finish_notifications()
        code,result=self.req('POST',f"/requests/{row['id']}/decision",{'decision':'approved','account':'card','expected_amount':'12.34'},'cashier',True,'income')
        self.assertEqual(code,200,result)
        ledger=self.req('GET')[1];entry=ledger['years'][0]['months'][0]['entries'][0]
        self.assertEqual(entry['direction'],'income');self.assertEqual(ledger['balances']['card'],'12.34')
        self.assertEqual(self.req('PATCH',f"/entries/{entry['id']}",{'account':'cash','amount':'20'})[0],200)
        self.assertEqual(self.req('GET')[1]['balances'],{'cash':'20.00','card':'0.00'})
        self.assertEqual(self.req('PATCH',f"/requests/{row['id']}",{'amount':'25'})[0],409)
        self.db.expire_all();self.assertEqual(str(self.db.get(models.BudgetRequest,row['id']).amount),'12.34')
        self.assertEqual(self.req('GET',role='secretary')[0],403)

    def test_upgrade_adds_cashier_flag_without_enabling_existing_positions(self):
        import tempfile
        from pathlib import Path
        from sqlalchemy import create_engine
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temp:
            engine=create_engine('sqlite:///'+str(Path(temp)/'old.db').replace('\\','/'))
            try:
                with engine.begin() as conn:
                    conn.exec_driver_sql('CREATE TABLE users (id INTEGER PRIMARY KEY)')
                    conn.exec_driver_sql('CREATE TABLE positions (id INTEGER PRIMARY KEY, name TEXT, can_comment_cards BOOLEAN)')
                    conn.exec_driver_sql("INSERT INTO positions VALUES (1,'admin',1)")
                    conn.exec_driver_sql('CREATE TABLE budget_entries (id INTEGER PRIMARY KEY)')
                with patch.object(database,'engine',engine):
                    database.ensure_schema_upgrades();database.ensure_schema_upgrades()
                with engine.connect() as conn:
                    self.assertEqual(conn.exec_driver_sql('SELECT name,is_kaznachey FROM positions').fetchall(),[('admin',0)])
                    self.assertTrue({'deleted_at','deleted_by'} <= {row[1] for row in conn.exec_driver_sql('PRAGMA table_info(budget_entries)')})
            finally:engine.dispose()

    def test_delete_operation_recalculates_and_keeps_request_history(self):
        row=self.submit();self.finish_notifications()
        code,result=self.req('POST',f"/requests/{row['id']}/decision",{'decision':'approved','account':'card','expected_amount':'12.34'},'cashier',True,'delete-source')
        self.assertEqual(code,200,result)
        entry=self.req('GET')[1]['years'][0]['months'][0]['entries'][0]
        self.assertEqual(self.req('DELETE',f"/entries/{entry['id']}",{'expected_updated_at':entry['updated_at']},role='member')[0],403)
        self.assertEqual(self.req('DELETE',f"/entries/{entry['id']}",{'expected_updated_at':entry['updated_at']},role='cashier')[0],200)
        self.assertEqual(self.req('GET')[1],{'can_import':True,'balances':{'cash':'0.00','card':'0.00'},'years':[]})
        self.assertEqual(self.req('DELETE',f"/entries/{entry['id']}",{'expected_updated_at':entry['updated_at']})[0],200)
        self.assertEqual(self.req('PATCH',f"/entries/{entry['id']}",{'amount':'25'})[0],404)
        self.db.expire_all();stored=self.db.get(models.BudgetEntry,entry['id'])
        self.assertIsNotNone(stored.deleted_at);self.assertEqual(stored.deleted_by,self.users['cashier'])
        self.assertEqual(self.db.get(models.BudgetRequest,row['id']).status,'approved')
        self.assertEqual(self.req('POST',f"/requests/{row['id']}/decision",{'decision':'approved','account':'cash','expected_amount':'12.34'},'cashier2',True,'no-reapprove')[0],409)

    def test_finance_responses_identify_creators_and_applicants_without_receipts(self):
        code,entry=self.req('POST','/entries',{'direction':'income','account':'cash','amount':'10','description':'Opening','operation_at':'2026-11-23T00:00:00Z'})
        self.assertEqual(code,201,entry);self.assertEqual(entry['created_by_login'],'admin');self.assertEqual(entry['created_by_name'],'admin Test')
        row=self.submit();pending=self.req('GET','/requests')[1][0]
        self.assertEqual(pending['applicant_login'],'member');self.assertEqual(pending['applicant_id'],self.users['member'])
        self.finish_notifications()
        self.req('POST',f"/requests/{row['id']}/decision",{'decision':'approved','account':'cash','expected_amount':'12.34'},'cashier',True,'author')
        entries=self.req('GET')[1]['years'][0]['months'][0]['entries']
        linked=next(e for e in entries if e['request_id'])
        self.assertEqual(linked['created_by_login'],'cashier');self.assertEqual(linked['applicant_login'],'member')
        self.assertEqual(linked['applicant_name'],'member Test');self.assertNotIn('private-receipt',str(entries))

    def test_delete_rejects_operation_changed_after_confirmation_was_opened(self):
        body={'direction':'income','account':'cash','amount':'10','description':'Opening','operation_at':'2026-11-23T00:00:00Z'}
        code,row=self.req('POST','/entries',body);self.assertEqual(code,201,row)
        self.req('PATCH',f"/entries/{row['id']}",{'amount':'30','account':'card'})
        code,result=self.req('DELETE',f"/entries/{row['id']}",{'expected_updated_at':row['updated_at']})
        self.assertEqual(code,409,result)
        self.assertEqual(self.req('GET')[1]['balances'],{'cash':'0.00','card':'30.00'})

    def test_result_animation_progress_is_saved_on_its_own_notification(self):
        import json
        row=self.submit();self.finish_notifications()
        self.req('POST',f"/requests/{row['id']}/decision",{'decision':'approved','account':'cash','expected_amount':'12.34'},'cashier',True,'result-progress')
        events=self.service('/claim',{'limit':20})[1];event=next(e for e in events if e['event_type']=='outcome')
        code,result=self.service(f"/{event['id']}/ack",{'lease_token':event['lease_token'],'outcome':'progress','outcome_message_id':77})
        self.assertEqual(code,200,result)
        self.db.expire_all();saved=self.db.get(models.BudgetNotification,event['id'])
        self.assertEqual(json.loads(saved.payload)['outcome_message_id'],77)
        self.service(f"/{event['id']}/ack",{'lease_token':event['lease_token'],'outcome':'retry','retry_after':1})
        self.db.expire_all();saved=self.db.get(models.BudgetNotification,event['id'])
        from datetime import datetime,timedelta
        saved.next_attempt_at=datetime.utcnow()-timedelta(seconds=1);self.db.commit()
        retry=next(e for e in self.service('/claim',{'limit':20})[1] if e['event_type']=='outcome')
        self.assertEqual(retry['outcome_message_id'],77)

    def test_ledger_author_details_do_not_query_each_request_separately(self):
        from sqlalchemy import event
        for i in range(15):
            request=models.BudgetRequest(request_type='add_funds',applicant_id=self.users['member'],applicant_name='Member',department_snapshot='[]',description='Donation',submitted_amount=1,amount=1,status='approved')
            self.db.add(request);self.db.flush()
            self.db.add(models.BudgetEntry(direction='income',account='cash',amount=1,description='Donation',created_by=self.users['cashier'],updated_by=self.users['admin'],request_id=request.id))
        self.db.commit();queries=[]
        def collect(conn,cursor,statement,parameters,context,executemany):
            if statement.lstrip().upper().startswith('SELECT'):queries.append(statement)
        event.listen(database.engine,'before_cursor_execute',collect)
        try:
            code,result=self.req('GET');self.assertEqual(code,200,result)
            self.assertLess(len(queries),10,len(queries))
        finally:event.remove(database.engine,'before_cursor_execute',collect)

    def test_monthly_income_expense_net_and_admin_import_capability(self):
        for direction,amount in [('income','100.10'),('income','0.20'),('expense','12.34')]:
            code,row=self.req('POST','/entries',{'direction':direction,'account':'cash','amount':amount,'description':'Totals','operation_at':'2026-11-01T00:00:00+03:00'})
            self.assertEqual(code,201,row)
        data=self.req('GET')[1];month=data['years'][0]['months'][0]
        self.assertEqual((month['income_total'],month['expense_total'],month['net_total']),('100.30','12.34','87.96'))
        self.assertTrue(data['can_import']);self.assertFalse(self.req('GET',role='cashier')[1]['can_import'])
        self.req('DELETE',f"/entries/{row['id']}",{'expected_updated_at':row['updated_at']})
        month=self.req('GET')[1]['years'][0]['months'][0]
        self.assertEqual(month['expense_total'],'0.00');self.assertEqual(month['net_total'],'100.30')
