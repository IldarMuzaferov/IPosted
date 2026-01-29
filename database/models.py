from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    Interval,
    UniqueConstraint,
    Index,
    CheckConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy import Enum as PgEnum


class Base(DeclarativeBase):
    pass


# =============================================================================
# USERS
# =============================================================================

class User(Base):
    """Telegram user who interacts with the bot."""
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # telegram user_id
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # IANA timezone (e.g., "Europe/Moscow", "America/New_York")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Europe/Moscow")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), nullable=True
    )

    # Relationships
    folders: Mapped[list["Folder"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    channel_memberships: Mapped[list["ChannelAdmin"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    posts: Mapped[list["Post"]] = relationship(
        back_populates="author", cascade="all, delete-orphan"
    )


# =============================================================================
# CHANNELS + ACCESS
# =============================================================================

class Channel(Base):
    """Telegram channel connected to the bot."""
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # telegram channel_id
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)  # @username for public
    is_private: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Bot admin status tracking
    bot_is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    bot_admin_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    admins: Mapped[list["ChannelAdmin"]] = relationship(
        back_populates="channel", cascade="all, delete-orphan"
    )
    folder_links: Mapped[list["FolderChannel"]] = relationship(
        back_populates="channel", cascade="all, delete-orphan"
    )
    targets: Mapped[list["PostTarget"]] = relationship(
        back_populates="channel", cascade="all, delete-orphan"
    )


class TgMemberStatus(str, Enum):
    """Telegram channel member status."""
    creator = "creator"
    administrator = "administrator"
    member = "member"
    left = "left"
    kicked = "kicked"
    unknown = "unknown"


class ChannelAdmin(Base):
    """
    Link between user and channel.
    Ensures: any admin of a channel can see scheduled posts for that channel.
    """
    __tablename__ = "channel_admins"

    channel_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("channels.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )

    tg_status: Mapped[TgMemberStatus] = mapped_column(
        PgEnum(TgMemberStatus, name="tg_member_status"),
        nullable=False, default=TgMemberStatus.unknown
    )
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), nullable=True
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now()
    )

    # Relationships
    channel: Mapped["Channel"] = relationship(back_populates="admins")
    user: Mapped["User"] = relationship(back_populates="channel_memberships")

    __table_args__ = (
        Index("ix_channel_admins_user", "user_id"),
    )


# =============================================================================
# FOLDERS (per-user channel grouping)
# =============================================================================

class Folder(Base):
    """User's folder for organizing channels."""
    __tablename__ = "folders"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    user: Mapped["User"] = relationship(back_populates="folders")
    channels: Mapped[list["FolderChannel"]] = relationship(
        back_populates="folder", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_folders_user_position", "user_id", "position"),
    )


class FolderChannel(Base):
    """Link between folder and channel."""
    __tablename__ = "folder_channels"

    folder_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("folders.id", ondelete="CASCADE"), primary_key=True
    )
    channel_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("channels.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Relationships
    folder: Mapped["Folder"] = relationship(back_populates="channels")
    channel: Mapped["Channel"] = relationship(back_populates="folder_links")

    __table_args__ = (
        Index("ix_folder_channels_channel", "channel_id"),
    )


# =============================================================================
# POSTS (content template)
# =============================================================================

class Post(Base):
    """
    Post content template.
    One Post can be published to multiple channels via PostTarget.
    """
    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    author_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    # Content
    text: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Post settings (from ТЗ "кнопки редактирования")
    silent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # 🔔/🔕
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # Закрепить
    protected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # Защита контента
    comments_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)  # Комментарии
    reactions_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)  # Реакции
    is_repost: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # Репост

    # Version for optimistic locking when editing
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    author: Mapped["User"] = relationship(back_populates="posts")
    media: Mapped[list["PostMedia"]] = relationship(
        back_populates="post", cascade="all, delete-orphan", order_by="PostMedia.order_index"
    )
    buttons: Mapped[list["PostButton"]] = relationship(
        back_populates="post", cascade="all, delete-orphan", order_by="[PostButton.row, PostButton.position]"
    )
    hidden_part: Mapped["PostHiddenPart | None"] = relationship(
        back_populates="post", uselist=False, cascade="all, delete-orphan"
    )
    targets: Mapped[list["PostTarget"]] = relationship(
        back_populates="post", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_posts_author_created", "author_id", "created_at"),
    )


class MediaType(str, Enum):
    """Type of media attached to post."""
    photo = "photo"
    video = "video"
    gif = "gif"
    document = "document"
    voice = "voice"


class PostMedia(Base):
    """Media file attached to post (up to 10 in a media group)."""
    __tablename__ = "post_media"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    post_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("posts.id", ondelete="CASCADE"), nullable=False
    )

    media_type: Mapped[MediaType] = mapped_column(
        PgEnum(MediaType, name="media_type"), nullable=False
    )
    file_id: Mapped[str] = mapped_column(Text, nullable=False)
    file_unique_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Caption for this specific media (optional)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Size in bytes (for validation ≤5MB as per ТЗ)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)

    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Relationships
    post: Mapped["Post"] = relationship(back_populates="media")

    __table_args__ = (
        Index("ix_post_media_post_order", "post_id", "order_index"),
    )


class PostButton(Base):
    """
    URL button on post.
    ТЗ: до 8 кнопок в ряд, до 15 рядов.
    """
    __tablename__ = "post_buttons"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    post_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("posts.id", ondelete="CASCADE"), nullable=False
    )

    text: Mapped[str] = mapped_column(String(64), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)

    row: Mapped[int] = mapped_column(SmallInteger, nullable=False)  # 0-14
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)  # 0-7

    # Relationships
    post: Mapped["Post"] = relationship(back_populates="buttons")

    __table_args__ = (
        UniqueConstraint("post_id", "row", "position", name="uq_post_buttons_grid"),
        CheckConstraint("row >= 0 AND row < 15", name="ck_post_buttons_row"),
        CheckConstraint("position >= 0 AND position < 8", name="ck_post_buttons_position"),
        Index("ix_post_buttons_post", "post_id"),
    )


class PostHiddenPart(Base):
    """
    Hidden continuation of post (Скрытое продолжение).
    Shown only to channel subscribers via bot button.
    """
    __tablename__ = "post_hidden_parts"

    post_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("posts.id", ondelete="CASCADE"), primary_key=True
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)

    # Relationships
    post: Mapped["Post"] = relationship(back_populates="hidden_part")


# =============================================================================
# POST TARGETS (per-channel publication)
# =============================================================================

class TargetState(str, Enum):
    """State of post publication to a specific channel."""
    draft = "draft"  # Being edited
    scheduled = "scheduled"  # Scheduled for future
    queued = "queued"  # In publish queue (about to send)
    sent = "sent"  # Successfully published
    failed = "failed"  # Publish failed
    canceled = "canceled"  # Canceled by user


class PostTarget(Base):
    """
    Publication of a Post to a specific Channel.
    This is the core entity for content-plan.
    """
    __tablename__ = "post_targets"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    post_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("posts.id", ondelete="CASCADE"), nullable=False
    )
    channel_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("channels.id", ondelete="CASCADE"), nullable=False
    )

    state: Mapped[TargetState] = mapped_column(
        PgEnum(TargetState, name="target_state"), nullable=False, default=TargetState.draft
    )

    # Scheduling
    publish_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), nullable=True
    )

    # After successful publish
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), nullable=True
    )
    sent_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # For "Изменить пост" - the original message being edited
    edit_origin_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # Is this a copy of another target? (via "Копировать" button)
    is_copy: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    copied_from_target_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("post_targets.id", ondelete="SET NULL"), nullable=True
    )

    # Auto-delete settings (merged from TargetAutoDelete for simplicity)
    auto_delete_after: Mapped[timedelta | None] = mapped_column(Interval, nullable=True)
    auto_delete_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    auto_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    post: Mapped["Post"] = relationship(back_populates="targets")
    channel: Mapped["Channel"] = relationship(back_populates="targets")
    reply: Mapped["ReplyTarget | None"] = relationship(
        back_populates="target",
        uselist=False,
        cascade="all, delete-orphan",
        foreign_keys="ReplyTarget.target_id",
    )
    replies_as_source: Mapped[list["ReplyTarget"]] = relationship(
        "ReplyTarget",
        foreign_keys="ReplyTarget.source_target_id",
        back_populates="source_target",
    )
    copied_from: Mapped["PostTarget | None"] = relationship(
        remote_side=[id], foreign_keys=[copied_from_target_id]
    )

    __table_args__ = (
        UniqueConstraint("post_id", "channel_id", name="uq_post_targets_post_channel"),
        # Content-plan query: "posts for channel X on date Y"
        Index("ix_post_targets_channel_publish", "channel_id", "publish_at"),
        # Scheduler query: "pending posts to publish"
        Index("ix_post_targets_state_publish", "state", "publish_at"),
        # Auto-delete scheduler
        Index("ix_post_targets_auto_delete", "auto_deleted", "auto_delete_at"),
        Index("ix_post_targets_post_id", "post_id"),
    )


# =============================================================================
# REPLY TARGET (Ответный пост)
# =============================================================================

class ReplyType(str, Enum):
    """Source of reply-to message."""
    forwarded = "forwarded"  # User forwarded a message from channel
    content_plan = "content_plan"  # User selected from content-plan


class ReplyTarget(Base):
    """
    Reply configuration for a PostTarget.
    ТЗ: "Ответный пост" - reply to a message from channel or content-plan.
    """
    __tablename__ = "reply_targets"

    target_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("post_targets.id", ondelete="CASCADE"), primary_key=True
    )

    reply_type: Mapped[ReplyType] = mapped_column(
        PgEnum(ReplyType, name="reply_type"), nullable=False
    )

    # The message to reply to
    reply_to_channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reply_to_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # If selected from content-plan, reference the source target
    source_target_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("post_targets.id", ondelete="SET NULL"), nullable=True
    )

    # Relationships
    target: Mapped["PostTarget"] = relationship(
        back_populates="reply", foreign_keys=[target_id]
    )
    source_target: Mapped["PostTarget | None"] = relationship(
        foreign_keys=[source_target_id],
        back_populates="replies_as_source",
    )


# =============================================================================
# FSM / USER STATE
# =============================================================================

class UserState(Base):
    """
    FSM state storage for user's current interaction.
    Used by aiogram's FSM storage backend.
    """
    __tablename__ = "user_states"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    state: Mapped[str | None] = mapped_column(String(128), nullable=True)
    data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now(), onupdate=func.now()
    )


# =============================================================================
# AUDIT LOG (optional but recommended)
# =============================================================================

class PostEventType(str, Enum):
    """Type of post event for audit log."""
    created = "created"
    updated = "updated"
    scheduled = "scheduled"
    rescheduled = "rescheduled"
    canceled = "canceled"
    sent = "sent"
    failed = "failed"
    deleted = "deleted"
    auto_deleted = "auto_deleted"


class PostEvent(Base):
    """Audit log for post actions."""
    __tablename__ = "post_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    post_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("posts.id", ondelete="CASCADE"), nullable=False
    )
    target_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("post_targets.id", ondelete="SET NULL"), nullable=True
    )
    actor_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    event_type: Mapped[PostEventType] = mapped_column(
        PgEnum(PostEventType, name="post_event_type"), nullable=False
    )
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_post_events_post_time", "post_id", "created_at"),
        Index("ix_post_events_target", "target_id"),
    )