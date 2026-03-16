# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Стек
- **Python 3.12+**, aiogram 3.13, SQLAlchemy 2.0 (async), asyncpg, PostgreSQL
- **Alembic** — настроен (`alembic.ini` + `alembic/env.py`), но `versions/` пуст — таблицы создаются через `create_all`
- **Pydantic Settings** — конфигурация из `.env`
- Установлены, но не используются: `apscheduler`, `pillow`, `openai`/`anthropic` API keys

## Команды
```bash
# Установка зависимостей
pip install -r requirements.txt

# Запуск бота
python run.py

# Alembic (когда будут миграции)
alembic revision --autogenerate -m "description"
alembic upgrade head
```

## Архитектура

**Точка входа:** `run.py` → `app/bot.py:main()` — создаёт Bot, Dispatcher, подключает middleware и роутеры, запускает polling.

**Поток данных:** Telegram → aiogram Dispatcher → DbSessionMiddleware (инъекция AsyncSession) → Router/Handler → SQLAlchemy → PostgreSQL.

**on_startup** (`app/bot.py`) вызывает `Base.metadata.create_all()` — автосоздание всех таблиц при старте.

## Модели БД (`app/database/models.py`)
5 моделей: **User**, **ContentPlan**, **HashtagSet**, **Template**, **Note**.
Все связаны с User через FK с каскадным удалением (`ondelete="CASCADE"` + `cascade="all, delete-orphan"`).

## Паттерны кода
- **FSM (StatesGroup)** — пошаговый ввод данных при создании и редактировании
- **Callback data** — формат `{prefix}_{action}:{id}` (например `cp_view:5`, `ht_ef:3:name`)
- **Редактирование** — `{prefix}_edit:{id}` → выбор поля → `{prefix}_ef:{id}:{field}` → ввод значения → `setattr(obj, field, value)`
- **Клавиатуры** — функции-фабрики в `keyboards/main_menu.py`, переиспользуемые `item_actions_kb()` и `edit_fields_kb()`
- Весь UI на русском языке, `parse_mode=HTML`
- Спецсимволы в `DATABASE_URL` (`.env`) должны быть URL-encoded (`#` → `%23`, `@` → `%40`)

## Конфигурация
- `.env` — секреты (BOT_TOKEN, DATABASE_URL), лежит в корне проекта
- `.env.example` — шаблон
- `alembic.ini` — URL базы данных перезаписывается из Settings в `alembic/env.py` (строка 12)

## Что реализовано
- Полный CRUD для всех 4 разделов (контент-планы, хештеги, шаблоны, заметки)
- Главное меню с навигацией
- FSM для создания/редактирования
- Middleware для автоинъекции сессии БД
- Сервис создания/получения пользователя

## Что НЕ реализовано
- Alembic миграции (versions/ пуст)
- Публикация по расписанию (APScheduler не подключен)
- AI-генерация текста
- Медиа-инструменты (Pillow)
- Аналитика, отложенный постинг, калькулятор цен, авто-отчёты, чек-листы
