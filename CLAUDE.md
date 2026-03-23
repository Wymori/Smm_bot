# CLAUDE.md

Руководство для Claude Code по проекту SMM Bot — Telegram-бот для SMM-специалистов.

## Стек
- **Python 3.12+**, aiogram 3.13, SQLAlchemy 2.0 (async), asyncpg, PostgreSQL 16
- **Alembic** — настроен (`alembic.ini` + `alembic/env.py`), но `versions/` пуст — таблицы создаются через `create_all`
- **Pydantic Settings** — конфигурация из `.env`
- Установлены, но пока не используются: `apscheduler`, `pillow`

## Команды
```bash
# Виртуальное окружение (venv в корне проекта)
source venv/bin/activate

# Запуск бота
python run.py

# Заполнение тестовых данных (хештеги)
python seed_data.py

# Alembic (когда будут миграции)
alembic revision --autogenerate -m "description"
alembic upgrade head
```

## Архитектура

**Точка входа:** `run.py` → `app/bot.py:main()` — создаёт Bot, Dispatcher, подключает middleware и роутеры, запускает polling.

**Поток данных:** Telegram → aiogram Dispatcher → DbSessionMiddleware (инъекция AsyncSession) → Router/Handler → SQLAlchemy → PostgreSQL.

**on_startup** (`app/bot.py`) вызывает `Base.metadata.create_all()` + `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` для новых полей (hashtags в content_plans).

**Глобальный `parse_mode=HTML`** задан в `DefaultBotProperties` при создании Bot — не нужно указывать в каждом `message.answer()`.

## Модели БД (`app/database/models.py`)
5 моделей: **User**, **ContentPlan**, **HashtagSet**, **Template**, **Note**.
Все связаны с User через FK с каскадным удалением (`ondelete="CASCADE"` + `cascade="all, delete-orphan"`).

### Ключевые поля
- **ContentPlan**: `title`, `text`, `hashtags` (отдельное поле!), `platform` (telegram/instagram), `scheduled_at`, `is_published`
- **HashtagSet**: `name`, `hashtags` (текст через пробел), `category`
- **Template**: `name`, `content`, `template_type` (default="general", типы убраны из UI)
- **Note**: `title`, `text`

## Паттерны кода

### Callback data
Формат: `{prefix}_{action}:{id}` или `{prefix}_{action}:{id}:{field}`
- Контент-план: `cp_` (view, edit, ef, del, confirm_del, add_ht, toggle_ht, custom_ht, publish, from_tpl, use_tpl, page)
- Хештеги: `ht_` (view, edit, ef, del, confirm_del, copy, page)
- Шаблоны: `tpl_` (view, edit, ef, del, confirm_del, page)
- Заметки: `note_` (view, edit, ef, del, confirm_del, page)
- Служебные: `main_menu`, `noop` (для кнопки-счётчика страниц)

### FSM-состояния
- **Создание**: `Create{Entity}` — пошаговый ввод полей
- **Редактирование**: `Edit{Entity}` — одно состояние `value`, в `state.data` хранятся `edit_id` и `edit_field`
- **Хештеги в постах**: `AddHashtags.custom` — ввод пользовательских хештегов для поста
- **Пост из шаблона**: `CreateFromTemplate` (title → fill_variable) → переиспользует `CreatePost.platform` для выбора платформы

### Клавиатуры (`keyboards/main_menu.py`)
Общие фабрики: `item_actions_kb()`, `edit_fields_kb()`, `confirm_delete_kb()`, `back_kb()`
Пагинация: `paginate()`, `pagination_row()`, `PAGE_SIZE = 5`
Хэндлеры контент-плана и хештегов используют собственные клавиатуры (`_post_actions_kb`, `_ht_actions_kb`) с дополнительными кнопками.

### UI-стиль
- Весь UI на **русском языке**, `parse_mode=HTML` (глобальный default)
- Эмодзи в кнопках и сообщениях: 📋📝💡🏷✏️🗑◀️➕📂✅❌📤 и т.д.
- Карточки сущностей через helper-функции: `_post_card()`, `_ht_card()`, `_tpl_card()`, `_note_card()`
- При редактировании — текущее значение показывается в `<code>` для копирования
- Подтверждение удаления: `{prefix}_del:` → подтверждение → `{prefix}_confirm_del:` → удаление
- Пагинация: `[◀️] [2/5] [▶️]` — появляется когда элементов больше `PAGE_SIZE`
- Спецсимволы в `DATABASE_URL` (`.env`) должны быть URL-encoded (`#` → `%23`, `@` → `%40`)

## Особенности

### Контент-план
- После создания поста показывается карточка с действиями (не меню)
- Выбор платформы: Telegram / Instagram (Instagram — заглушка)
- **Хештеги хранятся отдельно** в поле `hashtags` (не в `text`!), колонка добавляется через ALTER TABLE в on_startup
- **Флаги наборов**: ✅ = набор применён (хотя бы частично), ➕ = не применён. Нажатие переключает (toggle)
- Хелперы: `_is_set_applied()`, `_add_tags()`, `_remove_tags()` — case-insensitive сравнение, без дубликатов
- **Валидация**: свои хештеги и редактирование — каждое слово должно начинаться с `#`
- **Редактирование хештегов**: через "Редактировать" → "Хештеги" — показывает текущие в `<code>`, можно заменить или «-» для очистки
- **Публикация**: заглушка (`show_alert`), функция в разработке
- Поля `scheduled_at` и `is_published` отображаются в карточке, но устанавливаются пока только вручную в БД

### Хештеги
- Кнопка «Копировать» — отправляет хештеги в `<code>` для удобного копирования
- `seed_data.py` — скрипт для заполнения тестовых наборов (Фитнес, Кулинария, Компьютерные игры)

### Шаблоны
- Типы убраны из UI (поле `template_type` в БД сохраняется с default="general")
- **Переменные**: текст в `{фигурных скобках}` → при создании поста из шаблона бот просит заполнить каждую
- Детект переменных: `_find_variables()` (regex `\{([^}]+)\}`) — дублируется в `templates.py` и `content_plan.py`
- Связь с контент-планом: кнопка «📝 Из шаблона» в меню контент-плана → выбор шаблона → заголовок → переменные → платформа → пост

## Конфигурация
- `.env` — секреты (BOT_TOKEN, DATABASE_URL), лежит в корне проекта
- `.env.example` — шаблон
- `alembic.ini` — URL базы данных перезаписывается из Settings в `alembic/env.py`
- PostgreSQL: БД `smm_bot`, пользователь `smm_bot`

## Что реализовано
- Полный CRUD для 4 разделов (контент-планы, хештеги, шаблоны, заметки)
- Пагинация списков (по 5 элементов, кнопки ◀️/▶️)
- Главное меню с навигацией и эмодзи
- FSM для создания/редактирования
- Подтверждение удаления во всех разделах
- Хештеги к постам: toggle-флаги ✅/➕, свои с валидацией, редактирование
- Копирование хештегов и текущих значений при редактировании через `<code>`
- Выбор платформы при создании поста
- Создание постов из шаблонов с подстановкой переменных
- Middleware для автоинъекции сессии БД
- Сервис создания/получения пользователя
- Скрипт тестовых данных (`seed_data.py`)

## Что НЕ реализовано
- Alembic миграции (`versions/` пуст)
- Публикация по расписанию (APScheduler установлен, но не подключён)
- Подключение бота к Telegram-каналам для публикации
- Instagram-интеграция (платформа выбирается, но публикация — заглушка)
- AI-генерация текста (ключи OpenAI/Anthropic в конфиге, не используются)
- Медиа-инструменты (Pillow установлен, не используется)
- Аналитика, калькулятор цен, авто-отчёты, чек-листы
