from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import HashtagSet
from app.keyboards.main_menu import back_kb, confirm_delete_kb, edit_fields_kb, hashtag_menu_kb, paginate, pagination_row
from app.services.user_service import get_or_create_user

router = Router()


class CreateHashtagSet(StatesGroup):
    name = State()
    category = State()
    hashtags = State()


class EditHashtagSet(StatesGroup):
    value = State()


# --- Helpers ---

def _ht_card(hs: HashtagSet) -> str:
    lines = [f"🏷 <b>{hs.name}</b>"]
    if hs.category:
        lines.append(f"📁 Категория: {hs.category}")
    lines.append("")
    lines.append(hs.hashtags)
    return "\n".join(lines)


def _ht_actions_kb(hs_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Копировать", callback_data=f"ht_copy:{hs_id}")],
        [
            InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"ht_edit:{hs_id}"),
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"ht_del:{hs_id}"),
        ],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="ht_list")],
    ])


# --- Menu ---

@router.callback_query(F.data == "hashtags")
async def hashtag_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(
        "🏷 <b>Хештеги</b>\n\n"
        "Создавайте и управляйте наборами хештегов:",
        parse_mode="HTML",
        reply_markup=hashtag_menu_kb(),
    )
    await callback.answer()


# --- Create ---

@router.callback_query(F.data == "ht_create")
async def ht_create_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(CreateHashtagSet.name)
    await callback.message.edit_text(
        "🏷 <b>Новый набор хештегов</b>\n\n"
        "Введите название набора:\n"
        "<i>Например: «Фитнес», «Путешествия»</i>",
        parse_mode="HTML",
        reply_markup=back_kb("hashtags"),
    )
    await callback.answer()


@router.message(CreateHashtagSet.name)
async def ht_create_name(message: Message, state: FSMContext) -> None:
    await state.update_data(name=message.text)
    await state.set_state(CreateHashtagSet.category)
    await message.answer(
        "📁 Введите категорию:\n\n"
        "<i>Отправьте «-» чтобы пропустить</i>",
        parse_mode="HTML",
    )


@router.message(CreateHashtagSet.category)
async def ht_create_category(message: Message, state: FSMContext) -> None:
    category = message.text if message.text != "-" else None
    await state.update_data(category=category)
    await state.set_state(CreateHashtagSet.hashtags)
    await message.answer(
        "✍️ Введите хештеги через пробел:\n\n"
        "<i>Например: #фитнес #спорт #здоровье</i>",
        parse_mode="HTML",
    )


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
    await message.answer(
        f"✅ Набор <b>«{data['name']}»</b> сохранён!",
        parse_mode="HTML",
        reply_markup=hashtag_menu_kb(),
    )


# --- List ---

async def _show_ht_list(callback: CallbackQuery, session: AsyncSession, page: int = 0) -> None:
    user = await get_or_create_user(
        session, callback.from_user.id, callback.from_user.username, callback.from_user.full_name
    )
    result = await session.execute(
        select(HashtagSet).where(HashtagSet.user_id == user.id).order_by(HashtagSet.created_at.desc())
    )
    all_sets = result.scalars().all()

    if not all_sets:
        await callback.message.edit_text(
            "📭 <b>У вас пока нет наборов хештегов.</b>\n\n"
            "Нажмите «Создать набор», чтобы начать!",
            parse_mode="HTML",
            reply_markup=hashtag_menu_kb(),
        )
        await callback.answer()
        return

    sets, page, total = paginate(all_sets, page)
    buttons = []
    for s in sets:
        label = f"🏷 {s.name}" + (f" [{s.category}]" if s.category else "")
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"ht_view:{s.id}")])
    if total > 1:
        buttons.append(pagination_row("ht_page", page, total))
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="hashtags")])

    await callback.message.edit_text(
        "🏷 <b>Ваши наборы хештегов:</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data == "ht_list")
async def ht_list(callback: CallbackQuery, session: AsyncSession) -> None:
    await _show_ht_list(callback, session)


@router.callback_query(F.data.startswith("ht_page:"))
async def ht_list_page(callback: CallbackQuery, session: AsyncSession) -> None:
    page = int(callback.data.split(":")[1])
    await _show_ht_list(callback, session, page)


# --- View ---

@router.callback_query(F.data.startswith("ht_view:"))
async def ht_view(callback: CallbackQuery, session: AsyncSession) -> None:
    hs_id = int(callback.data.split(":")[1])
    result = await session.execute(select(HashtagSet).where(HashtagSet.id == hs_id))
    hs = result.scalar_one_or_none()

    if not hs:
        await callback.answer("Набор не найден", show_alert=True)
        return

    await callback.message.edit_text(
        _ht_card(hs), parse_mode="HTML",
        reply_markup=_ht_actions_kb(hs.id),
    )
    await callback.answer()


# --- Edit ---

HT_EDIT_FIELDS = [
    ("name", "Название"),
    ("category", "Категория"),
    ("hashtags", "Хештеги"),
]


@router.callback_query(F.data.startswith("ht_edit:"))
async def ht_edit_start(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    hs_id = int(callback.data.split(":")[1])
    result = await session.execute(select(HashtagSet).where(HashtagSet.id == hs_id))
    hs = result.scalar_one_or_none()
    if not hs:
        await callback.answer("Набор не найден", show_alert=True)
        return
    await callback.message.edit_text(
        f"✏️ <b>Редактирование набора</b>\n\n"
        f"🏷 {hs.name}\n\n"
        f"Выберите поле для изменения:",
        parse_mode="HTML",
        reply_markup=edit_fields_kb("ht", hs.id, HT_EDIT_FIELDS),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ht_ef:"))
async def ht_edit_field(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    _, item_id, field = callback.data.split(":")
    await state.set_state(EditHashtagSet.value)
    await state.update_data(edit_id=int(item_id), edit_field=field)

    result = await session.execute(select(HashtagSet).where(HashtagSet.id == int(item_id)))
    hs = result.scalar_one_or_none()
    labels = dict(HT_EDIT_FIELDS)
    current = getattr(hs, field, "") or "(нет)" if hs else "(нет)"

    await callback.message.edit_text(
        f"✏️ <b>Редактирование: {labels[field]}</b>\n\n"
        f"Текущее — нажмите, чтобы скопировать:\n"
        f"<code>{current}</code>\n\n"
        f"Введите новое значение:",
        parse_mode="HTML",
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
        await message.answer("❌ Набор не найден.", reply_markup=hashtag_menu_kb())
        return
    setattr(hs, field, message.text)
    await session.commit()
    await state.clear()
    await message.answer(
        f"✅ <b>Сохранено!</b>\n\n{_ht_card(hs)}",
        parse_mode="HTML",
        reply_markup=_ht_actions_kb(hs.id),
    )


# --- Copy hashtags (tap to copy) ---

@router.callback_query(F.data.startswith("ht_copy:"))
async def ht_copy(callback: CallbackQuery, session: AsyncSession) -> None:
    hs_id = int(callback.data.split(":")[1])
    result = await session.execute(select(HashtagSet).where(HashtagSet.id == hs_id))
    hs = result.scalar_one_or_none()

    if hs:
        await callback.message.answer(
            f"📋 <b>Нажмите, чтобы скопировать:</b>\n\n"
            f"<code>{hs.hashtags}</code>",
            parse_mode="HTML",
        )
    await callback.answer()


# --- Delete ---

@router.callback_query(F.data.startswith("ht_del:"))
async def ht_delete_ask(callback: CallbackQuery, session: AsyncSession) -> None:
    hs_id = int(callback.data.split(":")[1])
    result = await session.execute(select(HashtagSet).where(HashtagSet.id == hs_id))
    hs = result.scalar_one_or_none()
    if not hs:
        await callback.answer("Набор не найден", show_alert=True)
        return
    await callback.message.edit_text(
        f"🗑 <b>Удаление набора</b>\n\n"
        f"Вы уверены, что хотите удалить «{hs.name}»?",
        parse_mode="HTML",
        reply_markup=confirm_delete_kb("ht", hs.id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ht_confirm_del:"))
async def ht_delete_confirm(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    hs_id = int(callback.data.split(":")[1])
    result = await session.execute(select(HashtagSet).where(HashtagSet.id == hs_id))
    hs = result.scalar_one_or_none()
    if hs:
        await session.delete(hs)
        await session.commit()
    await callback.answer("✅ Удалено")
    await hashtag_menu(callback, state)
