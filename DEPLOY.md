# Инструкция по деплою на Selectel

## Вариант 1: Cloud Containers (Docker)

### Быстрый старт

```bash
# Сборка и запуск локально для проверки
docker-compose up --build

# Деплой в Selectel Cloud Containers
# 1. Зарегистрируйтесь в https://my.selectel.ru/
# 2. Создайте проект в Cloud Containers
# 3. Подключите GitHub репозиторий для автодеплоя
```

### Через Docker CLI

```bash
# Логин в Container Registry Selectel
docker login cr.selcloud.ru -u <username> -p <password>

# Сборка образа
docker build -t cr.selcloud.ru/<project-id>/legacy-back:latest .

# Пуш образа
docker push cr.selcloud.ru/<project-id>/legacy-back:latest
```

### Параметры контейнера

- **Порт**: 8000
- **CPU**: 1 vCPU
- **RAM**: 1-2 GB
- **Переменные окружения**:
  - `SECRET_KEY` - ваш секретный ключ
  - `PORT` - 8000

---

## Вариант 2: VPS (Ubuntu 22.04)

### 1. Подключение к серверу

```bash
ssh root@<ваш-ip>
```

### 2. Установка зависимостей

```bash
apt update && apt upgrade -y
apt install -y python3 python3-pip python3-venv git nginx
```

### 3. Клонирование проекта

```bash
cd /var/www
git clone https://github.com/IT-FAMCS/legacy-back.git
cd legacy-back

python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install gunicorn
```

### 4. Инициализация БД

```bash
python3 seed_db_v2.py
```

### 5. systemd сервис

```bash
cat > /etc/systemd/system/legacy-back.service << EOF
[Unit]
Description=Legacy Back FastAPI
After=network.target

[Service]
User=root
WorkingDirectory=/var/www/legacy-back
Environment="PATH=/var/www/legacy-back/venv/bin"
ExecStart=/var/www/legacy-back/venv/bin/gunicorn main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 127.0.0.1:8000
Restart=always

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable legacy-back
systemctl start legacy-back
```

### 6. Nginx конфигурация

```bash
cat > /etc/nginx/sites-available/legacy-back << EOF
server {
    listen 80;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    }
}
EOF

ln -s /etc/nginx/sites-available/legacy-back /etc/nginx/sites-enabled/
nginx -t
systemctl restart nginx
```

### 7. HTTPS (Let's Encrypt)

```bash
apt install -y certbot python3-certbot-nginx
certbot --nginx -d your-domain.com
```

---

## Переменные окружения

| Переменная | Описание | По умолчанию |
|------------|----------|--------------|
| `SECRET_KEY` | Ключ для JWT | `your-secret-key-change-in-production` |
| `PORT` | Порт приложения | `8000` |
| `DATABASE_URL` | URL БД | `sqlite:///./app.db` |
| `ACCESS_TOKEN_EXPIRE_DAYS` | Срок действия токена | `30` |

---

## Проверка работы

```bash
# Проверка API
curl http://localhost:8000/

# Вход админа
curl -X POST "http://localhost:8000/api/login" \
  -H "Content-Type: application/json" \
  -d '{"login":"admin","password":"admin123"}'

# Swagger UI
# Откройте http://localhost:8000/docs
```

---

## Логи

```bash
# Приложение
journalctl -u legacy-back -f

# Nginx
tail -f /var/log/nginx/access.log
tail -f /var/log/nginx/error.log

# Docker
docker-compose logs -f
```

---

## Бэкап БД

```bash
# Ручной бэкап
cp /var/www/legacy-back/app.db /var/backups/app.db.$(date +%Y%m%d)

# Автобэкап (cron)
echo "0 2 * * * cp /var/www/legacy-back/app.db /var/backups/app.db.\$(date +\%Y\%m\%d)" | crontab -
