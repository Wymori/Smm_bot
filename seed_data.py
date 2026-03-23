"""Скрипт для заполнения БД тестовыми наборами хештегов.

Запуск: python seed_data.py
Требование: хотя бы один пользователь должен существовать (отправьте /start боту).
"""
import asyncio

from sqlalchemy import select

from app.database.engine import async_session
from app.database.models import HashtagSet, User

TEST_SETS = [
    {
        "name": "Фитнес",
        "category": "Спорт",
        "hashtags": "#фитнес #спорт #тренировка #здоровье #мотивация",
    },
    {
        "name": "Кулинария",
        "category": "Еда",
        "hashtags": "#кулинария #рецепты #готовимдома #еда #вкусно",
    },
    {
        "name": "Компьютерные игры",
        "category": "Игры",
        "hashtags": "#игры #геймер #gaming #киберспорт #видеоигры",
    },
]


async def seed() -> None:
    async with async_session() as session:
        result = await session.execute(select(User))
        users = result.scalars().all()

        if not users:
            print("Нет пользователей. Сначала запустите бота и отправьте /start")
            return

        for user in users:
            added = 0
            for data in TEST_SETS:
                existing = await session.execute(
                    select(HashtagSet).where(
                        HashtagSet.user_id == user.id,
                        HashtagSet.name == data["name"],
                    )
                )
                if existing.scalar_one_or_none():
                    continue
                session.add(HashtagSet(user_id=user.id, **data))
                added += 1

            await session.commit()
            print(f"Пользователь {user.full_name}: добавлено {added} наборов хештегов")


if __name__ == "__main__":
    asyncio.run(seed())
