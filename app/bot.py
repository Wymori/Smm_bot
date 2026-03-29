import asyncio
import logging
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from sqlalchemy import text
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database.engine import async_session, engine
from app.database.models import Base, Channel, ContentPlan
from app.handlers import channels, content_plan, hashtags, notes, start, templates
from app.middlewares.db import DbSessionMiddleware
from app.services.publish_service import scheduled_publish_job

scheduler = AsyncIOScheduler(timezone="Europe/Moscow")


async def on_startup(bot: Bot) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text(
            "ALTER TABLE content_plans ADD COLUMN IF NOT EXISTS hashtags TEXT"
        ))
        await conn.execute(text(
            "ALTER TABLE channels ADD COLUMN IF NOT EXISTS platform VARCHAR(50) DEFAULT 'telegram'"
        ))
        await conn.execute(text(
            "ALTER TABLE content_plans ADD COLUMN IF NOT EXISTS scheduled_channel_id INTEGER REFERENCES channels(id) ON DELETE SET NULL"
        ))
    logging.info("Database tables created")

    # Reload scheduled posts
    async with async_session() as session:
        result = await session.execute(
            select(ContentPlan)
            .where(
                ContentPlan.scheduled_at.isnot(None),
                ContentPlan.is_published == False,
                ContentPlan.scheduled_channel_id.isnot(None),
            )
        )
        posts = result.scalars().all()
        now = datetime.now(timezone.utc)
        for post in posts:
            run_time = post.scheduled_at
            if run_time.tzinfo is None:
                run_time = run_time.replace(tzinfo=timezone.utc)
            if run_time > now:
                scheduler.add_job(
                    scheduled_publish_job,
                    "date",
                    run_date=run_time,
                    args=[bot, post.id, post.scheduled_channel_id],
                    id=f"publish_{post.id}",
                    replace_existing=True,
                )
                logging.info(f"Restored scheduled post #{post.id} at {run_time}")
            else:
                # Время прошло пока бот был выключен — публикуем сейчас с пометкой overdue
                scheduler.add_job(
                    scheduled_publish_job,
                    args=[bot, post.id, post.scheduled_channel_id],
                    kwargs={"overdue": True},
                    id=f"publish_{post.id}",
                    replace_existing=True,
                )
                logging.info(f"Publishing overdue post #{post.id}")


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp["scheduler"] = scheduler

    dp.update.middleware(DbSessionMiddleware())

    dp.include_routers(
        start.router,
        content_plan.router,
        hashtags.router,
        templates.router,
        notes.router,
        channels.router,
    )

    dp.startup.register(on_startup)

    scheduler.start()
    logging.info("Bot starting...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
