from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import HashtagSet
from app.keyboards.main_menu import back_kb, edit_fields_kb, hashtag_menu_kb, item_actions_kb
from app.services.user_service import get_or_create_user

router = Router()


class CreateHashtagSet(StatesGroup):
    name = State()
    category = State()
    hashtags = State()


class EditHashtagSet(StatesGroup):
    value = State()


# --- Menu ---

@router.callback_query(F.data == "hashtags")
async def hashtag_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Хештеги:", reply_markup=hashtag_menu_kb())
    await callback.answer()


# --- Create ---

@router.callback_query(F.data == "ht_create")
async def ht_create_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(CreateHashtagSet.name)
    await callback.message.edit_text(
        "Введите название набора хештегов (например: 'Фитнес'):",
        reply_markup=back_kb("hashtags"),
    )
    await callback.answer()


@router.message(CreateHashtagSet.name)
async def ht_create_name(message: Message, state: FSMContext) -> None:
    await state.update_data(name=message.text)
    await state.set_state(CreateHashtagSet.category)
    await message.answer("Введите категорию (или '-' чтобы пропустить):")


@router.message(CreateHashtagSet.category)
async def ht_create_category(message: Message, state: FSMContext) -> None:
    category = message.text if message.text != "-" else None
    await state.update_data(category=category)
    await state.set_state(CreateHashtagSet.hashtags)
    await message.answer("Введите хештеги через пробел:\n(например: #фитнес #спорт #здоровье)")


@router.message(CreateHashtagSet.hashtags)
async def ht_create_hashtags(message: Message, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    user = await get_or_create_user(
        session, message.from_user.id, message.from_user.username, message.from_user.full_name
    )

    hs = HashtagSet(
        user_id=user.id,
        name=data["name"],
        category=data.get("category"),
        hashtags=message.text,
    )
    session.add(hs)
    await session.commit()

    await state.clear()
    await message.answer(f"Набор \"{data['name']}\" сохранен!", reply_markup=hashtag_menu_kb())


# --- List ---

@router.callback_query(F.data == "ht_list")
async def ht_list(callback: CallbackQuery, session: AsyncSession) -> None:
    user = await get_or_create_user(
        session, callback.from_user.id, callback.from_user.username, callback.from_user.full_name
    )
    result = await session.execute(
        select(HashtagSet).where(HashtagSet.user_id == user.id).order_by(HashtagSet.created_at.desc()).limit(20)
    )
    sets = result.scalars().all()

    if not sets:
        await callback.message.edit_text("У вас пока нет наборов хештегов.", reply_markup=hashtag_menu_kb())
        await callback.answer()
        return

    buttons = []
    for s in sets:
        label = f"{s.name}" + (f" [{s.category}]" if s.category else "")
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"ht_view:{s.id}")])
    buttons.append([InlineKeyboardButton(text="Назад", callback_data="hashtags")])

    await callback.message.edit_text("Ваши наборы:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()


# --- View ---

@router.callback_query(F.data.startswith("ht_view:"))
async def ht_view(callback: CallbackQuery, session: AsyncSession) -> None:
    hs_id = int(callback.data.split(":")[1])
    result = await session.execute(select(HashtagSet).where(HashtagSet.id == hs_id))
    hs = result.scalar_one_or_none()

    if not hs:
        await callback.answer("Набор не найден", show_alert=True)
        return

    cat = f"\nКатегория: {hs.category}" if hs.category else ""
    text = f"<b>{hs.name}</b>{cat}\n\n{hs.hashtags}"
    await callback.message.edit_text(
        text, parse_mode="HTML",
        reply_markup=item_actions_kb("ht", hs.id, "ht_list"),
    )
    await callback.answer()


# --- Edit ---

HT_EDIT_FIELDS = [
    ("name", "Название"),
    ("category", "Категория"),
    ("hashtags", "Хештеги"),
]


@router.callback_query(F.data.startswith("ht_edit:"))
async def ht_edit_start(callback: CallbackQuery, session: AsyncSession) -> None:
    hs_id = int(callback.data.split(":")[1])
    result = await session.execute(select(HashtagSet).where(HashtagSet.id == hs_id))
    hs = result.scalar_one_or_none()
    if not hs:
        await callback.answer("Набор не найден", show_alert=True)
        return
    await callback.message.edit_text(
        f"Редактирование набора \"{hs.name}\".\nВыберите поле:",
        reply_markup=edit_fields_kb("ht", hs.id, HT_EDIT_FIELDS, "ht_list"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ht_ef:"))
async def ht_edit_field(callback: CallbackQuery, state: FSMContext) -> None:
    _, item_id, field = callback.data.split(":")
    await state.set_state(EditHashtagSet.value)
    await state.update_data(edit_id=int(item_id), edit_field=field)
    labels = dict(HT_EDIT_FIELDS)
    await callback.message.edit_text(
        f"Введите новое значение для поля \"{labels[field]}\":",
        reply_markup=back_kb(f"ht_edit:{item_id}"),
    )
    await callback.answer()


@router.message(EditHashtagSet.value)
async def ht_edit_save(message: Message, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    hs_id, field = data["edit_id"], data["edit_field"]
    result = await session.execute(select(HashtagSet).where(HashtagSet.id == hs_id))
    hs = result.scalar_one_or_none()
    if not hs:
        await state.clear()
        await message.answer("Набор не найден.", reply_markup=hashtag_menu_kb())
        return
    setattr(hs, field, message.text)
    await session.commit()
    await state.clear()
    cat = f"\nКатегория: {hs.category}" if hs.category else ""
    text = f"<b>{hs.name}</b>{cat}\n\n{hs.hashtags}"
    await message.answer(
        f"Сохранено!\n\n{text}",
        parse_mode="HTML",
        reply_markup=item_actions_kb("ht", hs.id, "ht_list"),
    )


# --- Copy hashtags (tap to copy) ---

@router.callback_query(F.data.startswith("ht_copy:"))
async def ht_copy(callback: CallbackQuery, session: AsyncSession) -> None:
    hs_id = int(callback.data.split(":")[1])
    result = await session.execute(select(HashtagSet).where(HashtagSet.id == hs_id))
    hs = result.scalar_one_or_none()

    if hs:
        await callback.message.answer(f"<code>{hs.hashtags}</code>", parse_mode="HTML")
    await callback.answer()


# --- Delete ---

@router.callback_query(F.data.startswith("ht_del:"))
async def ht_delete(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    hs_id = int(callback.data.split(":")[1])
    result = await session.execute(select(HashtagSet).where(HashtagSet.id == hs_id))
    hs = result.scalar_one_or_none()
    if hs:
        await session.delete(hs)
        await session.commit()
    await callback.answer("Удалено")
    await hashtag_menu(callback, state)
