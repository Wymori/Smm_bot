from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.keyboards.main_menu import main_menu_kb
from app.services.user_service import get_or_create_user

router = Router()

WELCOME_TEXT = (
    "Привет! Я SMM-помощник.\n\n"
    "Выбери раздел:"
)


@router.message(CommandStart())
async def cmd_start(message: Message, session: AsyncSession) -> None:
    await get_or_create_user(
        session,
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
    )
    await message.answer(WELCOME_TEXT, reply_markup=main_menu_kb)


@router.callback_query(lambda c: c.data == "main_menu")
async def back_to_main(callback: CallbackQuery) -> None:
    await callback.message.edit_text(WELCOME_TEXT, reply_markup=main_menu_kb)
    await callback.answer()
