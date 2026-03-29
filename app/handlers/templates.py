import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Template
from app.keyboards.main_menu import back_kb, confirm_delete_kb, edit_fields_kb, item_actions_kb, paginate, pagination_row, template_menu_kb
from app.services.user_service import get_or_create_user

router = Router()


class CreateTemplate(StatesGroup):
    name = State()
    content = State()


class EditTemplate(StatesGroup):
    value = State()


# --- Helpers ---

def _find_variables(text: str) -> list[str]:
    """Находит уникальные {переменные} в тексте, сохраняя порядок."""
    seen = set()
    result = []
    for match in re.finditer(r"\{([^}]+)\}", text):
        var = match.group(1)
        if var not in seen:
            seen.add(var)
            result.append(var)
    return result


def _tpl_card(tpl: Template) -> str:
    lines = [f"📝 <b>{tpl.name}</b>"]
    variables = _find_variables(tpl.content)
    if variables:
        var_list = ", ".join(f"{{{v}}}" for v in variables)
        lines.append(f"🔤 Переменные: <code>{var_list}</code>")
    lines.append("")
    lines.append(tpl.content)
    return "\n".join(lines)


# --- Menu ---

@router.callback_query(F.data == "templates")
async def template_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(
        "📝 <b>Шаблоны</b>\n\n"
        "Создавайте шаблоны постов с переменными\n"
        "и используйте их в контент-плане:",
        parse_mode="HTML",
        reply_markup=template_menu_kb(),
    )
    await callback.answer()


# --- Create ---

@router.callback_query(F.data == "tpl_create")
async def tpl_create_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(CreateTemplate.name)
    await callback.message.edit_text(
        "📝 <b>Новый шаблон</b>\n\n"
        "Введите название шаблона:\n"
        "<i>Например: «Анонс продукта», «Отзыв клиента»</i>",
        parse_mode="HTML",
        reply_markup=back_kb("templates"),
    )
    await callback.answer()


@router.message(CreateTemplate.name)
async def tpl_create_name(message: Message, state: FSMContext) -> None:
    await state.update_data(name=message.text)
    await state.set_state(CreateTemplate.content)
    await message.answer(
        "✍️ <b>Введите содержимое шаблона</b>\n\n"
        "💡 <b>Переменные</b> — слова в фигурных скобках, "
        "которые будут заменены при создании поста:\n\n"
        "<code>Встречайте {продукт}!\n"
        "Цена: {цена}\n"
        "Только до {дата}!</code>\n\n"
        "Вы можете придумать любые переменные — "
        "бот спросит их значения при создании поста.",
        parse_mode="HTML",
    )


@router.message(CreateTemplate.content)
async def tpl_create_content(message: Message, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    user = await get_or_create_user(
        session, message.from_user.id, message.from_user.username, message.from_user.full_name
    )

    tpl = Template(
        user_id=user.id,
        name=data["name"],
        content=message.text,
    )
    session.add(tpl)
    await session.commit()

    variables = _find_variables(message.text)
    var_info = ""
    if variables:
        var_list = ", ".join(f"{{{v}}}" for v in variables)
        var_info = f"\n🔤 Переменные: <code>{var_list}</code>"

    await state.clear()
    await message.answer(
        f"✅ Шаблон <b>«{data['name']}»</b> сохранён!{var_info}",
        parse_mode="HTML",
        reply_markup=template_menu_kb(),
    )


# --- List ---

async def _show_tpl_list(callback: CallbackQuery, session: AsyncSession, page: int = 0) -> None:
    user = await get_or_create_user(
        session, callback.from_user.id, callback.from_user.username, callback.from_user.full_name
    )
    result = await session.execute(
        select(Template).where(Template.user_id == user.id).order_by(Template.created_at.desc())
    )
    all_templates = result.scalars().all()

    if not all_templates:
        await callback.message.edit_text(
            "📭 <b>У вас пока нет шаблонов.</b>\n\n"
            "Нажмите «Создать шаблон», чтобы начать!",
            parse_mode="HTML",
            reply_markup=template_menu_kb(),
        )
        await callback.answer()
        return

    templates, page, total = paginate(all_templates, page)
    buttons = []
    for t in templates:
        var_count = len(_find_variables(t.content))
        badge = f" 🔤{var_count}" if var_count else ""
        buttons.append([InlineKeyboardButton(
            text=f"📝 {t.name}{badge}",
            callback_data=f"tpl_view:{t.id}",
        )])
    if total > 1:
        buttons.append(pagination_row("tpl_page", page, total))
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="templates")])

    await callback.message.edit_text(
        "📝 <b>Ваши шаблоны:</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data == "tpl_list")
async def tpl_list(callback: CallbackQuery, session: AsyncSession) -> None:
    await _show_tpl_list(callback, session)


@router.callback_query(F.data.startswith("tpl_page:"))
async def tpl_list_page(callback: CallbackQuery, session: AsyncSession) -> None:
    page = int(callback.data.split(":")[1])
    await _show_tpl_list(callback, session, page)


# --- View ---

@router.callback_query(F.data.startswith("tpl_view:"))
async def tpl_view(callback: CallbackQuery, session: AsyncSession) -> None:
    tpl_id = int(callback.data.split(":")[1])
    result = await session.execute(select(Template).where(Template.id == tpl_id))
    tpl = result.scalar_one_or_none()

    if not tpl:
        await callback.answer("Шаблон не найден", show_alert=True)
        return

    await callback.message.edit_text(
        _tpl_card(tpl), parse_mode="HTML",
        reply_markup=item_actions_kb("tpl", tpl.id, "tpl_list"),
    )
    await callback.answer()


# --- Edit ---

TPL_EDIT_FIELDS = [
    ("name", "Название"),
    ("content", "Содержимое"),
]


@router.callback_query(F.data.startswith("tpl_edit:"))
async def tpl_edit_start(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    tpl_id = int(callback.data.split(":")[1])
    result = await session.execute(select(Template).where(Template.id == tpl_id))
    tpl = result.scalar_one_or_none()
    if not tpl:
        await callback.answer("Шаблон не найден", show_alert=True)
        return
    await callback.message.edit_text(
        f"✏️ <b>Редактирование шаблона</b>\n\n"
        f"📝 {tpl.name}\n\n"
        f"Выберите поле для изменения:",
        parse_mode="HTML",
        reply_markup=edit_fields_kb("tpl", tpl.id, TPL_EDIT_FIELDS),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("tpl_ef:"))
async def tpl_edit_field(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    _, item_id, field = callback.data.split(":")
    await state.set_state(EditTemplate.value)
    await state.update_data(edit_id=int(item_id), edit_field=field)

    result = await session.execute(select(Template).where(Template.id == int(item_id)))
    tpl = result.scalar_one_or_none()
    labels = dict(TPL_EDIT_FIELDS)
    current = getattr(tpl, field, "") if tpl else ""

    await callback.message.edit_text(
        f"✏️ <b>Редактирование: {labels[field]}</b>\n\n"
        f"Текущее — нажмите, чтобы скопировать:\n"
        f"<code>{current}</code>\n\n"
        f"Введите новое значение:",
        parse_mode="HTML",
        reply_markup=back_kb(f"tpl_edit:{item_id}"),
    )
    await callback.answer()


@router.message(EditTemplate.value)
async def tpl_edit_save(message: Message, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    tpl_id, field = data["edit_id"], data["edit_field"]
    result = await session.execute(select(Template).where(Template.id == tpl_id))
    tpl = result.scalar_one_or_none()
    if not tpl:
        await state.clear()
        await message.answer("❌ Шаблон не найден.", reply_markup=template_menu_kb())
        return
    setattr(tpl, field, message.text)
    await session.commit()
    await state.clear()
    await message.answer(
        f"✅ <b>Сохранено!</b>\n\n{_tpl_card(tpl)}",
        parse_mode="HTML",
        reply_markup=item_actions_kb("tpl", tpl.id, "tpl_list"),
    )


# --- Delete ---

@router.callback_query(F.data.startswith("tpl_del:"))
async def tpl_delete_ask(callback: CallbackQuery, session: AsyncSession) -> None:
    tpl_id = int(callback.data.split(":")[1])
    result = await session.execute(select(Template).where(Template.id == tpl_id))
    tpl = result.scalar_one_or_none()
    if not tpl:
        await callback.answer("Шаблон не найден", show_alert=True)
        return
    await callback.message.edit_text(
        f"🗑 <b>Удаление шаблона</b>\n\n"
        f"Вы уверены, что хотите удалить «{tpl.name}»?",
        parse_mode="HTML",
        reply_markup=confirm_delete_kb("tpl", tpl.id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("tpl_confirm_del:"))
async def tpl_delete_confirm(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    tpl_id = int(callback.data.split(":")[1])
    result = await session.execute(select(Template).where(Template.id == tpl_id))
    tpl = result.scalar_one_or_none()
    if tpl:
        await session.delete(tpl)
        await session.commit()
    await callback.answer("✅ Удалено")
    await template_menu(callback, state)
