# Изменения для деплоя — legacy-back

## Что поменялось в коде
- **main.py** — `SECRET_KEY`, `ACCESS_TOKEN_EXPIRE_DAYS`, `ALLOWED_ORIGINS` теперь читаются из
  переменных окружения (дефолты прежние → обратно совместимо, dev-режим не ломается).
- **database.py** — строка подключения читается из `DATABASE_URL` (дефолт прежний `sqlite:///./app.db`).
  В проде передаётся `sqlite:////data/app.db` (файл в persistent-volume).
- **.github/workflows/deploy.yml** (новый) — на пуш в `master`/`main` собирает docker-образ
  и пушит в `ghcr.io/it-famcs/legacy-back:latest`.

## Как задеплоить (авторелиз)
1. Влить эти файлы в репозиторий и **запушить в master/main**.
2. Проверить, что GitHub Actions собрал образ (вкладка **Actions**).
3. **Один раз:** сделать пакет публичным — Организация **IT-FAMCS** → **Packages** →
   `legacy-back` → **Package settings** → **Change visibility → Public**.
4. Всё. На сервере стек и **watchtower** уже настроены — он сам подтянет свежий образ (раз в час)
   и перезапустит контейнер. БД в volume не теряется.

## Важно
- Прод-конфиг сервера (docker-compose, Caddy, бэкапы) живёт на сервере отдельно и в git не входит.
- Образ бэка запускается как контейнер `legacy-backend`, слушает :8000, БД — `/data/app.db` в volume.
