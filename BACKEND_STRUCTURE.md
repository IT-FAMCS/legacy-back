# Структура backend

```text
legacy-back/
├── main.py              # FastAPI-приложение, auth, пользователи, должности, темы, карточки, история
├── models.py            # SQLAlchemy-модели: User, Position, Department, Category, Card, Visit
├── schemas.py           # Pydantic-схемы запросов и ответов
├── database.py          # engine, SessionLocal, Base, get_db
├── seed_db_v2.py        # безопасное первичное наполнение справочников
├── requirements.txt     # зависимости Python
├── Dockerfile
├── docker-compose.yml
├── DEPLOY.md
└── DEPLOY_NOTES.md
```

## Основные изменения

- Удаление карточки контролируется backend-правами: admin, председатель, председатель студсовета, заместители председателя, а также роли с `can_delete_cards=True`.
- Удаление тем/категорий отключено на API: `DELETE /api/categories/{id}` возвращает 405 и не удаляет данные.
- История просмотров карточек и тем сохраняется с `user_id`; эндпоинты истории возвращают историю текущего пользователя, а чужую историю — только пользователям с правом `can_edit_any_user`.
- Добавлены эндпоинты деактивации и активации аккаунта:
  - `PUT /api/users/{login}/deactivate`
  - `PUT /api/users/{login}/activate`
- Вход заблокирован для `is_deactivated=True` или `is_active=False`.
- Руководитель отдела/направления и заместитель руководителя отдела могут редактировать карточки.
- При старте backend безопасно обновляет флаги стандартных должностей без очистки БД.

## Важно для деплоя

Не заменяй продовый `app.db` файлом из архива. Код использует `create_all`, а не `drop_all`, и не очищает существующие таблицы.
