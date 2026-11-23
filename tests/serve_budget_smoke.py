"""Isolated browser fixture. Never opens the existing app.db."""
import os, sys, tempfile
from pathlib import Path
from datetime import datetime
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='legacy-budget-browser-') as directory:
        os.environ['DATABASE_URL']='sqlite:///'+directory.replace('\\','/')+'/test.db'
        os.environ['SECRET_KEY']='isolated-budget-browser'
        os.environ['ALLOWED_ORIGINS']='http://127.0.0.1:4176'
        import main,models,database,uvicorn
        with database.SessionLocal() as db:
            password=main.hash_password('smoke-password');users={}
            for login in ['admin','member','cashier']:
                position=models.Position(name=login,is_kaznachey=login=='cashier',can_manage_positions=login=='admin')
                db.add(position);db.flush()
                user=models.User(login=login,first_name=login,last_name='Test',password_hash=password,position_id=position.id)
                db.add(user);db.flush();users[login]=user
            db.add(models.BudgetEntry(direction='income',account='cash',amount=300,description='Opening cash',operation_at=datetime(2025,12,1),created_by=users['admin'].id,updated_by=users['admin'].id))
            db.add(models.BudgetEntry(direction='expense',account='card',amount=50,description='Equipment',operation_at=datetime(2026,1,1),created_by=users['admin'].id,updated_by=users['admin'].id))
            db.add(models.BudgetRequest(request_type='reimbursement',applicant_id=users['member'].id,applicant_name='Иван Тестов',department_snapshot='["ИТ"]',description='Кабель',submitted_amount=25,amount=25,payout_details='card 123',telegram_file_id='private-browser-receipt'))
            db.commit()
        try:uvicorn.run(main.app,host='127.0.0.1',port=8016,log_level='warning')
        finally:database.engine.dispose()
