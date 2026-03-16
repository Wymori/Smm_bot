from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

main_menu_kb = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="Контент-план", callback_data="content_plan")],
    [InlineKeyboardButton(text="Хештеги", callback_data="hashtags")],
    [InlineKeyboardButton(text="Шаблоны", callback_data="templates")],
    [InlineKeyboardButton(text="Заметки / Идеи", callback_data="notes")],
])


def content_plan_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Создать пост", callback_data="cp_create")],
        [InlineKeyboardButton(text="Мои посты", callback_data="cp_list")],
        [InlineKeyboardButton(text="Назад", callback_data="main_menu")],
    ])


def hashtag_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Создать набор", callback_data="ht_create")],
        [InlineKeyboardButton(text="Мои наборы", callback_data="ht_list")],
        [InlineKeyboardButton(text="Назад", callback_data="main_menu")],
    ])


def template_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Создать шаблон", callback_data="tpl_create")],
        [InlineKeyboardButton(text="Мои шаблоны", callback_data="tpl_list")],
        [InlineKeyboardButton(text="Назад", callback_data="main_menu")],
    ])


def notes_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Новая заметка", callback_data="note_create")],
        [InlineKeyboardButton(text="Мои заметки", callback_data="note_list")],
        [InlineKeyboardButton(text="Назад", callback_data="main_menu")],
    ])


def back_kb(callback_data: str = "main_menu") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Назад", callback_data=callback_data)],
    ])


def item_actions_kb(prefix: str, item_id: int, back_to: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Редактировать", callback_data=f"{prefix}_edit:{item_id}"),
            InlineKeyboardButton(text="Удалить", callback_data=f"{prefix}_del:{item_id}"),
        ],
        [InlineKeyboardButton(text="Назад", callback_data=back_to)],
    ])


def edit_fields_kb(
    prefix: str, item_id: int, fields: list[tuple[str, str]], back_to: str,
) -> InlineKeyboardMarkup:
    """Клавиатура выбора поля для редактирования.

    fields — список кортежей (field_key, label), например [("title", "Заголовок")].
    """
    buttons = [
        [InlineKeyboardButton(text=label, callback_data=f"{prefix}_ef:{item_id}:{key}")]
        for key, label in fields
    ]
    buttons.append([InlineKeyboardButton(text="Назад", callback_data=f"{prefix}_view:{item_id}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)
