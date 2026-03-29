from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    content_plans: Mapped[list["ContentPlan"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    hashtag_sets: Mapped[list["HashtagSet"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    templates: Mapped[list["Template"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    notes: Mapped[list["Note"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    channels: Mapped[list["Channel"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    schedule_presets: Mapped[list["SchedulePreset"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class ContentPlan(Base):
    __tablename__ = "content_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(255))
    text: Mapped[str | None] = mapped_column(Text)
    hashtags: Mapped[str | None] = mapped_column(Text)
    platform: Mapped[str] = mapped_column(String(50), default="telegram")
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scheduled_channel_id: Mapped[int | None] = mapped_column(ForeignKey("channels.id", ondelete="SET NULL"), nullable=True)
    is_published: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="content_plans")
    media: Mapped[list["ContentPlanMedia"]] = relationship(back_populates="content_plan", cascade="all, delete-orphan")


class HashtagSet(Base):
    __tablename__ = "hashtag_sets"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    hashtags: Mapped[str] = mapped_column(Text)  # stored as comma-separated
    category: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="hashtag_sets")


class Template(Base):
    __tablename__ = "templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text)
    template_type: Mapped[str] = mapped_column(String(50), default="general")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="templates")


class Note(Base):
    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(255))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="notes")


class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    channel_id: Mapped[int] = mapped_column(BigInteger)
    title: Mapped[str] = mapped_column(String(255))
    username: Mapped[str | None] = mapped_column(String(255))
    platform: Mapped[str] = mapped_column(String(50), default="telegram")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="channels")


class SchedulePreset(Base):
    __tablename__ = "schedule_presets"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))
    preset_type: Mapped[str] = mapped_column(String(20))  # "hours" or "days"
    hours: Mapped[int] = mapped_column(default=0)
    days: Mapped[int] = mapped_column(default=0)
    hour: Mapped[int] = mapped_column(default=10)
    minute: Mapped[int] = mapped_column(default=0)
    sort_order: Mapped[int] = mapped_column(default=0)

    user: Mapped["User"] = relationship(back_populates="schedule_presets")


class ContentPlanMedia(Base):
    __tablename__ = "content_plan_media"

    id: Mapped[int] = mapped_column(primary_key=True)
    content_plan_id: Mapped[int] = mapped_column(ForeignKey("content_plans.id", ondelete="CASCADE"))
    file_id: Mapped[str] = mapped_column(Text)
    media_type: Mapped[str] = mapped_column(String(50))  # photo, video, document, audio, animation, voice, video_note, sticker
    file_name: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    content_plan: Mapped["ContentPlan"] = relationship(back_populates="media")
