import unittest
import test_budget as fixture
import models

class CustodyTests(unittest.TestCase):
    setUp=fixture.BudgetTests.setUp
    tearDown=fixture.BudgetTests.tearDown
    req=fixture.BudgetTests.req
    def entry(self,role='cashier',**values):
        code,row=self.req('POST','/entries',{'direction':'income','account':'cash','amount':'100','description':'Cash','operation_at':'2026-11-23T10:00:00Z',**values},role)
        self.assertEqual(code,201,row)
        return row
    def balance(self,role='cashier'):
        code,rows=self.req('GET','/treasurers');self.assertEqual(code,200,rows)
        return next(row for row in rows if row['user_id']==self.users[role])['balances']
    def test_default_owner_reassignment_null_and_edits_keep_author(self):
        row=self.entry();self.assertEqual(row['treasurer_id'],self.users['cashier'])
        self.assertEqual(self.balance()['cash'],'100.00')
        code,row=self.req('PATCH',f"/entries/{row['id']}",{'treasurer_id':self.users['cashier2'],'amount':'80'})
        self.assertEqual(code,200,row);self.assertEqual(row['created_by'],self.users['cashier'])
        self.assertEqual(self.balance()['cash'],'0.00');self.assertEqual(self.balance('cashier2')['cash'],'80.00')
        code,row=self.req('PATCH',f"/entries/{row['id']}",{'treasurer_id':None});self.assertEqual(code,200,row)
        self.assertEqual(self.balance('cashier2')['cash'],'0.00');self.assertEqual(self.req('GET')[1]['balances']['cash'],'80.00')
        code,row=self.req('PATCH',f"/entries/{row['id']}",{'amount':None});self.assertEqual(code,422,row)
    def test_manual_balance_then_operations_and_delete(self):
        row=self.entry();uid=self.users['cashier'];path=f'/treasurers/{uid}/balance'
        code,data=self.req('PUT',path,{'account':'cash','amount':'250','expected_balance':'100'},'cashier');self.assertEqual(code,200,data)
        self.assertEqual(self.balance()['cash'],'250.00');self.assertEqual(self.balance()['card'],'0.00')
        second=self.entry(amount='12.34',direction='expense',account='card')
        self.assertEqual(self.balance()['card'],'-12.34')
        code,data=self.req('PUT',path,{'account':'cash','amount':'500','expected_balance':'100'});self.assertEqual(code,409,data)
        self.req('DELETE',f"/entries/{row['id']}",{'expected_updated_at':row['updated_at']})
        self.assertEqual(self.balance()['cash'],'150.00')
        self.assertEqual(self.req('GET')[1]['balances']['cash'],'0.00')
        self.assertEqual(self.db.query(models.BudgetCustodyAdjustment).count(),1)
    def test_permissions_and_invalid_owner(self):
        row=self.entry(treasurer_id=None);self.assertIsNone(row['treasurer_id'])
        self.assertEqual(self.req('GET','/treasurers',role='member')[0],403)
        self.assertEqual(self.req('PUT',f"/treasurers/{self.users['cashier2']}/balance",{'account':'cash','amount':'1','expected_balance':'0'},'cashier')[0],403)
        self.assertEqual(self.req('PATCH',f"/entries/{row['id']}",{'treasurer_id':self.users['member']})[0],422)
        self.assertEqual(self.req('PATCH',f"/entries/{row['id']}",{'treasurer_id':999999})[0],422)
    def test_processed_request_uses_processor_not_applicant(self):
        request=fixture.BudgetTests.submit(self)
        fixture.BudgetTests.finish_notifications(self)
        code,data=self.req('POST',f"/requests/{request['id']}/decision",{'decision':'approved','account':'card','expected_amount':'12.34'},'cashier',True,'decide')
        self.assertEqual(code,200,data);self.assertEqual(self.balance()['card'],'-12.34')
    submit=fixture.BudgetTests.submit
    service=fixture.BudgetTests.service
    finish_notifications=fixture.BudgetTests.finish_notifications

    def test_upgrade_is_atomic_and_does_not_repopulate_explicit_null(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from sqlalchemy import create_engine,event
        import database
        with tempfile.TemporaryDirectory() as temp:
            engine=create_engine('sqlite:///'+str(Path(temp)/'old.db').replace('\\','/'))
            try:
                with engine.begin() as conn:
                    conn.exec_driver_sql('CREATE TABLE users (id INTEGER PRIMARY KEY)')
                    conn.exec_driver_sql('CREATE TABLE positions (id INTEGER PRIMARY KEY, name TEXT, can_comment_cards BOOLEAN)')
                    conn.exec_driver_sql('CREATE TABLE budget_entries (id INTEGER PRIMARY KEY, created_by INTEGER)')
                    conn.exec_driver_sql('INSERT INTO budget_entries VALUES (1,42)')
                def fail(conn,cursor,statement,params,context,many):
                    if statement.startswith('UPDATE budget_entries SET treasurer_id'):raise RuntimeError('interrupted')
                event.listen(engine,'before_cursor_execute',fail)
                with patch.object(database,'engine',engine):
                    with self.assertRaises(RuntimeError):database.ensure_schema_upgrades()
                event.remove(engine,'before_cursor_execute',fail)
                with engine.connect() as conn:
                    self.assertNotIn('treasurer_id',{r[1] for r in conn.exec_driver_sql('PRAGMA table_info(budget_entries)')})
                with patch.object(database,'engine',engine):database.ensure_schema_upgrades()
                with engine.begin() as conn:
                    self.assertEqual(conn.exec_driver_sql('SELECT treasurer_id FROM budget_entries').scalar(),42)
                    conn.exec_driver_sql('UPDATE budget_entries SET treasurer_id=NULL')
                with patch.object(database,'engine',engine):database.ensure_schema_upgrades()
                with engine.connect() as conn:self.assertIsNone(conn.exec_driver_sql('SELECT treasurer_id FROM budget_entries').scalar())
            finally:engine.dispose()
