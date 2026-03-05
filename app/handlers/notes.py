from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Note
from app.keyboards.main_menu import back_kb, item_actions_kb, notes_menu_kb
from app.services.user_service import get_or_create_user

router = Router()


class CreateNote(StatesGroup):
    title = State()
    text = State()


# --- Menu ---

@router.callback_query(F.data == "notes")
async def notes_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Заметки / Идеи:", reply_markup=notes_menu_kb())
    await callback.answer()


# --- Create ---

@router.callback_query(F.data == "note_create")
async def note_create_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(CreateNote.title)
    await callback.message.edit_text("Введите заголовок заметки:", reply_markup=back_kb("notes"))
    await callback.answer()


@router.message(CreateNote.title)
async def note_create_title(message: Message, state: FSMContext) -> None:
    await state.update_data(title=message.text)
    await state.set_state(CreateNote.text)
    await message.answer("Введите текст заметки:")


@router.message(CreateNote.text)
async def note_create_text(message: Message, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    user = await get_or_create_user(
        session, message.from_user.id, message.from_user.username, message.from_user.full_name
    )

    note = Note(user_id=user.id, title=data["title"], text=message.text)
    session.add(note)
    await session.commit()

    await state.clear()
    await message.answer(f"Заметка \"{data['title']}\" сохранена!", reply_markup=notes_menu_kb())


# --- List ---

@router.callback_query(F.data == "note_list")
async def note_list(callback: CallbackQuery, session: AsyncSession) -> None:
    user = await get_or_create_user(
        session, callback.from_user.id, callback.from_user.username, callback.from_user.full_name
    )
    result = await session.execute(
        select(Note).where(Note.user_id == user.id).order_by(Note.created_at.desc()).limit(20)
    )
    notes = result.scalars().all()

    if not notes:
        await callback.message.edit_text("У вас пока нет заметок.", reply_markup=notes_menu_kb())
        await callback.answer()
        return

    buttons = []
    for n in notes:
        buttons.append([InlineKeyboardButton(text=n.title, callback_data=f"note_view:{n.id}")])
    buttons.append([InlineKeyboardButton(text="Назад", callback_data="notes")])

    await callback.message.edit_text("Ваши заметки:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()


# --- View ---

@router.callback_query(F.data.startswith("note_view:"))
async def note_view(callback: CallbackQuery, session: AsyncSession) -> None:
    note_id = int(callback.data.split(":")[1])
    result = await session.execute(select(Note).where(Note.id == note_id))
    note = result.scalar_one_or_none()

    if not note:
        await callback.answer("Заметка не найдена", show_alert=True)
        return

    text = f"<b>{note.title}</b>\n\n{note.text}"
    await callback.message.edit_text(
        text, parse_mode="HTML",
        reply_markup=item_actions_kb("note", note.id, "note_list"),
    )
    await callback.answer()


# --- Delete ---

@router.callback_query(F.data.startswith("note_del:"))
async def note_delete(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    note_id = int(callback.data.split(":")[1])
    result = await session.execute(select(Note).where(Note.id == note_id))
    note = result.scalar_one_or_none()
    if note:
        await session.delete(note)
        await session.commit()
    await callback.answer("Удалено")
    await notes_menu(callback, state)
