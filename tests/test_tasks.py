import asyncio
import unittest
from test_trainee_access import asgi_request
import main
import models
import database


class TaskTests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        database.engine.dispose()

    def setUp(self):
        models.Base.metadata.drop_all(database.engine)
        models.Base.metadata.create_all(database.engine)
        self.db = database.SessionLocal()
        self.tokens = {}
        self.users = {}
        for name in ['Участник', 'Другой участник', 'Председатель', 'Заместитель председателя по IT', 'admin']:
            pos = models.Position(name=name, can_edit_cards=True, can_delete_cards=True)
            self.db.add(pos)
            self.db.flush()
            user = models.User(login='u' + str(pos.id), first_name='Имя', last_name=name,
                               password_hash='unused', position_id=pos.id)
            self.db.add(user)
            self.db.flush()
            self.users[name] = user.id
            self.tokens[name] = main.create_access_token({'sub': user.login})
        self.cards = {}
        for category_name, titles in [('Отделы и направления', ['IT', 'Культура']),
                                     ('Мероприятия', ['Посвящение']), ('Общая информация', ['Правила'])]:
            category = models.Category(name=category_name)
            self.db.add(category)
            self.db.flush()
            for title in titles:
                card = models.Card(title=title, category_id=category.id)
                self.db.add(card)
                self.db.flush()
                self.cards[title] = card.id
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def request(self, method, path='/api/tasks', body=None, role='Участник'):
        return asyncio.run(asgi_request(method, path, body, self.tokens.get(role)))

    def create(self, **values):
        status, data = self.request('POST', body={'title': 'Подготовить план', **values})
        self.assertEqual(status, 201, data)
        return data

    def test_shared_record_edits_completion_and_history_by_any_member(self):
        task = self.create(event_card_id=self.cards['Посвящение'],
                           department_card_ids=[self.cards['IT'], self.cards['Культура']],
                           assignee_ids=[self.users['Участник']], deadline='2026-12-01')
        self.assertTrue(task['created_at'])
        status, tasks = self.request('GET')
        self.assertEqual(status, 200)
        self.assertEqual([t['id'] for t in tasks], [task['id']])
        status, changed = self.request('PATCH', f"/api/tasks/{task['id']}",
            {'title': 'Новый план', 'completed': True, 'version': task['version']}, role='Другой участник')
        self.assertEqual(status, 200, changed)
        self.assertTrue(changed['completed'])
        self.assertEqual(changed['department_card_ids'], task['department_card_ids'])
        status, history = self.request('GET', f"/api/tasks/{task['id']}/history")
        self.assertEqual(status, 200)
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]['after']['title'], 'Новый план')
        self.assertEqual(history[0]['before']['title'], 'Подготовить план')
        self.assertEqual(history[0]['actor_id'], self.users['Другой участник'])
        status, reopened = self.request('PATCH', f"/api/tasks/{task['id']}",
                                       {'completed': False, 'version': changed['version']})
        self.assertEqual(status, 200)
        self.assertFalse(reopened['completed'])

    def test_archive_only_chair_and_deputies_preserves_history(self):
        for role in self.tokens:
            task = self.create()
            status, result = self.request('DELETE', f"/api/tasks/{task['id']}",
                                         {'version': task['version']}, role=role)
            allowed = role in ['Председатель', 'Заместитель председателя по IT']
            self.assertEqual(status, 200 if allowed else 403, (role, result))
            if allowed:
                self.assertTrue(result['archived'])
                status, rows = self.request('GET')
                self.assertNotIn(task['id'], [r['id'] for r in rows])
                status, history = self.request('GET', f"/api/tasks/{task['id']}/history")
                self.assertEqual(status, 200)
                self.assertEqual(history[0]['action'], 'archive')
                status, _ = self.request('PATCH', f"/api/tasks/{task['id']}",
                                         {'title': 'Нельзя', 'version': result['version']})
                self.assertEqual(status, 409)

    def test_version_conflicts_do_not_overwrite_or_add_history(self):
        task = self.create()
        path = f"/api/tasks/{task['id']}"
        self.assertEqual(self.request('PATCH', path, {'description': 'first', 'version': 1})[0], 200)
        self.assertEqual(self.request('PATCH', path, {'description': 'lost', 'version': 1})[0], 409)
        self.assertEqual(len(self.request('GET', path + '/history')[1]), 2)

    def test_invalid_links_assignees_and_empty_titles_rejected(self):
        for values in [{'title': '  '}, {'event_card_id': self.cards['IT']},
                       {'department_card_ids': [self.cards['Правила']]},
                       {'department_card_ids': [999999]}, {'assignee_ids': [999999]},
                       {'deadline': 'bad'}, {'title': 'x' * 301}]:
            status, _ = self.request('POST', body={'title': 'Таска', **values})
            self.assertIn(status, [422, 404], values)
        task = self.create()
        for field in ['title', 'completed', 'department_card_ids', 'assignee_ids']:
            self.assertEqual(self.request('PATCH', f"/api/tasks/{task['id']}",
                                         {field: None, 'version': 1})[0], 422, field)

    def test_restricted_cards_and_old_history_do_not_leak(self):
        task = self.create(event_card_id=self.cards['Посвящение'])
        card = self.db.get(models.Card, self.cards['Посвящение'])
        card.access_logins = 'nobody'
        self.db.commit()
        self.assertEqual(self.request('GET')[1], [])
        path = f"/api/tasks/{task['id']}"
        self.assertEqual(self.request('GET', path)[0], 404)
        self.assertEqual(self.request('GET', path + '/history')[0], 404)
        self.assertEqual(self.request('PATCH', path, {'version': 1, 'completed': True})[0], 404)
        self.assertEqual(self.request('POST', body={'title': 'Нельзя', 'event_card_id': card.id})[0], 404)
        options = self.request('GET', '/api/tasks/options')[1]
        self.assertNotIn(card.id, [x['id'] for x in options['events']])

    def test_inactive_assignees_kept_but_new_assignments_rejected(self):
        uid = self.users['Другой участник']
        task = self.create(assignee_ids=[uid])
        self.db.get(models.User, uid).is_deactivated = True
        self.db.commit()
        options = self.request('GET', '/api/tasks/options')[1]
        self.assertNotIn(uid, [u['id'] for u in options['users']])
        self.assertEqual(self.request('PATCH', f"/api/tasks/{task['id']}",
                                     {'version': 1, 'title': 'Сохранено', 'assignee_ids': [uid]})[0], 200)
        self.assertEqual(self.request('POST', body={'title': 'Новая', 'assignee_ids': [uid]})[0], 422)

    def test_linked_card_deletion_and_category_move_blocked(self):
        task = self.create(department_card_ids=[self.cards['IT']])
        path = f"/api/cards/{self.cards['IT']}"
        self.assertEqual(self.request('DELETE', path)[0], 409)
        other_category = self.db.get(models.Card, self.cards['Правила']).category_id
        self.assertEqual(self.request('PUT', path, {'category_id': other_category})[0], 409)
        self.assertEqual(self.request('DELETE', f"/api/tasks/{task['id']}",
                                     {'version': 1}, role='Председатель')[0], 200)
        self.assertEqual(self.request('DELETE', path)[0], 409)

    def test_history_redacts_old_links_when_task_becomes_general(self):
        task = self.create(event_card_id=self.cards['Посвящение'])
        path = f"/api/tasks/{task['id']}"
        self.assertEqual(self.request('PATCH', path, {'version': 1, 'event_card_id': None})[0], 200)
        self.db.get(models.Card, self.cards['Посвящение']).access_logins = 'nobody'
        self.db.commit()
        self.assertEqual(self.request('GET', path)[0], 200)
        history = self.request('GET', path + '/history')[1]
        self.assertNotIn('Посвящение', str(history))

    def test_authentication_required(self):
        self.assertIn(self.request('GET', role=None)[0], [401, 403])


    def test_task_link_creation_serializes_with_card_deletion(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor, TimeoutError
        from unittest.mock import patch
        entered, release = threading.Event(), threading.Event()
        original = models.Task.__init__
        def paused(task, **kwargs):
            original(task, **kwargs)
            entered.set()
            release.wait(5)
        with ThreadPoolExecutor(max_workers=2) as executor, patch.object(models.Task, '__init__', paused):
            creating = executor.submit(self.request, 'POST', '/api/tasks',
                                       {'title': 'Гонка', 'department_card_ids': [self.cards['IT']]})
            self.assertTrue(entered.wait(5))
            deleting = executor.submit(self.request, 'DELETE', f"/api/cards/{self.cards['IT']}")
            try:
                deleting.result(timeout=0.3)
            except TimeoutError:
                pass
            finally:
                release.set()
            self.assertEqual(creating.result()[0], 201)
            self.assertEqual(deleting.result()[0], 409, 'Deleting a card must serialize with linking a task')
        self.db.expire_all()
        self.assertIsNotNone(self.db.get(models.Card, self.cards['IT']))

    def test_concurrent_task_edits_accept_only_one_version(self):
        from concurrent.futures import ThreadPoolExecutor
        task = self.create()
        path = f"/api/tasks/{task['id']}"
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda text: self.request('PATCH', path, {'title': text, 'version': 1}), ['Первый', 'Второй']))
        self.assertEqual(sorted(status for status, _ in results), [200, 409])
        self.assertEqual(len(self.request('GET', path + '/history')[1]), 2)

    def test_profile_tasks_filter_excludes_completed_archived_and_other_assignees(self):
        uid = self.users['Участник']
        active = self.create(assignee_ids=[uid])
        self.create(assignee_ids=[uid], completed=True)
        archived = self.create(assignee_ids=[uid])
        self.request('DELETE', f"/api/tasks/{archived['id']}", {'version': 1}, role='Председатель')
        self.create(assignee_ids=[self.users['Другой участник']])
        status, rows = self.request('GET', f'/api/tasks?assignee_id={uid}&active_only=true')
        self.assertEqual(status, 200)
        self.assertEqual([row['id'] for row in rows], [active['id']])
        self.create(assignee_ids=[uid], event_card_id=self.cards['Посвящение'])
        self.db.get(models.Card, self.cards['Посвящение']).access_logins = 'nobody'
        self.db.commit()
        self.create(assignee_ids=[uid], department_card_ids=[self.cards['IT']])
        status, rows = self.request('GET', f'/api/tasks?assignee_id={uid}&active_only=true')
        self.assertEqual(len(rows), 2)
