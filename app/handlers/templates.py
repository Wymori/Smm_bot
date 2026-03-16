from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Template
from app.keyboards.main_menu import back_kb, edit_fields_kb, item_actions_kb, template_menu_kb
from app.services.user_service import get_or_create_user

router = Router()

TEMPLATE_TYPES = {
    "tpl_type_post": "post",
    "tpl_type_story": "story",
    "tpl_type_brief": "brief",
}

type_labels = {"post": "Пост", "story": "Сторис", "brief": "ТЗ для дизайнера"}


class CreateTemplate(StatesGroup):
    template_type = State()
    name = State()
    content = State()


class EditTemplate(StatesGroup):
    value = State()


# --- Menu ---

@router.callback_query(F.data == "templates")
async def template_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Шаблоны:", reply_markup=template_menu_kb())
    await callback.answer()


# --- Create ---

@router.callback_query(F.data == "tpl_create")
async def tpl_create_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(CreateTemplate.template_type)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Пост", callback_data="tpl_type_post")],
        [InlineKeyboardButton(text="Сторис", callback_data="tpl_type_story")],
        [InlineKeyboardButton(text="ТЗ для дизайнера", callback_data="tpl_type_brief")],
        [InlineKeyboardButton(text="Назад", callback_data="templates")],
    ])
    await callback.message.edit_text("Выберите тип шаблона:", reply_markup=kb)
    await callback.answer()


@router.callback_query(CreateTemplate.template_type, F.data.in_(TEMPLATE_TYPES))
async def tpl_create_type(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(template_type=TEMPLATE_TYPES[callback.data])
    await state.set_state(CreateTemplate.name)
    await callback.message.edit_text("Введите название шаблона:", reply_markup=back_kb("templates"))
    await callback.answer()


@router.message(CreateTemplate.name)
async def tpl_create_name(message: Message, state: FSMContext) -> None:
    await state.update_data(name=message.text)
    await state.set_state(CreateTemplate.content)
    await message.answer(
        "Введите содержимое шаблона.\n\n"
        "Можете использовать переменные: {название}, {дата}, {продукт} и т.д."
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
        template_type=data["template_type"],
    )
    session.add(tpl)
    await session.commit()

    await state.clear()
    await message.answer(f"Шаблон \"{data['name']}\" сохранен!", reply_markup=template_menu_kb())


# --- List ---

@router.callback_query(F.data == "tpl_list")
async def tpl_list(callback: CallbackQuery, session: AsyncSession) -> None:
    user = await get_or_create_user(
        session, callback.from_user.id, callback.from_user.username, callback.from_user.full_name
    )
    result = await session.execute(
        select(Template).where(Template.user_id == user.id).order_by(Template.created_at.desc()).limit(20)
    )
    templates = result.scalars().all()

    if not templates:
        await callback.message.edit_text("У вас пока нет шаблонов.", reply_markup=template_menu_kb())
        await callback.answer()
        return

    buttons = []
    for t in templates:
        label = f"[{type_labels.get(t.template_type, t.template_type)}] {t.name}"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"tpl_view:{t.id}")])
    buttons.append([InlineKeyboardButton(text="Назад", callback_data="templates")])

    await callback.message.edit_text("Ваши шаблоны:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()


# --- View ---

@router.callback_query(F.data.startswith("tpl_view:"))
async def tpl_view(callback: CallbackQuery, session: AsyncSession) -> None:
    tpl_id = int(callback.data.split(":")[1])
    result = await session.execute(select(Template).where(Template.id == tpl_id))
    tpl = result.scalar_one_or_none()

    if not tpl:
        await callback.answer("Шаблон не найден", show_alert=True)
        return

    text = f"<b>{tpl.name}</b>\nТип: {type_labels.get(tpl.template_type, tpl.template_type)}\n\n{tpl.content}"
    await callback.message.edit_text(
        text, parse_mode="HTML",
        reply_markup=item_actions_kb("tpl", tpl.id, "tpl_list"),
    )
    await callback.answer()


# --- Edit ---

TPL_EDIT_FIELDS = [
    ("name", "Название"),
    ("content", "Содержимое"),
]


@router.callback_query(F.data.startswith("tpl_edit:"))
async def tpl_edit_start(callback: CallbackQuery, session: AsyncSession) -> None:
    tpl_id = int(callback.data.split(":")[1])
    result = await session.execute(select(Template).where(Template.id == tpl_id))
    tpl = result.scalar_one_or_none()
    if not tpl:
        await callback.answer("Шаблон не найден", show_alert=True)
        return
    await callback.message.edit_text(
        f"Редактирование шаблона \"{tpl.name}\".\nВыберите поле:",
        reply_markup=edit_fields_kb("tpl", tpl.id, TPL_EDIT_FIELDS, "tpl_list"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("tpl_ef:"))
async def tpl_edit_field(callback: CallbackQuery, state: FSMContext) -> None:
    _, item_id, field = callback.data.split(":")
    await state.set_state(EditTemplate.value)
    await state.update_data(edit_id=int(item_id), edit_field=field)
    labels = dict(TPL_EDIT_FIELDS)
    await callback.message.edit_text(
        f"Введите новое значение для поля \"{labels[field]}\":",
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
        await message.answer("Шаблон не найден.", reply_markup=template_menu_kb())
        return
    setattr(tpl, field, message.text)
    await session.commit()
    await state.clear()
    text = f"<b>{tpl.name}</b>\nТип: {type_labels.get(tpl.template_type, tpl.template_type)}\n\n{tpl.content}"
    await message.answer(
        f"Сохранено!\n\n{text}",
        parse_mode="HTML",
        reply_markup=item_actions_kb("tpl", tpl.id, "tpl_list"),
    )


# --- Delete ---

@router.callback_query(F.data.startswith("tpl_del:"))
async def tpl_delete(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    tpl_id = int(callback.data.split(":")[1])
    result = await session.execute(select(Template).where(Template.id == tpl_id))
    tpl = result.scalar_one_or_none()
    if tpl:
        await session.delete(tpl)
        await session.commit()
    await callback.answer("Удалено")
    await template_menu(callback, state)
