import math

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

PAGE_SIZE = 5


def paginate(items: list, page: int) -> tuple[list, int, int]:
    """Возвращает (элементы_страницы, текущая_страница, всего_страниц)."""
    total = math.ceil(len(items) / PAGE_SIZE) if items else 1
    page = max(0, min(page, total - 1))
    start = page * PAGE_SIZE
    return items[start : start + PAGE_SIZE], page, total


def pagination_row(prefix: str, page: int, total: int) -> list[InlineKeyboardButton]:
    """Строка кнопок навигации: [◀️] [2/5] [▶️]."""
    buttons = []
    if page > 0:
        buttons.append(InlineKeyboardButton(text="◀️", callback_data=f"{prefix}:{page - 1}"))
    buttons.append(InlineKeyboardButton(text=f"{page + 1}/{total}", callback_data="noop"))
    if page < total - 1:
        buttons.append(InlineKeyboardButton(text="▶️", callback_data=f"{prefix}:{page + 1}"))
    return buttons

main_menu_kb = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="📋 Контент-план", callback_data="content_plan")],
    [InlineKeyboardButton(text="🏷 Хештеги", callback_data="hashtags")],
    [InlineKeyboardButton(text="📝 Шаблоны", callback_data="templates")],
    [InlineKeyboardButton(text="💡 Заметки / Идеи", callback_data="notes")],
])


def content_plan_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Создать пост", callback_data="cp_create")],
        [InlineKeyboardButton(text="📝 Из шаблона", callback_data="cp_from_tpl")],
        [InlineKeyboardButton(text="📂 Мои посты", callback_data="cp_list")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="main_menu")],
    ])


def hashtag_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Создать набор", callback_data="ht_create")],
        [InlineKeyboardButton(text="📂 Мои наборы", callback_data="ht_list")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="main_menu")],
    ])


def template_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Создать шаблон", callback_data="tpl_create")],
        [InlineKeyboardButton(text="📂 Мои шаблоны", callback_data="tpl_list")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="main_menu")],
    ])


def notes_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Новая заметка", callback_data="note_create")],
        [InlineKeyboardButton(text="📂 Мои заметки", callback_data="note_list")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="main_menu")],
    ])


def back_kb(callback_data: str = "main_menu") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data=callback_data)],
    ])


def item_actions_kb(prefix: str, item_id: int, back_to: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"{prefix}_edit:{item_id}"),
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"{prefix}_del:{item_id}"),
        ],
        [InlineKeyboardButton(text="◀️ Назад", callback_data=back_to)],
    ])


def edit_fields_kb(
    prefix: str, item_id: int, fields: list[tuple[str, str]],
) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=f"✏️ {label}", callback_data=f"{prefix}_ef:{item_id}:{key}")]
        for key, label in fields
    ]
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"{prefix}_view:{item_id}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def confirm_delete_kb(prefix: str, item_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"{prefix}_confirm_del:{item_id}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data=f"{prefix}_view:{item_id}"),
        ],
    ])
