import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from unittest.mock import patch
import test_budget as fixture
import models, database

SHEET_ID='test_sheet_1234567890'
CSV='Статья,Доход,Расход,,,,\n1. Организация,,,,,,\n1. Канцтовары,,"12,34",,,,\n2. Сбор,"1\u00a0234,50",,,,,\n,,,1234.50,12.34,,\nИТОГО,"1234,50","12,34",,,,\n'
BOOK=(SHEET_ID,[{'title':'MAIN','gid':'0','csv':'Остаток средств,500\n'}, {'title':'ноябрь26','gid':'12','csv':CSV}])

class BudgetImportTests(unittest.TestCase):
    setUp=fixture.BudgetTests.setUp
    tearDown=fixture.BudgetTests.tearDown
    req=fixture.BudgetTests.req
    def preview(self,role='admin',book=BOOK,**values):
        with patch('budget_import.fetch_spreadsheet',return_value=book):
            return self.req('POST','/imports/preview',{'url':'https://docs.google.com/spreadsheets/d/'+SHEET_ID+'/edit?gid=0','account':'card',**values},role)
    def test_month_sheets_skip_summary_subtotals_and_parse_local_money(self):
        code,data=self.preview();self.assertEqual(code,200,data)
        self.assertEqual(data['new_count'],2);self.assertEqual(data['income_total'],'1234.50');self.assertEqual(data['expense_total'],'12.34')
        self.assertEqual(self.db.query(models.BudgetEntry).count(),0)
        self.assertEqual(data['rows'][0]['description'],'1. Канцтовары')
        self.assertTrue(data['rows'][0]['approximate_date'])
        self.assertEqual(data['rows'][0]['operation_at'],'2026-10-31T21:00:00')
        self.assertIn('MAIN',data['skipped_sheets'])
    def test_only_admin_may_preview_or_commit_and_forbidden_never_fetches(self):
        for role in ['member','cashier','председатель','заместитель председателя','secretary']:
            with patch('budget_import.fetch_spreadsheet') as fetch:
                self.assertEqual(self.preview(role=role)[0],403)
                fetch.assert_not_called()
        self.assertEqual(self.req('POST','/imports/unknown/commit',role='cashier')[0],403)
    def test_commit_replay_deduplicates_and_never_restores_deleted_entry(self):
        code,preview=self.preview();self.assertEqual(code,200,preview)
        path=f"/imports/{preview['id']}/commit"
        code,result=self.req('POST',path);self.assertEqual(code,200,result);self.assertEqual(result['created_count'],2)
        self.assertEqual(self.req('POST',path)[1],result)
        self.assertEqual(self.req('GET')[1]['balances']['card'],'1222.16')
        _,again=self.preview();self.assertEqual(again['new_count'],0);self.assertEqual(again['skipped_count'],2)
        self.assertEqual(self.req('POST',f"/imports/{again['id']}/commit")[1]['created_count'],0)
        entry=self.req('GET')[1]['years'][0]['months'][0]['entries'][0]
        self.req('DELETE',f"/entries/{entry['id']}",{'expected_updated_at':entry['updated_at']})
        self.assertEqual(self.preview()[1]['new_count'],0)
    def test_concurrent_previews_commit_each_source_once(self):
        first=self.preview()[1];second=self.preview()[1]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda p:self.req('POST',f"/imports/{p['id']}/commit"),[first,second]))
        self.assertTrue(all(code==200 for code,_ in results),results)
        self.assertEqual(sum(result['created_count'] for _,result in results),2)
        self.assertEqual(self.db.query(models.BudgetEntry).count(),2)
    def test_changed_source_row_conflicts_and_expired_preview_cannot_commit(self):
        preview=self.preview()[1];self.req('POST',f"/imports/{preview['id']}/commit")
        changed=(SHEET_ID,[{'title':'ноябрь26','gid':'12','csv':CSV.replace('12,34','15,00')}])
        code,data=self.preview(book=changed);self.assertEqual(code,200,data);self.assertGreater(data['conflict_count'],0)
        self.assertEqual(self.req('POST',f"/imports/{data['id']}/commit")[0],409)
        fresh=self.preview()[1]
        batch=self.db.get(models.BudgetImportBatch,fresh['id']);batch.expires_at=datetime.utcnow()-timedelta(seconds=1);self.db.commit()
        self.assertEqual(self.req('POST',f"/imports/{fresh['id']}/commit")[0],410)
    def test_invalid_amount_or_date_rejects_whole_preview(self):
        for csv in [CSV.replace('12,34','12,345'),CSV.replace('12,34','-12,34'),CSV.replace('12,34','50$'),'Статья,Доход,Расход,Дата\nКабель,,10,31.02.2026']:
            code,data=self.preview(book=(SHEET_ID,[{'title':'ноябрь26','gid':'12','csv':csv}]))
            self.assertEqual(code,422,data);self.assertEqual(self.db.query(models.BudgetEntry).count(),0)
    def test_explicit_dates_and_fallback_month(self):
        code,data=self.preview(book=(SHEET_ID,[{'title':'Операции','gid':'12','csv':'Дата,Статья,Доход,Расход\n23.11.2026,Кабель,,10'}]))
        self.assertEqual(code,200,data);self.assertEqual(data['rows'][0]['operation_at'],'2026-11-22T21:00:00');self.assertFalse(data['rows'][0]['approximate_date'])
        book=(SHEET_ID,[{'title':'Лист1','gid':'12','csv':'Статья,Доход,Расход\nВзнос,10,'}])
        self.assertEqual(self.preview(book=book)[0],422)
        self.assertEqual(self.preview(book=book,fallback_month='2025-06')[1]['rows'][0]['operation_at'],'2025-05-31T21:00:00')
    def test_url_validation_rejects_external_hosts_credentials_and_non_https(self):
        for url in ['http://docs.google.com/spreadsheets/d/'+SHEET_ID, 'https://example.com/'+SHEET_ID, 'https://docs.google.com@127.0.0.1/spreadsheets/d/'+SHEET_ID]:
            code,data=self.preview(url=url);self.assertEqual(code,422,data)

    def test_changed_direction_conflicts_instead_of_adding_another_operation(self):
        book=(SHEET_ID,[{'title':'ноябрь26','gid':'12','csv':'Статья,Доход,Расход\nПокупка,,10'}])
        first=self.preview(book=book)[1]
        self.assertEqual(self.req('POST',f"/imports/{first['id']}/commit")[0],200)
        changed=(SHEET_ID,[{'title':'ноябрь26','gid':'12','csv':'Статья,Доход,Расход\nПокупка,10,'}])
        code,data=self.preview(book=changed);self.assertEqual(code,200,data)
        self.assertEqual(data['conflict_count'],1)
        self.assertEqual(self.req('POST',f"/imports/{data['id']}/commit")[0],409)
        self.assertEqual(self.db.query(models.BudgetEntry).count(),1)
    def test_ambiguous_income_and_expense_rejects_preview(self):
        book=(SHEET_ID,[{'title':'ноябрь26','gid':'12','csv':'Статья,Доход,Расход\nПокупка,10,20'}])
        code,data=self.preview(book=book);self.assertEqual(code,422,data)

    def test_new_target_does_not_conflict_with_existing_source(self):
        first=self.preview()[1];self.req('POST',f"/imports/{first['id']}/commit")
        code,data=self.preview(account='cash',treasurer_id=self.users['cashier'])
        self.assertEqual(code,200,data);self.assertEqual(data['conflict_count'],0);self.assertEqual(data['skipped_count'],2)
    def test_missing_description_rejects_money_row(self):
        book=(SHEET_ID,[{'title':'ноябрь26','gid':'12','csv':'Статья,Доход,Расход\n,10,\nПокупка,,20'}])
        code,data=self.preview(book=book);self.assertEqual(code,422,data)
