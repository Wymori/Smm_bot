from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import ContentPlan
from app.keyboards.main_menu import back_kb, content_plan_menu_kb, item_actions_kb
from app.services.user_service import get_or_create_user

router = Router()


class CreatePost(StatesGroup):
    title = State()
    text = State()
    platform = State()


# --- Menu ---

@router.callback_query(F.data == "content_plan")
async def content_plan_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Контент-план:", reply_markup=content_plan_menu_kb())
    await callback.answer()


# --- Create ---

@router.callback_query(F.data == "cp_create")
async def cp_create_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(CreatePost.title)
    await callback.message.edit_text("Введите заголовок поста:", reply_markup=back_kb("content_plan"))
    await callback.answer()


@router.message(CreatePost.title)
async def cp_create_title(message: Message, state: FSMContext) -> None:
    await state.update_data(title=message.text)
    await state.set_state(CreatePost.text)
    await message.answer("Введите текст поста (или отправьте '-' чтобы пропустить):")


@router.message(CreatePost.text)
async def cp_create_text(message: Message, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    text = message.text if message.text != "-" else None

    user = await get_or_create_user(
        session, message.from_user.id, message.from_user.username, message.from_user.full_name
    )
    post = ContentPlan(user_id=user.id, title=data["title"], text=text)
    session.add(post)
    await session.commit()

    await state.clear()
    await message.answer(
        f"Пост \"{data['title']}\" создан!",
        reply_markup=content_plan_menu_kb(),
    )


# --- List ---

@router.callback_query(F.data == "cp_list")
async def cp_list(callback: CallbackQuery, session: AsyncSession) -> None:
    user = await get_or_create_user(
        session, callback.from_user.id, callback.from_user.username, callback.from_user.full_name
    )
    result = await session.execute(
        select(ContentPlan)
        .where(ContentPlan.user_id == user.id)
        .order_by(ContentPlan.created_at.desc())
        .limit(20)
    )
    posts = result.scalars().all()

    if not posts:
        await callback.message.edit_text("У вас пока нет постов.", reply_markup=content_plan_menu_kb())
        await callback.answer()
        return

    lines = []
    for p in posts:
        status = "v" if p.is_published else "o"
        lines.append(f"[{status}] {p.title} (id:{p.id})")

    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    buttons = []
    for p in posts:
        buttons.append([InlineKeyboardButton(text=p.title, callback_data=f"cp_view:{p.id}")])
    buttons.append([InlineKeyboardButton(text="Назад", callback_data="content_plan")])

    await callback.message.edit_text("Ваши посты:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()


# --- View ---

@router.callback_query(F.data.startswith("cp_view:"))
async def cp_view(callback: CallbackQuery, session: AsyncSession) -> None:
    post_id = int(callback.data.split(":")[1])
    result = await session.execute(select(ContentPlan).where(ContentPlan.id == post_id))
    post = result.scalar_one_or_none()

    if not post:
        await callback.answer("Пост не найден", show_alert=True)
        return

    text = f"<b>{post.title}</b>\n\n{post.text or '(нет текста)'}\n\nПлатформа: {post.platform}"
    await callback.message.edit_text(
        text, parse_mode="HTML",
        reply_markup=item_actions_kb("cp", post.id, "cp_list"),
    )
    await callback.answer()


# --- Delete ---

@router.callback_query(F.data.startswith("cp_del:"))
async def cp_delete(callback: CallbackQuery, session: AsyncSession) -> None:
    post_id = int(callback.data.split(":")[1])
    result = await session.execute(select(ContentPlan).where(ContentPlan.id == post_id))
    post = result.scalar_one_or_none()
    if post:
        await session.delete(post)
        await session.commit()
    await callback.answer("Удалено")
    await cp_list.__wrapped__(callback, session) if hasattr(cp_list, '__wrapped__') else await content_plan_menu(callback, None)
