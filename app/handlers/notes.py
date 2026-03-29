from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Note
from app.keyboards.main_menu import back_kb, confirm_delete_kb, edit_fields_kb, item_actions_kb, notes_menu_kb, paginate, pagination_row
from app.services.user_service import get_or_create_user

router = Router()


class CreateNote(StatesGroup):
    title = State()
    text = State()


class EditNote(StatesGroup):
    value = State()


# --- Helpers ---

def _note_card(note: Note) -> str:
    return (
        f"💡 <b>{note.title}</b>\n\n"
        f"{note.text}"
    )


# --- Menu ---

@router.callback_query(F.data == "notes")
async def notes_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(
        "💡 <b>Заметки / Идеи</b>\n\n"
        "Храните идеи для контента в одном месте:",
        parse_mode="HTML",
        reply_markup=notes_menu_kb(),
    )
    await callback.answer()


# --- Create ---

@router.callback_query(F.data == "note_create")
async def note_create_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(CreateNote.title)
    await callback.message.edit_text(
        "💡 <b>Новая заметка</b>\n\n"
        "Введите заголовок заметки:",
        parse_mode="HTML",
        reply_markup=back_kb("notes"),
    )
    await callback.answer()


@router.message(CreateNote.title)
async def note_create_title(message: Message, state: FSMContext) -> None:
    await state.update_data(title=message.text)
    await state.set_state(CreateNote.text)
    await message.answer("✍️ Введите текст заметки:")


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
    await message.answer(
        f"✅ Заметка <b>«{data['title']}»</b> сохранена!",
        parse_mode="HTML",
        reply_markup=notes_menu_kb(),
    )


# --- List ---

async def _show_note_list(callback: CallbackQuery, session: AsyncSession, page: int = 0) -> None:
    user = await get_or_create_user(
        session, callback.from_user.id, callback.from_user.username, callback.from_user.full_name
    )
    result = await session.execute(
        select(Note).where(Note.user_id == user.id).order_by(Note.created_at.desc())
    )
    all_notes = result.scalars().all()

    if not all_notes:
        await callback.message.edit_text(
            "📭 <b>У вас пока нет заметок.</b>\n\n"
            "Нажмите «Новая заметка», чтобы начать!",
            parse_mode="HTML",
            reply_markup=notes_menu_kb(),
        )
        await callback.answer()
        return

    notes, page, total = paginate(all_notes, page)
    buttons = []
    for n in notes:
        buttons.append([InlineKeyboardButton(text=f"💡 {n.title}", callback_data=f"note_view:{n.id}")])
    if total > 1:
        buttons.append(pagination_row("note_page", page, total))
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="notes")])

    await callback.message.edit_text(
        "💡 <b>Ваши заметки:</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data == "note_list")
async def note_list(callback: CallbackQuery, session: AsyncSession) -> None:
    await _show_note_list(callback, session)


@router.callback_query(F.data.startswith("note_page:"))
async def note_list_page(callback: CallbackQuery, session: AsyncSession) -> None:
    page = int(callback.data.split(":")[1])
    await _show_note_list(callback, session, page)


# --- View ---

@router.callback_query(F.data.startswith("note_view:"))
async def note_view(callback: CallbackQuery, session: AsyncSession) -> None:
    note_id = int(callback.data.split(":")[1])
    result = await session.execute(select(Note).where(Note.id == note_id))
    note = result.scalar_one_or_none()

    if not note:
        await callback.answer("Заметка не найдена", show_alert=True)
        return

    await callback.message.edit_text(
        _note_card(note), parse_mode="HTML",
        reply_markup=item_actions_kb("note", note.id, "note_list"),
    )
    await callback.answer()


# --- Edit ---

NOTE_EDIT_FIELDS = [
    ("title", "Заголовок"),
    ("text", "Текст"),
]


@router.callback_query(F.data.startswith("note_edit:"))
async def note_edit_start(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    note_id = int(callback.data.split(":")[1])
    result = await session.execute(select(Note).where(Note.id == note_id))
    note = result.scalar_one_or_none()
    if not note:
        await callback.answer("Заметка не найдена", show_alert=True)
        return
    await callback.message.edit_text(
        f"✏️ <b>Редактирование заметки</b>\n\n"
        f"💡 {note.title}\n\n"
        f"Выберите поле для изменения:",
        parse_mode="HTML",
        reply_markup=edit_fields_kb("note", note.id, NOTE_EDIT_FIELDS),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("note_ef:"))
async def note_edit_field(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    _, item_id, field = callback.data.split(":")
    await state.set_state(EditNote.value)
    await state.update_data(edit_id=int(item_id), edit_field=field)

    result = await session.execute(select(Note).where(Note.id == int(item_id)))
    note = result.scalar_one_or_none()
    labels = dict(NOTE_EDIT_FIELDS)
    current = getattr(note, field, "") if note else ""

    await callback.message.edit_text(
        f"✏️ <b>Редактирование: {labels[field]}</b>\n\n"
        f"Текущее — нажмите, чтобы скопировать:\n"
        f"<code>{current}</code>\n\n"
        f"Введите новое значение:",
        parse_mode="HTML",
        reply_markup=back_kb(f"note_edit:{item_id}"),
    )
    await callback.answer()


@router.message(EditNote.value)
async def note_edit_save(message: Message, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    note_id, field = data["edit_id"], data["edit_field"]
    result = await session.execute(select(Note).where(Note.id == note_id))
    note = result.scalar_one_or_none()
    if not note:
        await state.clear()
        await message.answer("❌ Заметка не найдена.", reply_markup=notes_menu_kb())
        return
    setattr(note, field, message.text)
    await session.commit()
    await state.clear()
    await message.answer(
        f"✅ <b>Сохранено!</b>\n\n{_note_card(note)}",
        parse_mode="HTML",
        reply_markup=item_actions_kb("note", note.id, "note_list"),
    )


# --- Delete ---

@router.callback_query(F.data.startswith("note_del:"))
async def note_delete_ask(callback: CallbackQuery, session: AsyncSession) -> None:
    note_id = int(callback.data.split(":")[1])
    result = await session.execute(select(Note).where(Note.id == note_id))
    note = result.scalar_one_or_none()
    if not note:
        await callback.answer("Заметка не найдена", show_alert=True)
        return
    await callback.message.edit_text(
        f"🗑 <b>Удаление заметки</b>\n\n"
        f"Вы уверены, что хотите удалить «{note.title}»?",
        parse_mode="HTML",
        reply_markup=confirm_delete_kb("note", note.id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("note_confirm_del:"))
async def note_delete_confirm(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    note_id = int(callback.data.split(":")[1])
    result = await session.execute(select(Note).where(Note.id == note_id))
    note = result.scalar_one_or_none()
    if note:
        await session.delete(note)
        await session.commit()
    await callback.answer("✅ Удалено")
    await notes_menu(callback, state)
