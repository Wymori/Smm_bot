from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Channel
from app.keyboards.main_menu import back_kb
from app.services.user_service import get_or_create_user

router = Router()


class AddChannel(StatesGroup):
    waiting = State()


# --- Helpers ---

def _channel_card(ch: Channel) -> str:
    link = f"@{ch.username}" if ch.username else f"ID: {ch.channel_id}"
    return f"📢 <b>{ch.title}</b>\n{link}"


def _channels_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Подключить канал", callback_data="ch_add")],
        [InlineKeyboardButton(text="📂 Мои каналы", callback_data="ch_list")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="main_menu")],
    ])


# --- Menu ---

@router.callback_query(F.data == "channels")
async def channels_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(
        "⚙️ <b>Каналы</b>\n\n"
        "Подключите Telegram-канал, чтобы публиковать посты.\n\n"
        "<i>Бот должен быть администратором канала "
        "с правом публикации сообщений.</i>",
        parse_mode="HTML",
        reply_markup=_channels_menu_kb(),
    )
    await callback.answer()


# --- Add ---

@router.callback_query(F.data == "ch_add")
async def ch_add_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AddChannel.waiting)
    await callback.message.edit_text(
        "📢 <b>Подключение канала</b>\n\n"
        "Перешлите любое сообщение из вашего канала сюда.\n\n"
        "<i>Убедитесь, что бот добавлен как администратор канала "
        "с правом публикации сообщений.</i>",
        parse_mode="HTML",
        reply_markup=back_kb("channels"),
    )
    await callback.answer()


@router.message(AddChannel.waiting)
async def ch_add_receive(message: Message, state: FSMContext, session: AsyncSession, bot: Bot) -> None:
    # Check if forwarded from a channel
    if not message.forward_origin:
        await message.answer(
            "⚠️ Перешлите сообщение из канала.\n"
            "Просто отправьте (forward) любое сообщение из вашего канала.",
        )
        return

    from aiogram.types import MessageOriginChannel
    if not isinstance(message.forward_origin, MessageOriginChannel):
        await message.answer(
            "⚠️ Это не сообщение из канала.\n"
            "Перешлите сообщение именно из канала, а не из группы или чата.",
        )
        return

    channel_chat = message.forward_origin.chat
    channel_id = channel_chat.id
    channel_title = channel_chat.title or "Без названия"
    channel_username = channel_chat.username

    # Check bot is admin
    try:
        bot_member = await bot.get_chat_member(channel_id, (await bot.me()).id)
        if bot_member.status not in ("administrator", "creator"):
            await message.answer(
                f"⚠️ <b>Бот не является администратором канала</b>\n\n"
                f"Канал: {channel_title}\n\n"
                f"Добавьте бота как администратора канала "
                f"с правом «Публикация сообщений», затем попробуйте снова.",
                parse_mode="HTML",
            )
            return
    except Exception:
        await message.answer(
            "⚠️ <b>Не удалось проверить права бота</b>\n\n"
            "Убедитесь, что бот добавлен как администратор канала.",
            parse_mode="HTML",
        )
        return

    user = await get_or_create_user(
        session, message.from_user.id, message.from_user.username, message.from_user.full_name
    )

    # Check if already added
    existing = await session.execute(
        select(Channel).where(Channel.user_id == user.id, Channel.channel_id == channel_id)
    )
    if existing.scalar_one_or_none():
        await state.clear()
        await message.answer(
            f"ℹ️ Канал «{channel_title}» уже подключён!",
            reply_markup=_channels_menu_kb(),
        )
        return

    ch = Channel(
        user_id=user.id,
        channel_id=channel_id,
        title=channel_title,
        username=channel_username,
    )
    session.add(ch)
    await session.commit()

    await state.clear()
    await message.answer(
        f"✅ <b>Канал подключён!</b>\n\n{_channel_card(ch)}",
        parse_mode="HTML",
        reply_markup=_channels_menu_kb(),
    )


# --- List ---

@router.callback_query(F.data == "ch_list")
async def ch_list(callback: CallbackQuery, session: AsyncSession) -> None:
    user = await get_or_create_user(
        session, callback.from_user.id, callback.from_user.username, callback.from_user.full_name
    )
    result = await session.execute(
        select(Channel).where(Channel.user_id == user.id).order_by(Channel.created_at.desc())
    )
    channels = result.scalars().all()

    if not channels:
        await callback.message.edit_text(
            "📭 <b>У вас нет подключённых каналов.</b>\n\n"
            "Нажмите «Подключить канал» чтобы начать.",
            parse_mode="HTML",
            reply_markup=_channels_menu_kb(),
        )
        await callback.answer()
        return

    buttons = []
    for ch in channels:
        label = f"📢 {ch.title}"
        if ch.username:
            label += f" (@{ch.username})"
        buttons.append([
            InlineKeyboardButton(text=label, callback_data=f"ch_view:{ch.id}"),
        ])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="channels")])

    await callback.message.edit_text(
        "📢 <b>Ваши каналы:</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


# --- View / Delete ---

@router.callback_query(F.data.startswith("ch_view:"))
async def ch_view(callback: CallbackQuery, session: AsyncSession) -> None:
    ch_id = int(callback.data.split(":")[1])
    result = await session.execute(select(Channel).where(Channel.id == ch_id))
    ch = result.scalar_one_or_none()
    if not ch:
        await callback.answer("Канал не найден", show_alert=True)
        return

    await callback.message.edit_text(
        f"{_channel_card(ch)}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Отключить канал", callback_data=f"ch_del:{ch.id}")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="ch_list")],
        ]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ch_del:"))
async def ch_delete_ask(callback: CallbackQuery, session: AsyncSession) -> None:
    ch_id = int(callback.data.split(":")[1])
    result = await session.execute(select(Channel).where(Channel.id == ch_id))
    ch = result.scalar_one_or_none()
    if not ch:
        await callback.answer("Канал не найден", show_alert=True)
        return

    await callback.message.edit_text(
        f"🗑 <b>Отключение канала</b>\n\n"
        f"Вы уверены, что хотите отключить «{ch.title}»?",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да, отключить", callback_data=f"ch_confirm_del:{ch.id}"),
                InlineKeyboardButton(text="❌ Отмена", callback_data=f"ch_view:{ch.id}"),
            ],
        ]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("ch_confirm_del:"))
async def ch_delete_confirm(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    ch_id = int(callback.data.split(":")[1])
    result = await session.execute(select(Channel).where(Channel.id == ch_id))
    ch = result.scalar_one_or_none()
    if ch:
        await session.delete(ch)
        await session.commit()
    await callback.answer("✅ Канал отключён")
    await channels_menu(callback, state)
