import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import ContentPlan, HashtagSet, Template
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

PLATFORMS = {
    "cp_platform_telegram": "telegram",
    "cp_platform_instagram": "instagram",
}

PLATFORM_LABELS = {"telegram": "Telegram", "instagram": "Instagram"}
PLATFORM_ICONS = {"telegram": "📱", "instagram": "📷"}


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
    platform = State()


class EditPost(StatesGroup):
    value = State()


class AddHashtags(StatesGroup):
    custom = State()


class CreateFromTemplate(StatesGroup):
    title = State()
    fill_variable = State()


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
    platform = PLATFORM_LABELS.get(post.platform, post.platform)
    icon = PLATFORM_ICONS.get(post.platform, "📱")
    status = "✅ Опубликован" if post.is_published else "⏳ Не опубликован"
    lines = [
        f"📌 <b>{post.title}</b>",
        "",
        post.text or "<i>(нет текста)</i>",
    ]
    if post.hashtags:
        lines.append("")
        lines.append(f"🏷 {post.hashtags}")
    lines.extend([
        "",
        f"{icon} Платформа: {platform}",
        f"📊 Статус: {status}",
    ])
    if post.scheduled_at:
        lines.append(f"🕐 Запланировано: {post.scheduled_at.strftime('%d.%m.%Y %H:%M')}")
    return "\n".join(lines)


def _platform_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 Telegram", callback_data="cp_platform_telegram")],
        [InlineKeyboardButton(text="📷 Instagram", callback_data="cp_platform_instagram")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="content_plan")],
    ])


def _post_actions_kb(post_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Редактировать", callback_data=f"cp_edit:{post_id}")],
        [InlineKeyboardButton(text="🏷 Хештеги", callback_data=f"cp_add_ht:{post_id}")],
        [InlineKeyboardButton(text="📤 Опубликовать", callback_data=f"cp_publish:{post_id}")],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"cp_del:{post_id}")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="cp_list")],
    ])


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
    await state.update_data(text=text)
    await state.set_state(CreatePost.platform)
    await message.answer("🌐 Выберите платформу:", reply_markup=_platform_kb())


@router.callback_query(CreatePost.platform, F.data.in_(PLATFORMS))
async def cp_create_platform(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    platform = PLATFORMS[callback.data]

    user = await get_or_create_user(
        session, callback.from_user.id, callback.from_user.username, callback.from_user.full_name
    )
    post = ContentPlan(user_id=user.id, title=data["title"], text=data.get("text"), platform=platform)
    session.add(post)
    await session.commit()
    await session.refresh(post)

    await state.clear()
    await callback.message.edit_text(
        f"✅ <b>Пост создан!</b>\n\n{_post_card(post)}",
        parse_mode="HTML",
        reply_markup=_post_actions_kb(post.id),
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
        await state.update_data(text=data["tpl_content"])
        await state.set_state(CreatePost.platform)
        await message.answer(
            f"👀 <b>Предпросмотр:</b>\n\n{data['tpl_content']}\n\n"
            f"🌐 Выберите платформу:",
            parse_mode="HTML",
            reply_markup=_platform_kb(),
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

        await state.update_data(text=content)
        await state.set_state(CreatePost.platform)
        await message.answer(
            f"👀 <b>Предпросмотр:</b>\n\n{content}\n\n"
            f"🌐 Выберите платформу:",
            parse_mode="HTML",
            reply_markup=_platform_kb(),
        )


# --- List ---

async def _show_post_list(callback: CallbackQuery, session: AsyncSession, page: int = 0) -> None:
    user = await get_or_create_user(
        session, callback.from_user.id, callback.from_user.username, callback.from_user.full_name
    )
    result = await session.execute(
        select(ContentPlan)
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
        icon = PLATFORM_ICONS.get(p.platform, "📱")
        buttons.append([InlineKeyboardButton(
            text=f"{status}{p.title} {icon}",
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
    result = await session.execute(select(ContentPlan).where(ContentPlan.id == post_id))
    post = result.scalar_one_or_none()

    if not post:
        await callback.answer("Пост не найден", show_alert=True)
        return

    await callback.message.edit_text(
        _post_card(post), parse_mode="HTML",
        reply_markup=_post_actions_kb(post.id),
    )
    await callback.answer()


# --- Edit ---

CP_EDIT_FIELDS = [
    ("title", "Заголовок"),
    ("text", "Текст"),
    ("hashtags", "Хештеги"),
    ("platform", "Платформа"),
]


@router.callback_query(F.data.startswith("cp_edit:"))
async def cp_edit_start(callback: CallbackQuery, session: AsyncSession) -> None:
    post_id = int(callback.data.split(":")[1])
    result = await session.execute(select(ContentPlan).where(ContentPlan.id == post_id))
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

    result = await session.execute(select(ContentPlan).where(ContentPlan.id == int(item_id)))
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
    result = await session.execute(select(ContentPlan).where(ContentPlan.id == post_id))
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
        reply_markup=_post_actions_kb(post.id),
    )


# --- Hashtags ---

@router.callback_query(F.data.startswith("cp_add_ht:"))
async def cp_hashtags_menu(callback: CallbackQuery, session: AsyncSession) -> None:
    post_id = int(callback.data.split(":")[1])

    result = await session.execute(select(ContentPlan).where(ContentPlan.id == post_id))
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

    result = await session.execute(select(ContentPlan).where(ContentPlan.id == post_id))
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

    result = await session.execute(select(ContentPlan).where(ContentPlan.id == post_id))
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
        reply_markup=_post_actions_kb(post.id),
    )


# --- Publish (stub) ---

@router.callback_query(F.data.startswith("cp_publish:"))
async def cp_publish(callback: CallbackQuery) -> None:
    await callback.answer(
        "🚧 Публикация пока не доступна.\nФункция в разработке!",
        show_alert=True,
    )


# --- Delete ---

@router.callback_query(F.data.startswith("cp_del:"))
async def cp_delete_ask(callback: CallbackQuery, session: AsyncSession) -> None:
    post_id = int(callback.data.split(":")[1])
    result = await session.execute(select(ContentPlan).where(ContentPlan.id == post_id))
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
    result = await session.execute(select(ContentPlan).where(ContentPlan.id == post_id))
    post = result.scalar_one_or_none()
    if post:
        await session.delete(post)
        await session.commit()
    await callback.answer("✅ Удалено")
    await content_plan_menu(callback, state)
