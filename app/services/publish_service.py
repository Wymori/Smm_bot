"""Сервис публикации постов в Telegram-каналы.

Группирует медиа по совместимым типам согласно ограничениям Telegram API:
- Фото + Видео → одна медиагруппа (альбом)
- Документы → отдельная медиагруппа
- Аудио → отдельная медиагруппа
- GIF, голосовые, кружки, стикеры → каждый отдельным сообщением
"""
import logging

from aiogram import Bot
from aiogram.types import (
    InputMediaAudio,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
)
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database.models import Channel, ContentPlan, ContentPlanMedia, User

# Типы, которые можно объединять в медиагруппы
PHOTO_VIDEO_TYPES = {"photo", "video"}
DOCUMENT_TYPES = {"document"}
AUDIO_TYPES = {"audio"}
# Типы, которые отправляются только по одному
SINGLE_TYPES = {"animation", "voice", "video_note", "sticker"}


def _build_post_text(post: ContentPlan) -> str:
    """Собирает текст поста: текст + хештеги."""
    parts = []
    if post.text:
        parts.append(post.text)
    if post.hashtags:
        parts.append(post.hashtags)
    return "\n\n".join(parts) if parts else ""


def _group_media(media_list: list[ContentPlanMedia]) -> dict[str, list[ContentPlanMedia]]:
    """Группирует медиа по совместимым типам."""
    groups: dict[str, list[ContentPlanMedia]] = {
        "photo_video": [],
        "document": [],
        "audio": [],
        "single": [],
    }
    for m in media_list:
        if m.media_type in PHOTO_VIDEO_TYPES:
            groups["photo_video"].append(m)
        elif m.media_type in DOCUMENT_TYPES:
            groups["document"].append(m)
        elif m.media_type in AUDIO_TYPES:
            groups["audio"].append(m)
        else:
            groups["single"].append(m)
    return groups


def _make_input_media(m: ContentPlanMedia, caption: str | None = None):
    """Создаёт InputMedia* объект по типу медиа."""
    kwargs = {"media": m.file_id}
    if caption:
        kwargs["caption"] = caption
        kwargs["parse_mode"] = "HTML"
    if m.media_type == "photo":
        return InputMediaPhoto(**kwargs)
    if m.media_type == "video":
        return InputMediaVideo(**kwargs)
    if m.media_type == "document":
        return InputMediaDocument(**kwargs)
    if m.media_type == "audio":
        return InputMediaAudio(**kwargs)
    return None


# Типы, которые не поддерживают caption
NO_CAPTION_TYPES = {"video_note", "sticker"}

CAPTION_LIMIT = 1024


async def _send_single(bot: Bot, channel_id: int, m: ContentPlanMedia, caption: str | None = None) -> bool:
    """Отправляет одиночный медиафайл. Возвращает True если caption был реально отправлен."""
    supports_caption = m.media_type not in NO_CAPTION_TYPES
    effective_caption = caption if (supports_caption and caption) else None

    kwargs = {"chat_id": channel_id}
    if effective_caption:
        kwargs["caption"] = effective_caption
        kwargs["parse_mode"] = "HTML"

    if m.media_type == "photo":
        await bot.send_photo(**kwargs, photo=m.file_id)
    elif m.media_type == "video":
        await bot.send_video(**kwargs, video=m.file_id)
    elif m.media_type == "document":
        await bot.send_document(**kwargs, document=m.file_id)
    elif m.media_type == "audio":
        await bot.send_audio(**kwargs, audio=m.file_id)
    elif m.media_type == "animation":
        await bot.send_animation(**kwargs, animation=m.file_id)
    elif m.media_type == "voice":
        await bot.send_voice(**kwargs, voice=m.file_id)
    elif m.media_type == "video_note":
        await bot.send_video_note(chat_id=channel_id, video_note=m.file_id)
    elif m.media_type == "sticker":
        await bot.send_sticker(chat_id=channel_id, sticker=m.file_id)

    return effective_caption is not None


async def _send_media_group(
    bot: Bot, channel_id: int, media_list: list[ContentPlanMedia], caption: str | None = None,
) -> bool:
    """Отправляет медиагруппу. Возвращает True если caption был использован."""
    if not media_list:
        return False

    if len(media_list) == 1:
        return await _send_single(bot, channel_id, media_list[0], caption)

    items = []
    for i, m in enumerate(media_list):
        c = caption if i == 0 else None
        item = _make_input_media(m, c)
        if item:
            items.append(item)

    if len(items) >= 2:
        await bot.send_media_group(chat_id=channel_id, media=items)
        return caption is not None
    elif items:
        return await _send_single(bot, channel_id, media_list[0], caption)
    return False


async def publish_post(bot: Bot, post: ContentPlan, channel_id: int) -> None:
    """Публикует пост в канал.

    Стратегия:
    1. Собирает текст поста (text + hashtags)
    2. Группирует медиа по совместимым типам
    3. Первая группа получает caption с текстом
    4. Если медиа нет — отправляет текст обычным сообщением
    """
    text = _build_post_text(post)
    media = list(post.media) if post.media else []

    if not media:
        if text:
            await bot.send_message(chat_id=channel_id, text=text, parse_mode="HTML")
        return

    # Caption ограничен 1024 символами; если длиннее — отправляем текст отдельно
    caption = text if (text and len(text) <= CAPTION_LIMIT) else None

    groups = _group_media(media)
    caption_used = False

    # Порядок отправки: фото+видео → документы → аудио → одиночные
    for group_key in ("photo_video", "document", "audio"):
        group = groups[group_key]
        if group:
            cap = caption if (not caption_used and caption) else None
            used = await _send_media_group(bot, channel_id, group, cap)
            if used:
                caption_used = True

    # Одиночные типы (GIF, голосовые, кружки, стикеры)
    for m in groups["single"]:
        cap = caption if (not caption_used and caption) else None
        used = await _send_single(bot, channel_id, m, cap)
        if used:
            caption_used = True

    # Если caption не использован — отправляем текст отдельным сообщением
    if not caption_used and text:
        await bot.send_message(chat_id=channel_id, text=text, parse_mode="HTML")


async def scheduled_publish_job(bot: Bot, post_id: int, channel_db_id: int, overdue: bool = False) -> None:
    """Job-функция для APScheduler: публикует пост и уведомляет пользователя."""
    from app.database.engine import async_session

    async with async_session() as session:
        result = await session.execute(
            select(ContentPlan)
            .options(selectinload(ContentPlan.media))
            .where(ContentPlan.id == post_id)
        )
        post = result.scalar_one_or_none()
        if not post or post.is_published:
            return

        ch_result = await session.execute(select(Channel).where(Channel.id == channel_db_id))
        channel = ch_result.scalar_one_or_none()
        if not channel:
            logging.error(f"Scheduled publish: channel {channel_db_id} not found for post {post_id}")
            return

        user_result = await session.execute(select(User).where(User.id == post.user_id))
        user = user_result.scalar_one_or_none()
        tg_id = user.telegram_id if user else None

        try:
            await publish_post(bot, post, channel.channel_id)
            post.is_published = True
            post.scheduled_at = None
            post.scheduled_channel_id = None
            await session.commit()

            if tg_id:
                if overdue:
                    text = (
                        f"⚠️ <b>Просроченный пост опубликован!</b>\n\n"
                        f"📌 {post.title}\n"
                        f"📢 Канал: {channel.title}\n\n"
                        f"<i>Бот был выключен в запланированное время. "
                        f"Пост опубликован сразу после запуска.</i>"
                    )
                else:
                    text = (
                        f"✅ <b>Пост опубликован по расписанию!</b>\n\n"
                        f"📌 {post.title}\n"
                        f"📢 Канал: {channel.title}"
                    )
                await bot.send_message(chat_id=tg_id, text=text, parse_mode="HTML")
        except Exception as e:
            logging.exception(f"Scheduled publish failed for post {post_id}")
            if tg_id:
                try:
                    await bot.send_message(
                        chat_id=tg_id,
                        text=(
                            f"❌ <b>Ошибка публикации по расписанию</b>\n\n"
                            f"📌 {post.title}\n"
                            f"Ошибка: {e}"
                        ),
                        parse_mode="HTML",
                    )
                except Exception:
                    pass
