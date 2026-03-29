import logging
import re
from datetime import datetime, timedelta, timezone

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import Channel, ContentPlan, ContentPlanMedia, HashtagSet, SchedulePreset, Template
from app.services.publish_service import publish_post, scheduled_publish_job

MSK = timezone(timedelta(hours=3))
from app.keyboards.main_menu import (
    back_kb,
    confirm_delete_kb,
    content_plan_menu_kb,
    edit_fields_kb,
    paginate,
    pagination_row,
)
from app.services.user_service import get_or_create_user

router = Router()

def _find_variables(text: str) -> list[str]:
    seen = set()
    result = []
    for match in re.finditer(r"\{([^}]+)\}", text):
        var = match.group(1)
        if var not in seen:
            seen.add(var)
            result.append(var)
    return result


class CreatePost(StatesGroup):
    title = State()
    text = State()
    media = State()


class AddMedia(StatesGroup):
    waiting = State()


class SchedulePost(StatesGroup):
    datetime_input = State()


class CreatePreset(StatesGroup):
    hours = State()
    days = State()
    time = State()


class EditPost(StatesGroup):
    value = State()


class AddHashtags(StatesGroup):
    custom = State()


class CreateFromTemplate(StatesGroup):
    title = State()
    fill_variable = State()


MEDIA_TYPE_LABELS = {
    "photo": "фото",
    "video": "видео",
    "document": "документ",
    "audio": "аудио",
    "animation": "GIF",
    "voice": "голосовое",
    "video_note": "кружок",
    "sticker": "стикер",
}

MEDIA_TYPE_ICONS = {
    "photo": "🖼",
    "video": "🎬",
    "document": "📄",
    "audio": "🎵",
    "animation": "🎞",
    "voice": "🎤",
    "video_note": "⏺",
    "sticker": "🏷",
}


def _extract_media(message: Message) -> tuple[str, str, str | None] | None:
    """Извлекает (file_id, media_type, file_name) из сообщения."""
    if message.photo:
        return message.photo[-1].file_id, "photo", None
    if message.video:
        return message.video.file_id, "video", message.video.file_name
    if message.animation:
        return message.animation.file_id, "animation", message.animation.file_name
    if message.document:
        return message.document.file_id, "document", message.document.file_name
    if message.audio:
        return message.audio.file_id, "audio", message.audio.file_name
    if message.voice:
        return message.voice.file_id, "voice", None
    if message.video_note:
        return message.video_note.file_id, "video_note", None
    if message.sticker:
        return message.sticker.file_id, "sticker", None
    return None


def _media_summary(media_list: list) -> str:
    """Возвращает строку вида '2 фото, 1 видео'."""
    counts: dict[str, int] = {}
    for m in media_list:
        counts[m.media_type] = counts.get(m.media_type, 0) + 1
    parts = []
    for mtype, count in counts.items():
        icon = MEDIA_TYPE_ICONS.get(mtype, "📎")
        label = MEDIA_TYPE_LABELS.get(mtype, mtype)
        parts.append(f"{icon} {count} {label}")
    return ", ".join(parts)


# --- Helpers ---

def _is_set_applied(post_hashtags: str | None, set_hashtags: str) -> bool:
    if not post_hashtags:
        return False
    post_tags = {t.lower() for t in post_hashtags.split()}
    set_tags = {t.lower() for t in set_hashtags.split()}
    return bool(post_tags & set_tags)


def _add_tags(existing: str | None, new_tags: str) -> str:
    existing_lower = {t.lower() for t in (existing.split() if existing else [])}
    existing_list = existing.split() if existing else []
    for tag in new_tags.split():
        if tag.lower() not in existing_lower:
            existing_list.append(tag)
            existing_lower.add(tag.lower())
    return " ".join(existing_list)


def _remove_tags(existing: str | None, tags_to_remove: str) -> str | None:
    if not existing:
        return None
    remove_lower = {t.lower() for t in tags_to_remove.split()}
    remaining = [t for t in existing.split() if t.lower() not in remove_lower]
    return " ".join(remaining) if remaining else None


def _post_card(post: ContentPlan) -> str:
    status = "✅ Опубликован" if post.is_published else "⏳ Не опубликован"
    lines = [
        f"📌 <b>{post.title}</b>",
        "",
        post.text or "<i>(нет текста)</i>",
    ]
    if post.hashtags:
        lines.append("")
        lines.append(f"🏷 {post.hashtags}")
    if post.media:
        lines.append("")
        lines.append(f"📎 Медиа: {_media_summary(post.media)}")
    lines.extend([
        "",
        f"📊 Статус: {status}",
    ])
    if post.scheduled_at:
        scheduled_msk = post.scheduled_at
        if scheduled_msk.tzinfo is not None:
            scheduled_msk = scheduled_msk.astimezone(MSK)
        lines.append(f"🕐 Запланировано: {scheduled_msk.strftime('%d.%m.%Y %H:%M')} (МСК)")
    return "\n".join(lines)


def _media_step_kb(count: int = 0) -> InlineKeyboardMarkup:
    if count > 0:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"✅ Готово ({count} файл.)", callback_data="cp_media_done")],
        ])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏩ Пропустить", callback_data="cp_media_done")],
    ])


def _post_actions_kb(post_id: int, scheduled: bool = False) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"cp_edit:{post_id}")],
        [
            InlineKeyboardButton(text="🏷 Хештеги", callback_data=f"cp_add_ht:{post_id}"),
            InlineKeyboardButton(text="📎 Медиа", callback_data=f"cp_media:{post_id}"),
        ],
    ]
    if scheduled:
        buttons.append([
            InlineKeyboardButton(text="📤 Опубликовать", callback_data=f"cp_publish:{post_id}"),
            InlineKeyboardButton(text="❌ Отменить расписание", callback_data=f"cp_unschedule:{post_id}"),
        ])
    else:
        buttons.append([
            InlineKeyboardButton(text="📤 Опубликовать", callback_data=f"cp_publish:{post_id}"),
            InlineKeyboardButton(text="🕐 По расписанию", callback_data=f"cp_schedule:{post_id}"),
        ])
    buttons.extend([
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"cp_del:{post_id}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="cp_list")],
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# --- Menu ---

@router.callback_query(F.data == "content_plan")
async def content_plan_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(
        "📋 <b>Контент-план</b>\n\n"
        "Создавайте и управляйте своими постами:",
        parse_mode="HTML",
        reply_markup=content_plan_menu_kb(),
    )
    await callback.answer()


# --- Create ---

@router.callback_query(F.data == "cp_create")
async def cp_create_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(CreatePost.title)
    await callback.message.edit_text(
        "📝 <b>Новый пост</b>\n\n"
        "Введите заголовок поста:",
        parse_mode="HTML",
        reply_markup=back_kb("content_plan"),
    )
    await callback.answer()


@router.message(CreatePost.title)
async def cp_create_title(message: Message, state: FSMContext) -> None:
    await state.update_data(title=message.text)
    await state.set_state(CreatePost.text)
    await message.answer(
        "✍️ Введите текст поста:\n\n"
        "<i>Отправьте «-» чтобы пропустить</i>",
        parse_mode="HTML",
    )


@router.message(CreatePost.text)
async def cp_create_text(message: Message, state: FSMContext) -> None:
    text = message.text if message.text != "-" else None
    await state.update_data(text=text, pending_media=[])
    await state.set_state(CreatePost.media)
    await message.answer(
        "📎 <b>Прикрепите медиа</b>\n\n"
        "Отправьте фото, видео, документ, аудио, GIF, "
        "голосовое, кружок или стикер.\n\n"
        "<i>Можно отправить несколько файлов по очереди.</i>",
        parse_mode="HTML",
        reply_markup=_media_step_kb(0),
    )


@router.message(CreatePost.media)
async def cp_create_media_receive(message: Message, state: FSMContext) -> None:
    extracted = _extract_media(message)
    if not extracted:
        await message.answer(
            "⚠️ Отправьте медиафайл (фото, видео, документ и т.д.)\n"
            "или нажмите кнопку ниже.",
        )
        return
    file_id, media_type, file_name = extracted
    data = await state.get_data()
    pending = data.get("pending_media", [])
    pending.append({"file_id": file_id, "media_type": media_type, "file_name": file_name})
    await state.update_data(pending_media=pending)
    icon = MEDIA_TYPE_ICONS.get(media_type, "📎")
    label = MEDIA_TYPE_LABELS.get(media_type, media_type)
    await message.answer(
        f"{icon} {label.capitalize()} добавлено! (всего: {len(pending)})\n\n"
        "Отправьте ещё или нажмите «Готово».",
        reply_markup=_media_step_kb(len(pending)),
    )


@router.callback_query(CreatePost.media, F.data == "cp_media_done")
async def cp_create_media_done(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()

    user = await get_or_create_user(
        session, callback.from_user.id, callback.from_user.username, callback.from_user.full_name
    )
    post = ContentPlan(user_id=user.id, title=data["title"], text=data.get("text"))
    session.add(post)
    await session.flush()

    for m in data.get("pending_media", []):
        session.add(ContentPlanMedia(
            content_plan_id=post.id,
            file_id=m["file_id"],
            media_type=m["media_type"],
            file_name=m.get("file_name"),
        ))

    await session.commit()

    result = await session.execute(
        select(ContentPlan).options(selectinload(ContentPlan.media)).where(ContentPlan.id == post.id)
    )
    post = result.scalar_one()

    await state.clear()
    await callback.message.edit_text(
        f"✅ <b>Пост создан!</b>\n\n{_post_card(post)}",
        parse_mode="HTML",
        reply_markup=_post_actions_kb(post.id, scheduled=bool(post.scheduled_at)),
    )
    await callback.answer()


# --- Create from template ---

@router.callback_query(F.data == "cp_from_tpl")
async def cp_from_template_list(callback: CallbackQuery, session: AsyncSession) -> None:
    user = await get_or_create_user(
        session, callback.from_user.id, callback.from_user.username, callback.from_user.full_name
    )
    result = await session.execute(
        select(Template).where(Template.user_id == user.id).order_by(Template.created_at.desc())
    )
    templates = result.scalars().all()

    if not templates:
        await callback.message.edit_text(
            "📭 <b>У вас пока нет шаблонов.</b>\n\n"
            "Сначала создайте шаблон в разделе «Шаблоны».",
            parse_mode="HTML",
            reply_markup=content_plan_menu_kb(),
        )
        await callback.answer()
        return

    buttons = []
    for t in templates:
        buttons.append([InlineKeyboardButton(text=f"📝 {t.name}", callback_data=f"cp_use_tpl:{t.id}")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="content_plan")])

    await callback.message.edit_text(
        "📝 <b>Создать пост из шаблона</b>\n\n"
        "Выберите шаблон:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cp_use_tpl:"))
async def cp_use_template(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    tpl_id = int(callback.data.split(":")[1])
    result = await session.execute(select(Template).where(Template.id == tpl_id))
    tpl = result.scalar_one_or_none()
    if not tpl:
        await callback.answer("Шаблон не найден", show_alert=True)
        return

    variables = _find_variables(tpl.content)
    await state.update_data(tpl_content=tpl.content, variables=variables, var_index=0, filled={})
    await state.set_state(CreateFromTemplate.title)

    preview = tpl.content[:200] + ("..." if len(tpl.content) > 200 else "")
    await callback.message.edit_text(
        f"📝 <b>Шаблон: {tpl.name}</b>\n\n"
        f"<i>{preview}</i>\n\n"
        f"Введите заголовок для нового поста:",
        parse_mode="HTML",
        reply_markup=back_kb("cp_from_tpl"),
    )
    await callback.answer()


@router.message(CreateFromTemplate.title)
async def cp_tpl_title(message: Message, state: FSMContext) -> None:
    await state.update_data(title=message.text)
    data = await state.get_data()
    variables = data["variables"]

    if variables:
        var_name = variables[0]
        await state.set_state(CreateFromTemplate.fill_variable)
        await message.answer(
            f"🔤 <b>Заполните переменные</b>\n\n"
            f"Переменная <b>[1/{len(variables)}]</b>:\n"
            f"Введите значение для <code>{{{var_name}}}</code>:",
            parse_mode="HTML",
        )
    else:
        await state.update_data(text=data["tpl_content"], pending_media=[])
        await state.set_state(CreatePost.media)
        await message.answer(
            f"👀 <b>Предпросмотр:</b>\n\n{data['tpl_content']}\n\n"
            f"📎 <b>Прикрепите медиа</b> или нажмите «Пропустить»:",
            parse_mode="HTML",
            reply_markup=_media_step_kb(0),
        )


@router.message(CreateFromTemplate.fill_variable)
async def cp_tpl_fill_var(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    variables = data["variables"]
    var_index = data["var_index"]
    filled = data["filled"]

    filled[variables[var_index]] = message.text
    var_index += 1
    await state.update_data(filled=filled, var_index=var_index)

    if var_index < len(variables):
        var_name = variables[var_index]
        await message.answer(
            f"🔤 Переменная <b>[{var_index + 1}/{len(variables)}]</b>:\n"
            f"Введите значение для <code>{{{var_name}}}</code>:",
            parse_mode="HTML",
        )
    else:
        content = data["tpl_content"]
        for var, value in filled.items():
            content = content.replace(f"{{{var}}}", value)

        await state.update_data(text=content, pending_media=[])
        await state.set_state(CreatePost.media)
        await message.answer(
            f"👀 <b>Предпросмотр:</b>\n\n{content}\n\n"
            f"📎 <b>Прикрепите медиа</b> или нажмите «Пропустить»:",
            parse_mode="HTML",
            reply_markup=_media_step_kb(0),
        )


# --- List ---

async def _show_post_list(callback: CallbackQuery, session: AsyncSession, page: int = 0) -> None:
    user = await get_or_create_user(
        session, callback.from_user.id, callback.from_user.username, callback.from_user.full_name
    )
    result = await session.execute(
        select(ContentPlan)
        .options(selectinload(ContentPlan.media))
        .where(ContentPlan.user_id == user.id)
        .order_by(ContentPlan.created_at.desc())
    )
    all_posts = result.scalars().all()

    if not all_posts:
        await callback.message.edit_text(
            "📭 <b>У вас пока нет постов.</b>\n\n"
            "Нажмите «Создать пост», чтобы начать!",
            parse_mode="HTML",
            reply_markup=content_plan_menu_kb(),
        )
        await callback.answer()
        return

    posts, page, total = paginate(all_posts, page)
    buttons = []
    for p in posts:
        status = "✅ " if p.is_published else "📝 "
        buttons.append([InlineKeyboardButton(
            text=f"{status}{p.title}",
            callback_data=f"cp_view:{p.id}",
        )])
    if total > 1:
        buttons.append(pagination_row("cp_page", page, total))
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="content_plan")])

    await callback.message.edit_text(
        "📋 <b>Ваши посты:</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data == "cp_list")
async def cp_list(callback: CallbackQuery, session: AsyncSession) -> None:
    await _show_post_list(callback, session)


@router.callback_query(F.data.startswith("cp_page:"))
async def cp_list_page(callback: CallbackQuery, session: AsyncSession) -> None:
    page = int(callback.data.split(":")[1])
    await _show_post_list(callback, session, page)


# --- View ---

@router.callback_query(F.data.startswith("cp_view:"))
async def cp_view(callback: CallbackQuery, session: AsyncSession) -> None:
    post_id = int(callback.data.split(":")[1])
    result = await session.execute(select(ContentPlan).options(selectinload(ContentPlan.media)).where(ContentPlan.id == post_id))
    post = result.scalar_one_or_none()

    if not post:
        await callback.answer("Пост не найден", show_alert=True)
        return

    await callback.message.edit_text(
        _post_card(post), parse_mode="HTML",
        reply_markup=_post_actions_kb(post.id, scheduled=bool(post.scheduled_at)),
    )
    await callback.answer()


# --- Edit ---

CP_EDIT_FIELDS = [
    ("title", "Заголовок"),
    ("text", "Текст"),
    ("hashtags", "Хештеги"),
]


@router.callback_query(F.data.startswith("cp_edit:"))
async def cp_edit_start(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    post_id = int(callback.data.split(":")[1])
    result = await session.execute(select(ContentPlan).options(selectinload(ContentPlan.media)).where(ContentPlan.id == post_id))
    post = result.scalar_one_or_none()
    if not post:
        await callback.answer("Пост не найден", show_alert=True)
        return
    await callback.message.edit_text(
        f"✏️ <b>Редактирование поста</b>\n\n"
        f"📌 {post.title}\n\n"
        f"Выберите поле для изменения:",
        parse_mode="HTML",
        reply_markup=edit_fields_kb("cp", post.id, CP_EDIT_FIELDS),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cp_ef:"))
async def cp_edit_field(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    _, item_id, field = callback.data.split(":")
    await state.set_state(EditPost.value)
    await state.update_data(edit_id=int(item_id), edit_field=field)

    result = await session.execute(select(ContentPlan).options(selectinload(ContentPlan.media)).where(ContentPlan.id == int(item_id)))
    post = result.scalar_one_or_none()
    labels = dict(CP_EDIT_FIELDS)
    kb = back_kb(f"cp_edit:{item_id}")

    if field == "hashtags":
        current = post.hashtags if post and post.hashtags else "(нет)"
        await callback.message.edit_text(
            f"🏷 <b>Редактирование хештегов</b>\n\n"
            f"Текущие — нажмите, чтобы скопировать:\n"
            f"<code>{current}</code>\n\n"
            f"Введите новые хештеги через пробел\n"
            f"или «-» чтобы убрать все:\n\n"
            f"<i>Каждый хештег должен начинаться с #</i>",
            parse_mode="HTML",
            reply_markup=kb,
        )
    elif field == "text":
        current = post.text if post and post.text else "(нет текста)"
        await callback.message.edit_text(
            f"✏️ <b>Редактирование текста</b>\n\n"
            f"Текущий — нажмите, чтобы скопировать:\n"
            f"<code>{current}</code>\n\n"
            f"Введите новый текст:",
            parse_mode="HTML",
            reply_markup=kb,
        )
    elif field == "title":
        current = post.title if post else ""
        await callback.message.edit_text(
            f"✏️ <b>Редактирование заголовка</b>\n\n"
            f"Текущий — нажмите, чтобы скопировать:\n"
            f"<code>{current}</code>\n\n"
            f"Введите новый заголовок:",
            parse_mode="HTML",
            reply_markup=kb,
        )
    else:
        current = getattr(post, field, "") if post else ""
        await callback.message.edit_text(
            f"✏️ <b>Редактирование: {labels[field]}</b>\n\n"
            f"Текущее значение — нажмите, чтобы скопировать:\n"
            f"<code>{current}</code>\n\n"
            f"Введите новое значение:",
            parse_mode="HTML",
            reply_markup=kb,
        )
    await callback.answer()


@router.message(EditPost.value)
async def cp_edit_save(message: Message, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    post_id, field = data["edit_id"], data["edit_field"]
    result = await session.execute(select(ContentPlan).options(selectinload(ContentPlan.media)).where(ContentPlan.id == post_id))
    post = result.scalar_one_or_none()
    if not post:
        await state.clear()
        await message.answer("❌ Пост не найден.", reply_markup=content_plan_menu_kb())
        return

    if field == "hashtags":
        if message.text.strip() == "-":
            post.hashtags = None
        else:
            words = message.text.split()
            invalid = [w for w in words if not w.startswith("#")]
            if invalid:
                await message.answer(
                    f"⚠️ <b>Каждый хештег должен начинаться с #</b>\n\n"
                    f"Ошибка в: {', '.join(invalid)}\n\n"
                    f"Попробуйте ещё раз:",
                    parse_mode="HTML",
                )
                return
            post.hashtags = message.text
    else:
        setattr(post, field, message.text)

    await session.commit()
    await state.clear()
    await message.answer(
        f"✅ <b>Сохранено!</b>\n\n{_post_card(post)}",
        parse_mode="HTML",
        reply_markup=_post_actions_kb(post.id, scheduled=bool(post.scheduled_at)),
    )


# --- Hashtags ---

@router.callback_query(F.data.startswith("cp_add_ht:"))
async def cp_hashtags_menu(callback: CallbackQuery, session: AsyncSession) -> None:
    post_id = int(callback.data.split(":")[1])

    result = await session.execute(select(ContentPlan).options(selectinload(ContentPlan.media)).where(ContentPlan.id == post_id))
    post = result.scalar_one_or_none()
    if not post:
        await callback.answer("Пост не найден", show_alert=True)
        return

    user = await get_or_create_user(
        session, callback.from_user.id, callback.from_user.username, callback.from_user.full_name
    )
    ht_result = await session.execute(
        select(HashtagSet).where(HashtagSet.user_id == user.id).order_by(HashtagSet.created_at.desc())
    )
    sets = ht_result.scalars().all()

    buttons = []
    for s in sets:
        applied = _is_set_applied(post.hashtags, s.hashtags)
        flag = "✅" if applied else "➕"
        label = f"{flag} {s.name}" + (f" [{s.category}]" if s.category else "")
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"cp_toggle_ht:{post_id}:{s.id}")])
    buttons.append([InlineKeyboardButton(text="✍️ Написать свои", callback_data=f"cp_custom_ht:{post_id}")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"cp_view:{post_id}")])

    current = post.hashtags or "(нет)"
    await callback.message.edit_text(
        f"🏷 <b>Хештеги</b>\n\n"
        f"Текущие:\n<code>{current}</code>\n\n"
        f"➕ — добавить набор\n"
        f"✅ — убрать набор",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cp_toggle_ht:"))
async def cp_toggle_hashtag_set(callback: CallbackQuery, session: AsyncSession) -> None:
    parts = callback.data.split(":")
    post_id, set_id = int(parts[1]), int(parts[2])

    result = await session.execute(select(ContentPlan).options(selectinload(ContentPlan.media)).where(ContentPlan.id == post_id))
    post = result.scalar_one_or_none()
    if not post:
        await callback.answer("Пост не найден", show_alert=True)
        return

    ht_result = await session.execute(select(HashtagSet).where(HashtagSet.id == set_id))
    ht_set = ht_result.scalar_one_or_none()
    if not ht_set:
        await callback.answer("Набор не найден", show_alert=True)
        return

    if _is_set_applied(post.hashtags, ht_set.hashtags):
        post.hashtags = _remove_tags(post.hashtags, ht_set.hashtags)
        await callback.answer("✅ Набор убран")
    else:
        post.hashtags = _add_tags(post.hashtags, ht_set.hashtags)
        await callback.answer("➕ Набор добавлен")

    await session.commit()

    # Re-show hashtag menu with updated flags
    user = await get_or_create_user(
        session, callback.from_user.id, callback.from_user.username, callback.from_user.full_name
    )
    ht_all = await session.execute(
        select(HashtagSet).where(HashtagSet.user_id == user.id).order_by(HashtagSet.created_at.desc())
    )
    sets = ht_all.scalars().all()

    buttons = []
    for s in sets:
        applied = _is_set_applied(post.hashtags, s.hashtags)
        flag = "✅" if applied else "➕"
        label = f"{flag} {s.name}" + (f" [{s.category}]" if s.category else "")
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"cp_toggle_ht:{post_id}:{s.id}")])
    buttons.append([InlineKeyboardButton(text="✍️ Написать свои", callback_data=f"cp_custom_ht:{post_id}")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"cp_view:{post_id}")])

    current = post.hashtags or "(нет)"
    await callback.message.edit_text(
        f"🏷 <b>Хештеги</b>\n\n"
        f"Текущие:\n<code>{current}</code>\n\n"
        f"➕ — добавить набор\n"
        f"✅ — убрать набор",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data.startswith("cp_custom_ht:"))
async def cp_custom_hashtags_start(callback: CallbackQuery, state: FSMContext) -> None:
    post_id = int(callback.data.split(":")[1])
    await state.set_state(AddHashtags.custom)
    await state.update_data(post_id=post_id)
    await callback.message.edit_text(
        "✍️ <b>Свои хештеги</b>\n\n"
        "Введите хештеги через пробел:\n"
        "<i>Например: #маркетинг #smm #контент</i>\n\n"
        "<i>Каждый хештег должен начинаться с #</i>",
        parse_mode="HTML",
        reply_markup=back_kb(f"cp_add_ht:{post_id}"),
    )
    await callback.answer()


@router.message(AddHashtags.custom)
async def cp_custom_hashtags_save(message: Message, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    post_id = data["post_id"]

    words = message.text.split()
    invalid = [w for w in words if not w.startswith("#")]
    if invalid:
        await message.answer(
            f"⚠️ <b>Каждый хештег должен начинаться с #</b>\n\n"
            f"Ошибка в: {', '.join(invalid)}\n\n"
            f"Попробуйте ещё раз:",
            parse_mode="HTML",
        )
        return

    result = await session.execute(select(ContentPlan).options(selectinload(ContentPlan.media)).where(ContentPlan.id == post_id))
    post = result.scalar_one_or_none()
    if not post:
        await state.clear()
        await message.answer("❌ Пост не найден.", reply_markup=content_plan_menu_kb())
        return

    post.hashtags = _add_tags(post.hashtags, message.text)
    await session.commit()
    await state.clear()

    await message.answer(
        f"✅ <b>Хештеги добавлены!</b>\n\n{_post_card(post)}",
        parse_mode="HTML",
        reply_markup=_post_actions_kb(post.id, scheduled=bool(post.scheduled_at)),
    )


# --- Media management ---

@router.callback_query(F.data.startswith("cp_media:"))
async def cp_media_menu(callback: CallbackQuery, session: AsyncSession) -> None:
    post_id = int(callback.data.split(":")[1])
    result = await session.execute(
        select(ContentPlan).options(selectinload(ContentPlan.media)).where(ContentPlan.id == post_id)
    )
    post = result.scalar_one_or_none()
    if not post:
        await callback.answer("Пост не найден", show_alert=True)
        return

    buttons = []
    if post.media:
        for m in post.media:
            icon = MEDIA_TYPE_ICONS.get(m.media_type, "📎")
            label = MEDIA_TYPE_LABELS.get(m.media_type, m.media_type)
            name = m.file_name or label
            buttons.append([
                InlineKeyboardButton(text=f"{icon} {name}", callback_data=f"cp_media_show:{m.id}"),
                InlineKeyboardButton(text="🗑", callback_data=f"cp_media_rm:{post_id}:{m.id}"),
            ])
    buttons.append([InlineKeyboardButton(text="➕ Добавить медиа", callback_data=f"cp_media_add:{post_id}")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"cp_view:{post_id}")])

    count_text = _media_summary(post.media) if post.media else "нет вложений"
    await callback.message.edit_text(
        f"📎 <b>Медиа поста</b> «{post.title}»\n\n"
        f"Вложения: {count_text}\n\n"
        f"Нажмите на файл, чтобы просмотреть.\n"
        f"🗑 — удалить файл.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cp_media_show:"))
async def cp_media_show(callback: CallbackQuery, session: AsyncSession) -> None:
    media_id = int(callback.data.split(":")[1])
    result = await session.execute(select(ContentPlanMedia).where(ContentPlanMedia.id == media_id))
    media = result.scalar_one_or_none()
    if not media:
        await callback.answer("Файл не найден", show_alert=True)
        return

    send = callback.message.answer_photo
    if media.media_type == "photo":
        send = callback.message.answer_photo
    elif media.media_type == "video":
        send = callback.message.answer_video
    elif media.media_type == "document":
        send = callback.message.answer_document
    elif media.media_type == "audio":
        send = callback.message.answer_audio
    elif media.media_type == "animation":
        send = callback.message.answer_animation
    elif media.media_type == "voice":
        send = callback.message.answer_voice
    elif media.media_type == "video_note":
        send = callback.message.answer_video_note
    elif media.media_type == "sticker":
        send = callback.message.answer_sticker

    await send(media.file_id)
    await callback.answer()


@router.callback_query(F.data.startswith("cp_media_rm:"))
async def cp_media_remove(callback: CallbackQuery, session: AsyncSession) -> None:
    parts = callback.data.split(":")
    post_id, media_id = int(parts[1]), int(parts[2])
    result = await session.execute(select(ContentPlanMedia).where(ContentPlanMedia.id == media_id))
    media = result.scalar_one_or_none()
    if media:
        await session.delete(media)
        await session.commit()
    await callback.answer("✅ Файл удалён")

    # Re-show media menu
    result = await session.execute(
        select(ContentPlan).options(selectinload(ContentPlan.media)).where(ContentPlan.id == post_id)
    )
    post = result.scalar_one_or_none()
    if not post:
        return

    buttons = []
    if post.media:
        for m in post.media:
            icon = MEDIA_TYPE_ICONS.get(m.media_type, "📎")
            label = MEDIA_TYPE_LABELS.get(m.media_type, m.media_type)
            name = m.file_name or label
            buttons.append([
                InlineKeyboardButton(text=f"{icon} {name}", callback_data=f"cp_media_show:{m.id}"),
                InlineKeyboardButton(text="🗑", callback_data=f"cp_media_rm:{post_id}:{m.id}"),
            ])
    buttons.append([InlineKeyboardButton(text="➕ Добавить медиа", callback_data=f"cp_media_add:{post_id}")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"cp_view:{post_id}")])

    count_text = _media_summary(post.media) if post.media else "нет вложений"
    await callback.message.edit_text(
        f"📎 <b>Медиа поста</b> «{post.title}»\n\n"
        f"Вложения: {count_text}\n\n"
        f"Нажмите на файл, чтобы просмотреть.\n"
        f"🗑 — удалить файл.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data.startswith("cp_media_add:"))
async def cp_media_add_start(callback: CallbackQuery, state: FSMContext) -> None:
    post_id = int(callback.data.split(":")[1])
    await state.set_state(AddMedia.waiting)
    await state.update_data(media_post_id=post_id)
    await callback.message.edit_text(
        "📎 <b>Добавление медиа</b>\n\n"
        "Отправьте файл (фото, видео, документ, аудио, GIF, "
        "голосовое, кружок или стикер).\n\n"
        "Нажмите «Готово» когда закончите.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Готово", callback_data=f"cp_media_add_done:{post_id}")],
        ]),
    )
    await callback.answer()


@router.message(AddMedia.waiting)
async def cp_media_add_receive(message: Message, state: FSMContext, session: AsyncSession) -> None:
    extracted = _extract_media(message)
    if not extracted:
        await message.answer("⚠️ Отправьте медиафайл или нажмите «Готово».")
        return

    file_id, media_type, file_name = extracted
    data = await state.get_data()
    post_id = data["media_post_id"]

    session.add(ContentPlanMedia(
        content_plan_id=post_id,
        file_id=file_id,
        media_type=media_type,
        file_name=file_name,
    ))
    await session.commit()

    icon = MEDIA_TYPE_ICONS.get(media_type, "📎")
    label = MEDIA_TYPE_LABELS.get(media_type, media_type)
    await message.answer(
        f"{icon} {label.capitalize()} добавлено!\n"
        "Отправьте ещё или нажмите «Готово».",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Готово", callback_data=f"cp_media_add_done:{post_id}")],
        ]),
    )


@router.callback_query(F.data.startswith("cp_media_add_done:"))
async def cp_media_add_done(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    post_id = int(callback.data.split(":")[1])
    await state.clear()

    result = await session.execute(
        select(ContentPlan).options(selectinload(ContentPlan.media)).where(ContentPlan.id == post_id)
    )
    post = result.scalar_one_or_none()
    if not post:
        await callback.answer("Пост не найден", show_alert=True)
        return

    await callback.message.edit_text(
        _post_card(post),
        parse_mode="HTML",
        reply_markup=_post_actions_kb(post.id, scheduled=bool(post.scheduled_at)),
    )
    await callback.answer()


# --- Schedule ---

DEFAULT_PRESETS = [
    {"name": "⏱ Через 1 час", "preset_type": "hours", "hours": 1, "sort_order": 0},
    {"name": "⏱ Через 3 часа", "preset_type": "hours", "hours": 3, "sort_order": 1},
    {"name": "🌅 Завтра 10:00", "preset_type": "days", "days": 1, "hour": 10, "minute": 0, "sort_order": 2},
    {"name": "🌇 Завтра 18:00", "preset_type": "days", "days": 1, "hour": 18, "minute": 0, "sort_order": 3},
]


async def _get_or_create_presets(session: AsyncSession, user_id: int) -> list[SchedulePreset]:
    result = await session.execute(
        select(SchedulePreset).where(SchedulePreset.user_id == user_id).order_by(SchedulePreset.sort_order)
    )
    presets = result.scalars().all()
    if not presets:
        for p in DEFAULT_PRESETS:
            session.add(SchedulePreset(user_id=user_id, **p))
        await session.commit()
        result = await session.execute(
            select(SchedulePreset).where(SchedulePreset.user_id == user_id).order_by(SchedulePreset.sort_order)
        )
        presets = result.scalars().all()
    return list(presets)


def _preset_to_date(preset: SchedulePreset) -> datetime:
    now = datetime.now(MSK)
    if preset.preset_type == "hours":
        return now + timedelta(hours=preset.hours)
    else:
        target = now + timedelta(days=preset.days)
        return target.replace(hour=preset.hour, minute=preset.minute, second=0, microsecond=0)


def _preset_label(p: SchedulePreset) -> str:
    """Генерирует человекочитаемое название пресета."""
    if p.preset_type == "hours":
        return f"⏱ Через {p.hours} ч."
    days_labels = {0: "Сегодня", 1: "Завтра", 2: "Послезавтра"}
    day = days_labels.get(p.days, f"Через {p.days} дн.")
    return f"📅 {day} {p.hour:02d}:{p.minute:02d}"


async def _show_schedule_time(callback_or_msg, session, user_id, post_id, channel_id, state):
    presets = await _get_or_create_presets(session, user_id)
    now = datetime.now(MSK)

    buttons = []
    row = []
    for p in presets:
        row.append(InlineKeyboardButton(
            text=p.name or _preset_label(p),
            callback_data=f"cp_sched_use:{post_id}:{channel_id}:{p.id}",
        ))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([InlineKeyboardButton(text="⚙️ Настроить шаблоны", callback_data=f"cp_presets:{post_id}:{channel_id}")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data=f"cp_view:{post_id}")])

    text = (
        "🕐 <b>Выберите время публикации</b>\n\n"
        f"Сейчас: {now.strftime('%d.%m.%Y %H:%M')} (МСК)\n\n"
        "Выберите шаблон или введите\n"
        "дату и время в формате <code>ДД.ММ.ГГГГ ЧЧ:ММ</code>"
    )

    if hasattr(callback_or_msg, 'message'):
        await callback_or_msg.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    else:
        await callback_or_msg.answer(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data.startswith("cp_schedule:"))
async def cp_schedule_start(callback: CallbackQuery, session: AsyncSession) -> None:
    post_id = int(callback.data.split(":")[1])

    user = await get_or_create_user(
        session, callback.from_user.id, callback.from_user.username, callback.from_user.full_name
    )
    result = await session.execute(
        select(Channel).where(Channel.user_id == user.id).order_by(Channel.platform, Channel.created_at.desc())
    )
    channels = result.scalars().all()

    if not channels:
        await callback.answer(
            "⚠️ Нет подключённых каналов.\nПодключите канал в разделе «Каналы».",
            show_alert=True,
        )
        return

    by_platform: dict[str, list] = {}
    for ch in channels:
        by_platform.setdefault(ch.platform, []).append(ch)

    buttons = []
    platform_headers = {"telegram": "📱 Telegram", "instagram": "📷 Instagram"}

    for platform in ("telegram", "instagram"):
        header = platform_headers.get(platform, platform)
        chs = by_platform.get(platform, [])
        buttons.append([InlineKeyboardButton(text=f"— {header} —", callback_data="noop")])
        if chs:
            for ch in chs:
                label = f"📢 {ch.title}"
                if ch.username:
                    label += f" (@{ch.username})"
                buttons.append([InlineKeyboardButton(text=label, callback_data=f"cp_sched_ch:{post_id}:{ch.id}")])
        else:
            buttons.append([InlineKeyboardButton(text="  нет подключённых", callback_data="noop")])

    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data=f"cp_view:{post_id}")])

    await callback.message.edit_text(
        "🕐 <b>Публикация по расписанию</b>\n\nВыберите канал:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cp_sched_ch:"))
async def cp_schedule_channel(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    parts = callback.data.split(":")
    post_id, channel_id = int(parts[1]), int(parts[2])
    await state.set_state(SchedulePost.datetime_input)
    await state.update_data(sched_post_id=post_id, sched_channel_id=channel_id)

    user = await get_or_create_user(
        session, callback.from_user.id, callback.from_user.username, callback.from_user.full_name
    )
    await _show_schedule_time(callback, session, user.id, post_id, channel_id, state)
    await callback.answer()


@router.callback_query(F.data.startswith("cp_sched_use:"))
async def cp_schedule_use_preset(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    parts = callback.data.split(":")
    post_id, channel_id, preset_id = int(parts[1]), int(parts[2]), int(parts[3])

    result = await session.execute(select(SchedulePreset).where(SchedulePreset.id == preset_id))
    preset = result.scalar_one_or_none()
    if not preset:
        await state.clear()
        await callback.answer("Шаблон не найден", show_alert=True)
        return

    run_date = _preset_to_date(preset)
    if run_date <= datetime.now(MSK):
        await callback.answer("⚠️ Это время уже прошло. Выберите другой шаблон.", show_alert=True)
        return

    await _save_schedule(callback, state, session, post_id, channel_id, run_date)


@router.message(SchedulePost.datetime_input)
async def cp_schedule_manual(message: Message, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    post_id = data["sched_post_id"]
    channel_id = data["sched_channel_id"]

    try:
        run_date = datetime.strptime(message.text.strip(), "%d.%m.%Y %H:%M")
        run_date = run_date.replace(tzinfo=MSK)
    except ValueError:
        await message.answer(
            "⚠️ Неверный формат. Введите дату в формате:\n"
            "<code>ДД.ММ.ГГГГ ЧЧ:ММ</code>\n\n"
            "Например: <code>25.04.2026 14:30</code>",
            parse_mode="HTML",
        )
        return

    if run_date <= datetime.now(MSK):
        await message.answer("⚠️ Дата должна быть в будущем. Попробуйте ещё раз.")
        return

    await _save_schedule_msg(message, state, session, post_id, channel_id, run_date)


async def _save_schedule(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession,
    post_id: int, channel_id: int, run_date: datetime,
) -> None:
    result = await session.execute(
        select(ContentPlan).options(selectinload(ContentPlan.media)).where(ContentPlan.id == post_id)
    )
    post = result.scalar_one_or_none()
    if not post:
        await callback.answer("Пост не найден", show_alert=True)
        return
    ch_result = await session.execute(select(Channel).where(Channel.id == channel_id))
    channel = ch_result.scalar_one_or_none()
    if not channel:
        await callback.answer("Канал не найден", show_alert=True)
        return

    post.scheduled_at = run_date
    post.scheduled_channel_id = channel_id
    await session.commit()

    from app.bot import scheduler
    scheduler.add_job(
        scheduled_publish_job, "date", run_date=run_date,
        args=[callback.bot, post_id, channel_id],
        id=f"publish_{post_id}", replace_existing=True,
    )

    await state.clear()
    await callback.message.edit_text(
        f"✅ <b>Пост запланирован!</b>\n\n"
        f"🕐 {run_date.strftime('%d.%m.%Y %H:%M')} (МСК)\n"
        f"📢 Канал: {channel.title}\n\n{_post_card(post)}",
        parse_mode="HTML",
        reply_markup=_post_actions_kb(post.id, scheduled=True),
    )
    await callback.answer()


async def _save_schedule_msg(
    message: Message, state: FSMContext, session: AsyncSession,
    post_id: int, channel_id: int, run_date: datetime,
) -> None:
    result = await session.execute(
        select(ContentPlan).options(selectinload(ContentPlan.media)).where(ContentPlan.id == post_id)
    )
    post = result.scalar_one_or_none()
    if not post:
        await state.clear()
        await message.answer("❌ Пост не найден.", reply_markup=content_plan_menu_kb())
        return
    ch_result = await session.execute(select(Channel).where(Channel.id == channel_id))
    channel = ch_result.scalar_one_or_none()
    if not channel:
        await state.clear()
        await message.answer("❌ Канал не найден.", reply_markup=content_plan_menu_kb())
        return

    post.scheduled_at = run_date
    post.scheduled_channel_id = channel_id
    await session.commit()

    from app.bot import scheduler
    scheduler.add_job(
        scheduled_publish_job, "date", run_date=run_date,
        args=[message.bot, post_id, channel_id],
        id=f"publish_{post_id}", replace_existing=True,
    )

    await state.clear()
    await message.answer(
        f"✅ <b>Пост запланирован!</b>\n\n"
        f"🕐 {run_date.strftime('%d.%m.%Y %H:%M')} (МСК)\n"
        f"📢 Канал: {channel.title}\n\n{_post_card(post)}",
        parse_mode="HTML",
        reply_markup=_post_actions_kb(post.id, scheduled=True),
    )


@router.callback_query(F.data.startswith("cp_unschedule:"))
async def cp_unschedule(callback: CallbackQuery, session: AsyncSession) -> None:
    post_id = int(callback.data.split(":")[1])
    result = await session.execute(
        select(ContentPlan).options(selectinload(ContentPlan.media)).where(ContentPlan.id == post_id)
    )
    post = result.scalar_one_or_none()
    if not post:
        await callback.answer("Пост не найден", show_alert=True)
        return

    post.scheduled_at = None
    post.scheduled_channel_id = None
    await session.commit()

    from app.bot import scheduler
    try:
        scheduler.remove_job(f"publish_{post_id}")
    except Exception:
        pass

    await callback.message.edit_text(
        f"✅ <b>Расписание отменено</b>\n\n{_post_card(post)}",
        parse_mode="HTML",
        reply_markup=_post_actions_kb(post.id, scheduled=False),
    )
    await callback.answer()


# --- Preset management ---

@router.callback_query(F.data.startswith("cp_presets:"))
async def cp_presets_menu(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    parts = callback.data.split(":")
    post_id, channel_id = int(parts[1]), int(parts[2])
    await state.update_data(sched_post_id=post_id, sched_channel_id=channel_id)

    user = await get_or_create_user(
        session, callback.from_user.id, callback.from_user.username, callback.from_user.full_name
    )
    presets = await _get_or_create_presets(session, user.id)

    buttons = []
    for p in presets:
        label = p.name or _preset_label(p)
        buttons.append([
            InlineKeyboardButton(text=label, callback_data="noop"),
            InlineKeyboardButton(text="🗑", callback_data=f"cp_preset_del:{p.id}:{post_id}:{channel_id}"),
        ])
    buttons.append([InlineKeyboardButton(text="➕ Добавить шаблон", callback_data=f"cp_preset_add:{post_id}:{channel_id}")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад к выбору времени", callback_data=f"cp_sched_ch:{post_id}:{channel_id}")])

    await callback.message.edit_text(
        "⚙️ <b>Шаблоны времени</b>\n\n"
        "Ваши шаблоны для быстрого выбора.\n"
        "🗑 — удалить шаблон.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cp_preset_del:"))
async def cp_preset_delete(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    parts = callback.data.split(":")
    preset_id, post_id, channel_id = int(parts[1]), int(parts[2]), int(parts[3])

    result = await session.execute(select(SchedulePreset).where(SchedulePreset.id == preset_id))
    preset = result.scalar_one_or_none()
    if preset:
        await session.delete(preset)
        await session.commit()
    await callback.answer("✅ Шаблон удалён")

    # Re-show presets menu
    callback.data = f"cp_presets:{post_id}:{channel_id}"
    await cp_presets_menu(callback, state, session)


@router.callback_query(F.data.startswith("cp_preset_add:"))
async def cp_preset_add_start(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    post_id, channel_id = int(parts[1]), int(parts[2])
    await state.update_data(sched_post_id=post_id, sched_channel_id=channel_id)

    await callback.message.edit_text(
        "➕ <b>Новый шаблон</b>\n\n"
        "Выберите тип:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⏱ Через N часов", callback_data=f"cp_preset_type:hours:{post_id}:{channel_id}")],
            [InlineKeyboardButton(text="📅 Через N дней в ЧЧ:ММ", callback_data=f"cp_preset_type:days:{post_id}:{channel_id}")],
            [InlineKeyboardButton(text="◀️ Назад", callback_data=f"cp_presets:{post_id}:{channel_id}")],
        ]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cp_preset_type:"))
async def cp_preset_type(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    ptype, post_id, channel_id = parts[1], int(parts[2]), int(parts[3])
    await state.update_data(preset_type=ptype, sched_post_id=post_id, sched_channel_id=channel_id)

    if ptype == "hours":
        await state.set_state(CreatePreset.hours)
        await callback.message.edit_text(
            "⏱ <b>Через сколько часов?</b>\n\n"
            "Введите число (например: <code>2</code> или <code>6</code>):",
            parse_mode="HTML",
        )
    else:
        await state.set_state(CreatePreset.days)
        await callback.message.edit_text(
            "📅 <b>Через сколько дней?</b>\n\n"
            "Введите число:\n"
            "<code>0</code> — сегодня\n"
            "<code>1</code> — завтра\n"
            "<code>2</code> — послезавтра\n"
            "<code>3</code> — через 3 дня\n"
            "и т.д.",
            parse_mode="HTML",
        )
    await callback.answer()


@router.message(CreatePreset.hours)
async def cp_preset_hours(message: Message, state: FSMContext, session: AsyncSession) -> None:
    try:
        hours = int(message.text.strip())
        if hours < 1 or hours > 168:
            raise ValueError
    except ValueError:
        await message.answer("⚠️ Введите число от 1 до 168.")
        return

    data = await state.get_data()
    user = await get_or_create_user(
        session, message.from_user.id, message.from_user.username, message.from_user.full_name
    )

    name = f"⏱ Через {hours} ч."
    session.add(SchedulePreset(
        user_id=user.id, name=name, preset_type="hours", hours=hours, sort_order=hours,
    ))
    await session.commit()
    await state.set_state(SchedulePost.datetime_input)

    post_id = data["sched_post_id"]
    channel_id = data["sched_channel_id"]
    await message.answer(f"✅ Шаблон «{name}» добавлен!")
    await _show_schedule_time(message, session, user.id, post_id, channel_id, state)


@router.message(CreatePreset.days)
async def cp_preset_days(message: Message, state: FSMContext) -> None:
    try:
        days = int(message.text.strip())
        if days < 0 or days > 30:
            raise ValueError
    except ValueError:
        await message.answer("⚠️ Введите число от 0 до 30.")
        return

    await state.update_data(preset_days=days)
    await state.set_state(CreatePreset.time)
    await message.answer(
        "🕐 <b>Во сколько?</b>\n\n"
        "Введите время в формате <code>ЧЧ:ММ</code>\n"
        "Например: <code>10:00</code> или <code>14:30</code>",
        parse_mode="HTML",
    )


@router.message(CreatePreset.time)
async def cp_preset_time(message: Message, state: FSMContext, session: AsyncSession) -> None:
    try:
        parts = message.text.strip().split(":")
        hour, minute = int(parts[0]), int(parts[1])
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except (ValueError, IndexError):
        await message.answer("⚠️ Введите время в формате <code>ЧЧ:ММ</code>", parse_mode="HTML")
        return

    data = await state.get_data()
    days = data["preset_days"]
    user = await get_or_create_user(
        session, message.from_user.id, message.from_user.username, message.from_user.full_name
    )

    days_labels = {0: "Сегодня", 1: "Завтра", 2: "Послезавтра"}
    day_label = days_labels.get(days, f"Через {days} дн.")
    name = f"📅 {day_label} {hour:02d}:{minute:02d}"

    session.add(SchedulePreset(
        user_id=user.id, name=name, preset_type="days",
        days=days, hour=hour, minute=minute, sort_order=100 + days * 100 + hour,
    ))
    await session.commit()
    await state.set_state(SchedulePost.datetime_input)

    post_id = data["sched_post_id"]
    channel_id = data["sched_channel_id"]
    await message.answer(f"✅ Шаблон «{name}» добавлен!")
    await _show_schedule_time(message, session, user.id, post_id, channel_id, state)


# --- Publish ---

@router.callback_query(F.data.startswith("cp_publish:"))
async def cp_publish_start(callback: CallbackQuery, session: AsyncSession) -> None:
    post_id = int(callback.data.split(":")[1])

    user = await get_or_create_user(
        session, callback.from_user.id, callback.from_user.username, callback.from_user.full_name
    )
    result = await session.execute(
        select(Channel).where(Channel.user_id == user.id).order_by(Channel.platform, Channel.created_at.desc())
    )
    channels = result.scalars().all()

    if not channels:
        await callback.answer(
            "⚠️ Нет подключённых каналов.\nПодключите канал в разделе «Каналы».",
            show_alert=True,
        )
        return

    # Группируем каналы по платформе
    by_platform: dict[str, list] = {}
    for ch in channels:
        by_platform.setdefault(ch.platform, []).append(ch)

    buttons = []
    platform_headers = {
        "telegram": "📱 Telegram",
        "instagram": "📷 Instagram",
    }

    for platform in ("telegram", "instagram"):
        header = platform_headers.get(platform, platform)
        chs = by_platform.get(platform, [])
        buttons.append([InlineKeyboardButton(text=f"— {header} —", callback_data="noop")])
        if chs:
            for ch in chs:
                label = f"📢 {ch.title}"
                if ch.username:
                    label += f" (@{ch.username})"
                buttons.append([InlineKeyboardButton(
                    text=label,
                    callback_data=f"cp_do_publish:{post_id}:{ch.id}",
                )])
        else:
            buttons.append([InlineKeyboardButton(text="  нет подключённых", callback_data="noop")])

    buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data=f"cp_view:{post_id}")])

    await callback.message.edit_text(
        "📤 <b>Публикация поста</b>\n\n"
        "Выберите канал для публикации:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cp_do_publish:"))
async def cp_do_publish(callback: CallbackQuery, session: AsyncSession, bot: Bot) -> None:
    parts = callback.data.split(":")
    post_id, channel_db_id = int(parts[1]), int(parts[2])

    result = await session.execute(
        select(ContentPlan).options(selectinload(ContentPlan.media)).where(ContentPlan.id == post_id)
    )
    post = result.scalar_one_or_none()
    if not post:
        await callback.answer("Пост не найден", show_alert=True)
        return

    ch_result = await session.execute(select(Channel).where(Channel.id == channel_db_id))
    channel = ch_result.scalar_one_or_none()
    if not channel:
        await callback.answer("Канал не найден", show_alert=True)
        return

    try:
        await publish_post(bot, post, channel.channel_id)
        post.is_published = True
        await session.commit()

        await callback.message.edit_text(
            f"✅ <b>Пост опубликован!</b>\n\n"
            f"📢 Канал: {channel.title}\n\n"
            f"{_post_card(post)}",
            parse_mode="HTML",
            reply_markup=_post_actions_kb(post.id, scheduled=bool(post.scheduled_at)),
        )
        await callback.answer("✅ Опубликовано!")
    except Exception as e:
        logging.exception("Publish failed")
        await callback.answer(
            f"❌ Ошибка публикации: {str(e)[:150]}",
            show_alert=True,
        )


# --- Delete ---

@router.callback_query(F.data.startswith("cp_del:"))
async def cp_delete_ask(callback: CallbackQuery, session: AsyncSession) -> None:
    post_id = int(callback.data.split(":")[1])
    result = await session.execute(select(ContentPlan).options(selectinload(ContentPlan.media)).where(ContentPlan.id == post_id))
    post = result.scalar_one_or_none()
    if not post:
        await callback.answer("Пост не найден", show_alert=True)
        return
    await callback.message.edit_text(
        f"🗑 <b>Удаление поста</b>\n\n"
        f"Вы уверены, что хотите удалить пост «{post.title}»?",
        parse_mode="HTML",
        reply_markup=confirm_delete_kb("cp", post.id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cp_confirm_del:"))
async def cp_delete_confirm(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    post_id = int(callback.data.split(":")[1])
    result = await session.execute(select(ContentPlan).options(selectinload(ContentPlan.media)).where(ContentPlan.id == post_id))
    post = result.scalar_one_or_none()
    if post:
        await session.delete(post)
        await session.commit()
    await callback.answer("✅ Удалено")
    await content_plan_menu(callback, state)
