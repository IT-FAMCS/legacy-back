# Student Organization Management System

Система управления студенческой организацией с JWT аутентификацией, ролями и должностями.

---

## 🚀 Локальное развёртывание (пошаговая инструкция)

### Шаг 1: Установка Python

**macOS:**

```bash
# Через Homebrew
brew install python@3.9

# Или через pyenv (рекомендуется)
brew install pyenv
pyenv install 3.9.6
pyenv global 3.9.6
```

**Windows:**

1. Скачайте установщик с https://www.python.org/downloads/
2. Запустите установщик
3. ✅ Отметьте галочку "Add Python to PATH"
4. Нажмите "Install Now"

**Linux (Ubuntu/Debian):**

```bash
sudo apt update
sudo apt install python3 python3-pip
```

### Шаг 2: Проверка установки

```bash
python3 --version  # Должно быть 3.9 или выше
pip3 --version
```

### Шаг 3: Клонирование/копирование проекта

Перейдите в директорию проекта:

```bash
cd /path/to/legacy-back
```

### Шаг 4: Создание виртуального окружения (рекомендуется)

```bash
# Создание виртуального окружения
python3 -m venv venv

# Активация (macOS/Linux)
source venv/bin/activate

# Активация (Windows)
venv\Scripts\activate
```

### Шаг 5: Установка зависимостей

```bash
pip3 install -r requirements.txt
```

**Устанавливаются пакеты:**

- `fastapi` - веб-фреймворк
- `uvicorn` - ASGI сервер
- `sqlalchemy` - ORM для работы с БД
- `pydantic` - валидация данных
- `passlib[bcrypt]` - хеширование паролей
- `python-jose[cryptography]` - JWT токены

### Шаг 6: Инициализация базы данных

База данных создаётся автоматически при первом запуске приложения.

Для создания БД с начальными данными (должности, пользователи, категории, карточки):

```bash
python3 seed_db_v2.py
```

**Будут созданы:**

- 8 должностей (admin, председатель, секретарь, и т.д.)
- 9 пользователей с разными ролями
- 3 категории
- 7 карточек

### Шаг 7: Запуск сервера

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

**Параметры:**

- `--reload` - автоперезагрузка при изменении кода (для разработки)
- `--host 0.0.0.0` - доступ с любых сетевых интерфейсов
- `--port 8000` - порт

### Шаг 8: Проверка работы

Откройте в браузере:

- **API документация (Swagger UI):** http://127.0.0.1:8000/docs
- **ReDoc:** http://127.0.0.1:8000/redoc
- **Корневой endpoint:** http://127.0.0.1:8000/

### Шаг 9: Тестовый вход

```bash
curl -X POST "http://127.0.0.1:8000/api/login" \
  -H "Content-Type: application/json" \
  -d '{"login":"admin","password":"admin123"}'
```

---

## Структура проекта

```
legacy-back/
├── main.py          # FastAPI приложение с API endpoints
├── database.py      # Конфигурация базы данных
├── models.py        # SQLAlchemy модели (Position, User, Category, Card, Visits)
├── schemas.py       # Pydantic схемы для валидации
├── requirements.txt # Python зависимости
├── seed_db_v2.py    # Скрипт заполнения БД
└── README.md        # Документация
```

---

## Установка

```bash
pip3 install -r requirements.txt
```

## Запуск приложения

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

## Документация API

После запуска откройте:

- **Swagger UI**: http://127.0.0.1:8000/docs
- **ReDoc**: http://127.0.0.1:8000/redoc

---

## API Endpoints

### Должности (Position)

| Метод  | Endpoint              | Описание               | Доступ               |
| ------ | --------------------- | ---------------------- | -------------------- |
| GET    | `/api/positions`      | Список всех должностей | Все авторизованные   |
| POST   | `/api/positions`      | Создание должности     | can_manage_positions |
| GET    | `/api/positions/{id}` | Должность по ID        | Все авторизованные   |
| PUT    | `/api/positions/{id}` | Обновление должности   | can_manage_positions |
| DELETE | `/api/positions/{id}` | Удаление должности     | can_manage_positions |

### Аутентификация

| Метод | Endpoint      | Описание                           |
| ----- | ------------- | ---------------------------------- |
| POST  | `/api/login`  | Вход пользователя (логин + пароль) |
| POST  | `/api/logout` | Выход пользователя                 |

### Пользователи

| Метод | Endpoint           | Описание                                       | Доступ                                    |
| ----- | ------------------ | ---------------------------------------------- | ----------------------------------------- |
| POST  | `/api/register`    | Регистрация нового пользователя                | can_register_users                        |
| GET   | `/api/users`       | Список пользователей (с фильтрацией по отделу) | Все авторизованные                        |
| GET   | `/api/user/me`     | Информация о текущем пользователе              | Все авторизованные                        |
| PUT   | `/api/user/change` | Обновление данных **другого** пользователя     | can_edit_any_user или руководитель отдела |

### Категории

| Метод  | Endpoint               | Описание              | Доступ                |
| ------ | ---------------------- | --------------------- | --------------------- |
| POST   | `/api/categories`      | Создание категории    | can_edit_categories   |
| GET    | `/api/categories`      | Список всех категорий | Все авторизованные    |
| GET    | `/api/categories/{id}` | Категория по ID       | Все авторизованные    |
| PUT    | `/api/categories/{id}` | Обновление категории  | can_edit_categories   |
| DELETE | `/api/categories/{id}` | Удаление категории    | can_delete_categories |

### Карточки

| Метод  | Endpoint          | Описание                             | Доступ             |
| ------ | ----------------- | ------------------------------------ | ------------------ |
| POST   | `/api/cards`      | Создание карточки                    | can_edit_cards     |
| GET    | `/api/cards`      | Список доступных карточек            | Все авторизованные |
| GET    | `/api/cards/{id}` | Карточка по ID (с проверкой доступа) | Все авторизованные |
| PUT    | `/api/cards/{id}` | Обновление карточки                  | can_edit_cards     |
| DELETE | `/api/cards/{id}` | Удаление карточки                    | can_delete_cards   |

### История посещений

| Метод | Endpoint                 | Описание                    |
| ----- | ------------------------ | --------------------------- |
| GET   | `/api/visits/categories` | История посещений категорий |
| GET   | `/api/visits/cards`      | История посещений карточек  |

---

## Модель данных

### Должность (Position)

- **id**, **name** (уникальное название)
- **hierarchy_level** - уровень иерархии (1 = высший, 10 = низший)
- **can_register_users** - регистрация пользователей
- **can_edit_categories** - редактирование категорий
- **can_delete_categories** - удаление категорий
- **can_edit_cards** - редактирование карточек
- **can_delete_cards** - удаление карточек
- **can_edit_any_user** - редактирование любых пользователей
- **can_manage_positions** - управление должностями (создание/редактирование/удаление)
- **created_at**

### Пользователь (User)

- login, password_hash
- first_name, last_name, middle_name
- birth_date
- course, group
- **position_id** (ссылка на Position)
- department
- telegram
- is_active, is_deactivated
- last_login

### Категория (Category)

- name, description
- created_at

### Карточка (Card)

- title, content (markdown)
- category_id
- access_positions, access_logins (для ограничения доступа)
- created_at

---

## Должности по умолчанию

| Должность                       | can_manage_positions | can_register_users | can_edit_categories | can_delete_cards |
| ------------------------------- | -------------------- | ------------------ | ------------------- | ---------------- |
| admin                           | ✅                   | ✅                 | ✅                  | ✅               |
| председатель                    | ✅                   | ✅                 | ✅                  | ✅               |
| председатель студсовета         | ✅                   | ✅                 | ✅                  | ✅               |
| заместитель председателя        | ✅                   | ✅                 | ✅                  | ✅               |
| секретарь                       | ✅                   | ✅                 | ✅                  | ✅               |
| руководитель отдела/направления | ❌                   | ❌                 | ❌                  | ❌               |
| заместитель руководителя отдела | ❌                   | ❌                 | ❌                  | ❌               |
| участник                        | ❌                   | ❌                 | ❌                  | ❌               |

---

## Тестовые пользователи

| Логин       | Пароль     | Должность                       | Отдел       |
| ----------- | ---------- | ------------------------------- | ----------- |
| admin       | admin123   | admin                           | IT          |
| predsedatel | pred123    | председатель                    | Руководство |
| zampred     | zampred123 | заместитель председателя        | Руководство |
| sekr        | sekr123    | секретарь                       | Руководство |
| ruk_it      | ruk123     | руководитель отдела/направления | IT          |
| zamruk_it   | zamruk123  | заместитель руководителя отдела | IT          |
| user1       | user123    | участник                        | IT          |
| user2       | user123    | участник                        | SMM         |
| user3       | user123    | участник                        | Фандрайз    |

---

## Категории и карточки

### Отделы и направления

- Фандрай
- IT
- SMM

### Мероприятия

- Тропа
- Капуста

### Общая информация

- Положение
- Правила редактуры

---

## Примеры использования

### Вход в систему

```bash
curl -X POST "http://127.0.0.1:8000/api/login" \
  -H "Content-Type: application/json" \
  -d '{"login": "admin", "password": "admin123"}'
```

### Получение списка должностей

```bash
curl "http://127.0.0.1:8000/api/positions" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### Создание новой должности (только для admin, председателя, секретаря)

```bash
curl -X POST "http://127.0.0.1:8000/api/positions" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "координатор",
    "hierarchy_level": 4,
    "can_register_users": true,
    "can_edit_categories": true,
    "can_delete_categories": false,
    "can_edit_cards": true,
    "can_delete_cards": false,
    "can_manage_positions": false
  }'
```

### Получение списка пользователей

```bash
curl "http://127.0.0.1:8000/api/users" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### Фильтрация пользователей по отделу

```bash
curl "http://127.0.0.1:8000/api/users?department=IT" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### Создание категории

```bash
curl -X POST "http://127.0.0.1:8000/api/categories" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "Документы", "description": "Официальные документы"}'
```

---

## База данных

SQLite база данных хранится в файле `app.db` и **не пересоздается** при каждом запуске.
Данные сохраняются между перезапусками приложения.

Для пересоздания БД с начальными данными:

```bash
rm app.db && python3 seed_db_v2.py
```

---

## Безопасность

- JWT токены живут 30 дней
- Пароли хешируются с помощью bcrypt
- Все endpoints кроме `/api/login` требуют JWT токен в заголовке `Authorization: Bearer <token>`
- Ролевой доступ реализован через таблицу должностей (Position)
- Управление должностями доступно только пользователям с `can_manage_positions=True`
- **Пользователи не могут редактировать себя** - для изменения своих данных обратитесь к администратору

---

## CORS

Разрешённые origin для фронтенда:

- `http://localhost:5173`
- `http://127.0.0.1:5173`
- `http://localhost:3000`
- `http://127.0.0.1:3000`

Для добавления нового origin измените `allow_origins` в `main.py`.
