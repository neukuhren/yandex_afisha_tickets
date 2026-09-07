# Yandex Afisha Tickets Bot

Telegram-бот для мониторинга наличия билетов на события Яндекс Афиши.

## Возможности

- Периодический парсинг билетов по секторам и ценам (раз в минуту)
- Уведомления администраторам о появлении билетов (каждые 30 секунд в течение часа, кнопка «Я увидел»)
- Уведомления об изменении количества билетов
- Включение/выключение оповещений по каждому событию
- Добавление событий по ссылке (только суперадмин, команда скрыта от остальных)

## Быстрый старт (локально)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# заполните BOT_TOKEN и DATABASE_URL
PYTHONPATH=. python main.py
```

## Деплой на сервер (systemd + PostgreSQL)

```bash
export BOT_TOKEN=...
export DB_PASSWORD=...
bash deploy/install.sh
```

## Переменные окружения

| Переменная | Описание |
|---|---|
| `BOT_TOKEN` | Токен Telegram-бота |
| `DATABASE_URL` | PostgreSQL URL (`postgresql+asyncpg://...`) |
| `SUPER_ADMIN_ID` | Telegram ID суперадмина |
| `ADMIN_IDS` | Telegram ID админов через запятую |
| `PARSE_INTERVAL_SECONDS` | Интервал парсинга (по умолчанию 60) |
| `APPEARANCE_INTERVAL_SECONDS` | Интервал повторных уведомлений (30) |
| `APPEARANCE_DURATION_SECONDS` | Длительность серии уведомлений (3600) |

## Команды бота

- `/start` — приветствие
- `/events` — управление оповещениями по событиям
- `/add_url` — добавить событие (видно только суперадмину)
