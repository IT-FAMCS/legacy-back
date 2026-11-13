"""Temporary task demo fixture. Never reads or changes app.db."""
import os
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='legacy-tasks-browser-') as directory:
        os.environ['DATABASE_URL'] = 'sqlite:///' + directory.replace('\\', '/') + '/test.db'
        os.environ['SECRET_KEY'] = 'tasks-browser-only'
        import main
        import models
        import database
        import uvicorn
        from tasks import TaskCreate
        with database.SessionLocal() as db:
            password = main.hash_password('smoke-password')
            for login, title, first in [('member', 'Участник', 'Анна'), ('other', 'Другой участник', 'Иван'), ('deputy', 'Заместитель председателя', 'Мария')]:
                position = models.Position(name=title, can_edit_cards=True, can_delete_cards=True)
                db.add(position)
                db.flush()
                db.add(models.User(login=login, first_name=first, last_name='Тестовая', password_hash=password, position_id=position.id))
            for name, titles in [('Отделы и направления', ['Информационный отдел', 'Культурный отдел']), ('Мероприятия', ['Посвящение первокурсников', 'День факультета']), ('Общая информация', ['Правила'])]:
                category = models.Category(name=name)
                db.add(category)
                db.flush()
                for title in titles:
                    db.add(models.Card(title=title, content='Описание карточки для проверки таск.', category_id=category.id))
            db.commit()
            endpoint = next(route.endpoint for route in main.app.routes if getattr(route, 'path', None) == '/api/tasks' and 'POST' in route.methods)
            member = db.query(models.User).filter_by(login='member').one()
            samples = [
                dict(title='Подготовить афишу посвящения', description='Согласовать макет и передать в печать.', event_card_id=3, department_card_ids=[1,2], assignee_ids=[1,2], deadline='2026-11-20'),
                dict(title='Собрать программу вечера', description='Добавить выступления и расписание репетиций.', event_card_id=3, department_card_ids=[2], assignee_ids=[2], deadline='2026-11-10'),
                dict(title='Обновить список контактов', description='Проверить контакты партнёров отдела.', department_card_ids=[1], assignee_ids=[1]),
                dict(title='Забронировать аудиторию', event_card_id=4, department_card_ids=[2], assignee_ids=[3], deadline='2026-11-26'),
                dict(title='Подготовить отчёт за месяц', description='Общая таска организации.', assignee_ids=[1], completed=True),
            ]
            for sample in samples:
                endpoint(data=TaskCreate(**sample), user=member, db=db)
        try:
            uvicorn.run(main.app, host='127.0.0.1', port=8013, log_level='warning')
        finally:
            database.engine.dispose()
