import json
import unittest
import test_tasks


class ProfileTaskImprovements(unittest.TestCase):
    tearDownClass = classmethod(lambda cls: __import__('database').engine.dispose())
    setUp = test_tasks.TaskTests.setUp
    tearDown = test_tasks.TaskTests.tearDown
    request = test_tasks.TaskTests.request
    create = test_tasks.TaskTests.create
    def test_self_edit_and_protected_fields(self):
        for role in self.tokens:
            user = self.db.get(__import__('models').User, self.users[role])
            path = '/api/user/change/' + user.login
            allowed = role != 'Участник'
            self.assertEqual(self.request('PUT', path, {'first_name': 'Новое имя'}, role)[0], 200 if allowed else 403)
            for field, value in [('position', 'admin'), ('department_ids', []), ('is_active', False), ('is_deactivated', True)]:
                self.assertEqual(self.request('PUT', path, {field: value}, role)[0], 403)

    def test_preference_is_independent_and_available_to_participants(self):
        path = '/api/user/me/preferences'
        self.assertEqual(self.request('PUT', path, {'raccoon_enabled': False})[0], 200)
        self.assertFalse(self.request('GET', '/api/user/me')[1]['raccoon_enabled'])
        self.assertTrue(self.request('GET', '/api/user/me', role='admin')[1]['raccoon_enabled'])

    def test_bulk_archive_restore_history_and_conflict(self):
        a = self.create(completed=True)
        b = self.create(completed=True)
        payload = {'tasks': [{'id': t['id'], 'version': t['version']} for t in [a, b]]}
        path = '/api/tasks/archive-completed'
        self.assertEqual(self.request('POST', path, payload)[0], 403)
        self.request('PATCH', f"/api/tasks/{b['id']}", {'version': b['version'], 'description': 'changed'})
        self.assertEqual(self.request('POST', path, payload, 'admin')[0], 409)
        self.assertFalse(self.request('GET', f"/api/tasks/{a['id']}")[1]['archived'])
        payload['tasks'][1]['version'] += 1
        status, result = self.request('POST', path, payload, 'admin')
        self.assertEqual(status, 200, result)
        self.assertEqual(result['count'], 2)
        archived = self.request('GET', f"/api/tasks/{a['id']}")[1]
        restore = f"/api/tasks/{a['id']}/restore"
        self.assertEqual(self.request('POST', restore, {'version': archived['version']})[0], 403)
        self.assertEqual(self.request('POST', restore, {'version': a['version']}, 'admin')[0], 409)
        status, restored = self.request('POST', restore, {'version': archived['version']}, 'admin')
        self.assertEqual(status, 200, restored)
        self.assertFalse(restored['archived'])
        self.assertTrue(restored['completed'])
        self.assertEqual(self.request('GET', f"/api/tasks/{a['id']}/history")[1][0]['action'], 'restore')

    def test_bulk_archive_rejects_incomplete_and_hidden_tasks_atomically(self):
        a = self.create(completed=True)
        b = self.create()
        payload = {'tasks': [{'id': t['id'], 'version': t['version']} for t in [a,b]]}
        self.assertEqual(self.request('POST', '/api/tasks/archive-completed', payload, 'admin')[0], 409)
        self.assertFalse(self.request('GET', f"/api/tasks/{a['id']}")[1]['archived'])

    def test_delete_department_preserves_people(self):
        import models
        dept=models.Department(name='Отдел')
        self.db.add(dept); self.db.flush()
        self.db.add(models.UserDepartment(user_id=self.users['Участник'],department_id=dept.id));self.db.commit()
        dept_id = dept.id
        status, result=self.request('DELETE', f'/api/departments/{dept_id}', role='admin')
        self.assertEqual(status,204,result)
        self.assertIsNotNone(self.db.get(models.User,self.users['Участник']))
        self.db.expire_all()
        self.assertEqual(self.db.query(models.UserDepartment).filter_by(department_id=dept_id).count(),0)

    def test_report_docx_contents_and_completion_history(self):
        import models, main, asyncio
        from datetime import datetime
        from io import BytesIO
        from zipfile import ZipFile
        from xml.etree import ElementTree as ET
        dept=models.Department(name='IT')
        self.db.add(dept);self.db.flush()
        for role in ['Участник','Другой участник']:
            self.db.add(models.UserDepartment(user_id=self.users[role],department_id=dept.id))
        self.db.add(models.MemberWarning(user_id=self.users['Участник'],reason='Причина секретная',issued_at=datetime(2026,11,1),issued_by_login='admin',issued_by_name='Админ'))
        self.db.commit()
        task=self.create(description='Описание <работы> & проверка',deadline='2026-11-01',assignee_ids=[self.users['Участник']],department_card_ids=[self.cards['IT']],event_card_id=self.cards['Посвящение'])
        self.request('PATCH',f"/api/tasks/{task['id']}",{'version':task['version'],'completed':True})
        self.db.query(models.TaskHistory).filter_by(task_id=task['id'],action='update').update({'created_at':datetime(2026,11,2,10)})
        self.db.commit()
        self.db.get(models.Card,self.cards['IT']).title = 'Карточка IT'
        self.db.commit()
        from task_reports import build_department_report
        content=build_department_report(self.db,self.db.get(models.Card,self.cards['IT']),dept,self.db.get(models.Card,self.cards['Посвящение']),[self.db.get(models.Task,task['id'])])
        with ZipFile(BytesIO(content)) as z:
            self.assertIn('[Content_Types].xml',z.namelist())
            root=ET.fromstring(z.read('word/document.xml'))
            ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
            text=' '.join(root.itertext())
            for value in ['Отчёт','Карточка отдела: Карточка IT','IT','Посвящение','Краткое описание','Какие ошибки','Итог','Описание <работы> & проверка','02.11.2026','Просрочен','Другой участник','Нет тасок','Да']:
                self.assertIn(value,text)
            self.assertNotIn('Причина секретная',text)
            self.assertEqual(len(root.findall('.//w:tr',ns)),3)
        url=f"/api/cards/{self.cards['IT']}/task-report?department_id={dept.id}&event_card_id={self.cards['Посвящение']}"
        self.assertEqual(self.request('GET',url)[0],403)
        async def download():
            messages=[]
            path,query=url.split('?')
            async def receive(): return {'type':'http.request','body':b'','more_body':False}
            async def send(message):messages.append(message)
            await main.app({'type':'http','asgi':{'version':'3.0'},'http_version':'1.1','method':'GET','scheme':'http','path':path,'raw_path':path.encode(),'query_string':query.encode(),'root_path':'','headers':[(b'authorization',('Bearer '+self.tokens['Председатель']).encode())],'client':('127.0.0.1',1),'server':('testserver',80)},receive,send)
            return messages
        result=asyncio.run(download())
        self.assertEqual(result[0]['status'],200)
        self.assertIn(b'application/vnd.openxmlformats-officedocument.wordprocessingml.document',dict(result[0]['headers'])[b'content-type'])
        with ZipFile(BytesIO(b''.join(m.get('body',b'') for m in result))):pass

    def test_report_rejects_wrong_category_and_missing_department(self):
        url=f"/api/cards/{self.cards['Посвящение']}/task-report?department_id=999"
        self.assertEqual(self.request('GET',url,role='Председатель')[0],422)
        url=f"/api/cards/{self.cards['IT']}/task-report?department_id=999"
        self.assertEqual(self.request('GET',url,role='Председатель')[0],404)

    def test_optional_self_fields_can_be_cleared(self):
        user=self.db.get(__import__('models').User,self.users['Председатель'])
        path='/api/user/change/'+user.login
        fields={'middle_name':'Отчество','group':'10','course':'2','telegram':'@before','birth_date':'2000-01-01T00:00:00'}
        self.assertEqual(self.request('PUT',path,fields,'Председатель')[0],200)
        status,result=self.request('PUT',path,dict.fromkeys(fields),'Председатель')
        self.assertEqual(status,200,result)
        for field in fields:self.assertIsNone(result[field])

    def test_department_deletion_serializes_with_registration(self):
        import main, models, threading
        from concurrent.futures import ThreadPoolExecutor, TimeoutError
        from unittest.mock import patch
        department=models.Department(name='Гонка отдела')
        self.db.add(department)
        admin=self.db.get(models.User,self.users['admin'])
        admin.position_ref.can_register_users=True
        admin.position_ref.hierarchy_level=1
        self.db.commit()
        dept_id=department.id
        entered,release=threading.Event(),threading.Event()
        def paused_hash(password):
            entered.set()
            if not release.wait(5):raise RuntimeError('registration was not released')
            return 'test-only-password'
        payload={'login':'new-member','password':'password123','first_name':'Имя','last_name':'Фамилия','position':'Участник','department_ids':[dept_id]}
        with ThreadPoolExecutor(max_workers=2) as executor,patch.object(main,'hash_password',paused_hash):
            creating=executor.submit(self.request,'POST','/api/register',payload,'admin')
            if not entered.wait(5):
                release.set()
                self.fail(f'Registration did not reach password hashing: {creating.result()}')
            deleting=executor.submit(self.request,'DELETE',f'/api/departments/{dept_id}',None,'admin')
            serialized=False
            try:deleting.result(timeout=0.3)
            except TimeoutError:serialized=True
            finally:release.set()
            self.assertEqual(creating.result()[0],201)
            self.assertEqual(deleting.result()[0],204)
            self.assertTrue(serialized,'deletion must wait for membership validation and insertion')
        self.db.expire_all()
        self.assertEqual(self.db.query(models.UserDepartment).filter_by(department_id=dept_id).count(),0)
        self.assertEqual(self.request('GET','/api/users',role='admin')[0],200)

    def test_bulk_archive_loads_graphs_in_batches(self):
        import models, database
        from sqlalchemy import event
        rows=[models.Task(title=f'Массовая {i}',description='',creator_id=self.users['Участник'],completed=True,archived=False) for i in range(125)]
        self.db.add_all(rows);self.db.commit()
        body={'tasks':[{'id':t.id,'version':t.version} for t in rows]}
        selects=[]
        def count(conn,cursor,statement,parameters,context,many):
            if statement.lstrip().upper().startswith('SELECT'):selects.append(statement)
        event.listen(database.engine,'before_cursor_execute',count)
        try:status,result=self.request('POST','/api/tasks/archive-completed',body,'admin')
        finally:event.remove(database.engine,'before_cursor_execute',count)
        self.assertEqual(status,200,result)
        self.assertEqual(result['count'],125)
        self.assertLess(len(selects),20,'Task graph loading and notification binding lookup must be batched')
