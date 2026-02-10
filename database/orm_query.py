"""
ORM Query Layer для Posted Bot
Исправленная и дополненная версия

ИСПРАВЛЕНЫ ОШИБКИ:
1. orm_add_channel_admin - неправильный синтаксис для составного PK
2. AllChannelsDayPlanRow - mutable default в frozen dataclass
3. tg_status принимался как str вместо enum

ДОБАВЛЕНЫ НЕДОСТАЮЩИЕ ФУНКЦИИ:
- orm_publish_target_now - "Выложить сразу"
- orm_reschedule_target - "Изменить время публикации"
- orm_delete_post - удаление поста
- orm_add_channel_to_folder - добавление канала в папку
- orm_remove_channel_from_folder - удаление канала из папки
- orm_log_post_event - запись в аудит лог
- orm_get_channels_without_folder - каналы без папки (для UI)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Sequence, Iterable

from sqlalchemy import and_, delete, exists, func, select, update, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, joinedload

from database.models import (
    User, Channel, ChannelAdmin, TgMemberStatus,
    Folder, FolderChannel,
    Post, PostMedia, PostButton, PostHiddenPart, MediaType,
    PostTarget, TargetState, ReplyTarget, ReplyType,
    UserState, PostEvent, PostEventType
)


# ---------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------

class ORMError(Exception):
    pass

class NotFound(ORMError):
    pass

class Forbidden(ORMError):
    pass

class ValidationError(ORMError):
    pass


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

async def orm_user_has_channel_access(session: AsyncSession, *, user_id: int, channel_id: int) -> bool:
    """
    Доступ = пользователь присутствует в channel_admins для канала.
    Это ключевое требование ТЗ: админы видят общие посты канала.
    """
    q = select(
        exists().where(
            and_(
                ChannelAdmin.user_id == user_id,
                ChannelAdmin.channel_id == channel_id,
            )
        )
    )
    return bool(await session.scalar(q))


async def orm_require_channel_access(session: AsyncSession, *, user_id: int, channel_id: int) -> None:
    if not await orm_user_has_channel_access(session, user_id=user_id, channel_id=channel_id):
        raise Forbidden(f"user_id={user_id} has no access to channel_id={channel_id}")


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime(day.year, day.month, day.day, 0, 0, 0)
    end = start + timedelta(days=1)
    return start, end


def _validate_buttons_grid(buttons: Sequence[tuple[int, int, str, str]]) -> None:
    """
    buttons: (row, position, text, url)
    Ограничения: row 0..14, position 0..7 (15 рядов × 8 кнопок).
    """
    used = set()
    for row, pos, text, url in buttons:
        if row < 0 or row >= 15:
            raise ValidationError(f"row must be 0..14, got {row}")
        if pos < 0 or pos >= 8:
            raise ValidationError(f"position must be 0..7, got {pos}")
        key = (row, pos)
        if key in used:
            raise ValidationError(f"duplicate button cell row={row} pos={pos}")
        used.add(key)
        if not text or not url:
            raise ValidationError("button text/url must be non-empty")


def _validate_media_limit(media_count: int) -> None:
    if media_count > 10:
        raise ValidationError("media group limit exceeded (max 10)")


def _validate_file_size(file_size: int | None, *, max_bytes: int = 5 * 1024 * 1024) -> None:
    """ТЗ: до 5 МБ"""
    if file_size is not None and file_size > max_bytes:
        raise ValidationError(f"file_size exceeds limit: {file_size} > {max_bytes}")


# ---------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------

async def orm_upsert_user(
    session: AsyncSession,
    *,
    user_id: int,
    username: str | None = None,
    first_name: str | None = None,
    timezone: str | None = None,
) -> User:
    user = await session.get(User, user_id)
    if user is None:
        user = User(
            id=user_id,
            username=username,
            first_name=first_name,
            timezone=timezone or "Europe/Moscow"
        )
        session.add(user)
        await session.flush()
        return user

    # update only provided fields
    if username is not None:
        user.username = username
    if first_name is not None:
        user.first_name = first_name
    if timezone is not None:
        user.timezone = timezone
    user.last_seen_at = datetime.utcnow()
    await session.flush()
    return user


async def orm_get_user(session: AsyncSession, *, user_id: int) -> User:
    user = await session.get(User, user_id)
    if not user:
        raise NotFound(f"user {user_id} not found")
    return user


async def orm_update_user_timezone(session: AsyncSession, *, user_id: int, timezone: str) -> None:
    """Смена часового пояса (из настроек)."""
    user = await orm_get_user(session, user_id=user_id)
    user.timezone = timezone
    await session.flush()


# ---------------------------------------------------------------------
# Channels + Admins
# ---------------------------------------------------------------------

async def orm_upsert_channel(
        session: AsyncSession,
        channel_id: int,
        title: str,
        username: str | None = None,
        is_private: bool = False,
        linked_chat_id: int | None = None,  # <-- ДОБАВИТЬ
):
    """Создаёт или обновляет канал."""
    from database.models import Channel

    channel = await session.get(Channel, channel_id)

    if channel:
        # Обновляем
        channel.title = title
        channel.username = username
        channel.is_private = is_private
        if linked_chat_id is not None:  # <-- ДОБАВИТЬ
            channel.linked_chat_id = linked_chat_id
    else:
        # Создаём новый
        channel = Channel(
            id=channel_id,
            title=title,
            username=username,
            is_private=is_private,
            linked_chat_id=linked_chat_id,  # <-- ДОБАВИТЬ
        )
        session.add(channel)


async def orm_add_channel_admin(
    session: AsyncSession,
    *,
    channel_id: int,
    user_id: int,
    tg_status: TgMemberStatus | None = None,  # ИСПРАВЛЕНО: был str
    verified_at: datetime | None = None,
) -> None:
    """
    Создает запись в channel_admins если её нет.
    """
    # ИСПРАВЛЕНО: для составного PK нужен tuple, а не dict
    existing = await session.get(ChannelAdmin, (channel_id, user_id))
    if existing:
        if tg_status is not None:
            existing.tg_status = tg_status
        if verified_at is not None:
            existing.verified_at = verified_at
        await session.flush()
        return

    row = ChannelAdmin(
        channel_id=channel_id,
        user_id=user_id,
        tg_status=tg_status or TgMemberStatus.unknown,
        verified_at=verified_at,
    )
    session.add(row)
    await session.flush()


async def orm_remove_channel_admin(
    session: AsyncSession,
    *,
    channel_id: int,
    user_id: int,
) -> None:
    """Удаляет админа канала (например, если бот потерял права)."""
    await session.execute(
        delete(ChannelAdmin).where(
            ChannelAdmin.channel_id == channel_id,
            ChannelAdmin.user_id == user_id
        )
    )
    await session.flush()


async def orm_get_user_channels(
    session: AsyncSession,
    *,
    user_id: int,
) -> list[Channel]:
    """Все каналы, где пользователь админ."""
    q = (
        select(Channel)
        .join(ChannelAdmin, ChannelAdmin.channel_id == Channel.id)
        .where(ChannelAdmin.user_id == user_id)
        .order_by(Channel.title.asc())
    )
    res = await session.execute(q)
    return list(res.scalars().all())


async def orm_get_channels_without_folder(
    session: AsyncSession,
    *,
    user_id: int,
) -> list[Channel]:
    """
    ДОБАВЛЕНО: Каналы пользователя, которые не входят ни в одну его папку.
    Для UI контент-плана: кнопка "Каналы" показывает каналы без папки.
    """
    # Подзапрос: каналы в папках этого пользователя
    in_folders = (
        select(FolderChannel.channel_id)
        .join(Folder, Folder.id == FolderChannel.folder_id)
        .where(Folder.user_id == user_id)
        .subquery()
    )

    q = (
        select(Channel)
        .join(ChannelAdmin, ChannelAdmin.channel_id == Channel.id)
        .where(ChannelAdmin.user_id == user_id)
        .where(Channel.id.not_in(select(in_folders.c.channel_id)))
        .order_by(Channel.title.asc())
    )
    res = await session.execute(q)
    return list(res.scalars().all())


async def orm_update_bot_admin_status(
    session: AsyncSession,
    *,
    channel_id: int,
    is_admin: bool,
) -> None:
    """Обновляет статус бота как админа канала."""
    ch = await session.get(Channel, channel_id)
    if ch:
        ch.bot_is_admin = is_admin
        ch.bot_admin_checked_at = datetime.utcnow()
        await session.flush()


# ---------------------------------------------------------------------
# Folders (индивидуальные для пользователя)
# ---------------------------------------------------------------------

async def orm_get_user_folders(session: AsyncSession, *, user_id: int) -> list[Folder]:
    q = (
        select(Folder)
        .where(Folder.user_id == user_id)
        .order_by(Folder.position.asc(), Folder.created_at.asc())
    )
    res = await session.execute(q)
    return list(res.scalars().all())


async def orm_create_folder(
    session: AsyncSession,
    *,
    user_id: int,
    title: str,
    position: int | None = None
) -> Folder:
    if not title:
        raise ValidationError("folder title is empty")

    if position is None:
        q = select(func.coalesce(func.max(Folder.position), 0)).where(Folder.user_id == user_id)
        max_pos = int(await session.scalar(q) or 0)
        position = max_pos + 1

    folder = Folder(user_id=user_id, title=title, position=position)
    session.add(folder)
    await session.flush()
    return folder


async def orm_rename_folder(
    session: AsyncSession,
    *,
    user_id: int,
    folder_id: int,
    new_title: str
) -> None:
    folder = await session.get(Folder, folder_id)
    if not folder or folder.user_id != user_id:
        raise NotFound("folder not found")
    if not new_title:
        raise ValidationError("new_title is empty")
    folder.title = new_title
    await session.flush()


async def orm_delete_folder(
    session: AsyncSession,
    *,
    user_id: int,
    folder_id: int
) -> None:
    """
    ТЗ: удаляем только папку; каналы остаются без папки.
    """
    folder = await session.get(Folder, folder_id)
    if not folder or folder.user_id != user_id:
        raise NotFound("folder not found")

    await session.execute(delete(FolderChannel).where(FolderChannel.folder_id == folder_id))
    await session.execute(delete(Folder).where(Folder.id == folder_id))
    await session.flush()


async def orm_get_free_channels_for_user(
    session: AsyncSession,
    *,
    user_id: int
) -> list[Channel]:
    """
    "Свободные" = каналы, где user админ, но канал не состоит ни в одной папке user'а.
    Используется при добавлении каналов в папку.
    """
    sub = (
        select(FolderChannel.channel_id)
        .join(Folder, Folder.id == FolderChannel.folder_id)
        .where(Folder.user_id == user_id)
        .subquery()
    )

    q = (
        select(Channel)
        .join(ChannelAdmin, ChannelAdmin.channel_id == Channel.id)
        .where(ChannelAdmin.user_id == user_id)
        .where(Channel.id.not_in(select(sub.c.channel_id)))
        .order_by(Channel.title.asc())
    )
    res = await session.execute(q)
    return list(res.scalars().all())


async def orm_set_folder_channels(
    session: AsyncSession,
    *,
    user_id: int,
    folder_id: int,
    channel_ids: Sequence[int],
) -> None:
    """
    Полная перезапись состава папки.
    """
    folder = await session.get(Folder, folder_id)
    if not folder or folder.user_id != user_id:
        raise NotFound("folder not found")

    for ch_id in channel_ids:
        if not await orm_user_has_channel_access(session, user_id=user_id, channel_id=ch_id):
            raise Forbidden(f"user has no access to channel {ch_id}")

    await session.execute(delete(FolderChannel).where(FolderChannel.folder_id == folder_id))
    for idx, ch_id in enumerate(channel_ids):
        session.add(FolderChannel(folder_id=folder_id, channel_id=ch_id, position=idx))
    await session.flush()


async def orm_add_channel_to_folder(
    session: AsyncSession,
    *,
    user_id: int,
    folder_id: int,
    channel_id: int,
) -> None:
    """
    ДОБАВЛЕНО: Добавить один канал в папку.
    ТЗ: кнопка "Добавить канал" при создании поста.
    """
    folder = await session.get(Folder, folder_id)
    if not folder or folder.user_id != user_id:
        raise NotFound("folder not found")

    if not await orm_user_has_channel_access(session, user_id=user_id, channel_id=channel_id):
        raise Forbidden(f"user has no access to channel {channel_id}")

    # Проверяем, не добавлен ли уже
    existing = await session.get(FolderChannel, (folder_id, channel_id))
    if existing:
        return  # Уже в папке

    # Определяем позицию
    q = select(func.coalesce(func.max(FolderChannel.position), -1)).where(
        FolderChannel.folder_id == folder_id
    )
    max_pos = int(await session.scalar(q) or -1)

    session.add(FolderChannel(folder_id=folder_id, channel_id=channel_id, position=max_pos + 1))
    await session.flush()


async def orm_remove_channel_from_folder(
    session: AsyncSession,
    *,
    user_id: int,
    folder_id: int,
    channel_id: int,
) -> None:
    """ДОБАВЛЕНО: Убрать канал из папки."""
    folder = await session.get(Folder, folder_id)
    if not folder or folder.user_id != user_id:
        raise NotFound("folder not found")

    await session.execute(
        delete(FolderChannel).where(
            FolderChannel.folder_id == folder_id,
            FolderChannel.channel_id == channel_id
        )
    )
    await session.flush()


async def orm_get_folder_channels(
    session: AsyncSession,
    *,
    user_id: int,
    folder_id: int
) -> list[Channel]:
    folder = await session.get(Folder, folder_id)
    if not folder or folder.user_id != user_id:
        raise NotFound("folder not found")

    q = (
        select(Channel)
        .join(FolderChannel, FolderChannel.channel_id == Channel.id)
        .where(FolderChannel.folder_id == folder_id)
        .order_by(FolderChannel.position.asc(), Channel.title.asc())
    )
    res = await session.execute(q)
    return list(res.scalars().all())


async def orm_get_folder_channel_count(
    session: AsyncSession,
    *,
    user_id: int,
    folder_id: int
) -> int:
    """ДОБАВЛЕНО: Количество каналов в папке (для кнопки "Каналы: n шт")."""
    folder = await session.get(Folder, folder_id)
    if not folder or folder.user_id != user_id:
        raise NotFound("folder not found")

    q = select(func.count(FolderChannel.channel_id)).where(FolderChannel.folder_id == folder_id)
    return int(await session.scalar(q) or 0)


# ---------------------------------------------------------------------
# Posts (контент) + Media + Buttons
# ---------------------------------------------------------------------

async def orm_create_post_with_targets(
    session: AsyncSession,
    *,
    author_id: int,
    channel_ids: Sequence[int],
    text: str | None = None,
) -> tuple[Post, list[PostTarget]]:
    """
    Создает Post (шаблон) и targets на выбранные каналы в состоянии draft.
    """
    if not channel_ids:
        raise ValidationError("channel_ids is empty")

    for ch in channel_ids:
        await orm_require_channel_access(session, user_id=author_id, channel_id=ch)

    post = Post(author_id=author_id, text=text)
    session.add(post)
    await session.flush()

    targets: list[PostTarget] = []
    for ch in channel_ids:
        t = PostTarget(post_id=post.id, channel_id=ch, state=TargetState.draft)
        session.add(t)
        targets.append(t)

    await session.flush()
    return post, targets


async def orm_get_post_full(session: AsyncSession, *, post_id: int) -> Post:
    """Полная загрузка: media/buttons/hidden_part/targets."""
    q = (
        select(Post)
        .where(Post.id == post_id)
        .options(
            selectinload(Post.media),
            selectinload(Post.buttons),
            selectinload(Post.targets),
            selectinload(Post.hidden_part),
        )
    )
    res = await session.execute(q)
    post = res.scalar_one_or_none()
    if not post:
        raise NotFound("post not found")
    return post


async def orm_update_post_text(session: AsyncSession, *, post_id: int, text: str | None) -> None:
    post = await session.get(Post, post_id)
    if not post:
        raise NotFound("post not found")
    post.text = text
    post.version += 1
    await session.flush()


async def orm_set_post_flags(
    session: AsyncSession,
    *,
    post_id: int,
    silent: bool | None = None,
    pinned: bool | None = None,
    protected: bool | None = None,
    comments_enabled: bool | None = None,
    reactions_enabled: bool | None = None,
    is_repost: bool | None = None,
) -> None:
    post = await session.get(Post, post_id)
    if not post:
        raise NotFound("post not found")

    if silent is not None:
        post.silent = silent
    if pinned is not None:
        post.pinned = pinned
    if protected is not None:
        post.protected = protected
    if comments_enabled is not None:
        post.comments_enabled = comments_enabled
    if reactions_enabled is not None:
        post.reactions_enabled = reactions_enabled
    if is_repost is not None:
        post.is_repost = is_repost

    post.version += 1
    await session.flush()


async def orm_delete_post(
    session: AsyncSession,
    *,
    actor_user_id: int,
    post_id: int,
) -> None:
    """
    ДОБАВЛЕНО: Удаление поста целиком.
    Каскадно удалит media, buttons, hidden_part, targets.
    """
    post = await session.get(Post, post_id)
    if not post:
        raise NotFound("post not found")

    # Проверяем что пользователь - автор или админ хотя бы одного канала
    if post.author_id != actor_user_id:
        # Проверим есть ли доступ хотя бы к одному target
        q = select(PostTarget.channel_id).where(PostTarget.post_id == post_id).limit(1)
        res = await session.execute(q)
        channel_id = res.scalar_one_or_none()
        if channel_id:
            await orm_require_channel_access(session, user_id=actor_user_id, channel_id=channel_id)

    await session.delete(post)
    await session.flush()


async def orm_replace_post_buttons(
    session: AsyncSession,
    *,
    post_id: int,
    buttons: Sequence[tuple[int, int, str, str]],
) -> None:
    """
    Полностью заменить кнопки на посте.
    buttons: (row, position, text, url)
    """
    _validate_buttons_grid(buttons)

    await session.execute(delete(PostButton).where(PostButton.post_id == post_id))
    for row, pos, text, url in buttons:
        session.add(PostButton(post_id=post_id, row=row, position=pos, text=text, url=url))
    await session.flush()


async def orm_set_hidden_part(session: AsyncSession, *, post_id: int, text: str | None) -> None:
    """Скрытое продолжение. Если text=None или пусто — удаляем."""
    hp = await session.get(PostHiddenPart, post_id)
    if not text:
        if hp:
            await session.execute(delete(PostHiddenPart).where(PostHiddenPart.post_id == post_id))
            await session.flush()
        return

    if hp is None:
        session.add(PostHiddenPart(post_id=post_id, text=text))
    else:
        hp.text = text
    await session.flush()


async def orm_add_post_media(
    session: AsyncSession,
    *,
    post_id: int,
    media_type: MediaType,
    file_id: str,
    file_unique_id: str | None = None,
    caption: str | None = None,
    file_size: int | None = None,
    order_index: int | None = None,
) -> PostMedia:
    """Добавляет медиа. Лимиты: 10 файлов, каждый ≤5MB."""
    if not file_id:
        raise ValidationError("file_id is empty")
    _validate_file_size(file_size)

    q = select(func.count(PostMedia.id)).where(PostMedia.post_id == post_id)
    cnt = int(await session.scalar(q) or 0)
    _validate_media_limit(cnt + 1)

    if order_index is None:
        q2 = select(func.coalesce(func.max(PostMedia.order_index), -1)).where(PostMedia.post_id == post_id)
        mx = int(await session.scalar(q2) or -1)
        order_index = mx + 1

    m = PostMedia(
        post_id=post_id,
        media_type=media_type,
        file_id=file_id,
        file_unique_id=file_unique_id,
        caption=caption,
        file_size=file_size,
        order_index=order_index,
    )
    session.add(m)
    await session.flush()
    return m


async def orm_delete_post_media(session: AsyncSession, post_id: int):
    """Удаляет все медиа из поста."""
    from database.models import PostMedia
    await session.execute(
        delete(PostMedia).where(PostMedia.post_id == post_id)
    )


async def orm_clear_post_media(session: AsyncSession, *, post_id: int) -> None:
    """ДОБАВЛЕНО: Удалить все медиа поста."""
    await session.execute(delete(PostMedia).where(PostMedia.post_id == post_id))
    await session.flush()


# ---------------------------------------------------------------------
# Targets: schedule / reschedule / cancel / copy / publish now
# ---------------------------------------------------------------------

async def orm_get_target(session: AsyncSession, *, target_id: int) -> PostTarget:
    t = await session.get(PostTarget, target_id)
    if not t:
        raise NotFound("target not found")
    return t


async def orm_get_target_full(session: AsyncSession, *, target_id: int) -> PostTarget:
    q = (
        select(PostTarget)
        .where(PostTarget.id == target_id)
        .options(
            joinedload(PostTarget.post).selectinload(Post.media),
            joinedload(PostTarget.post).selectinload(Post.buttons),
            joinedload(PostTarget.post).selectinload(Post.hidden_part),
            selectinload(PostTarget.reply),
            joinedload(PostTarget.post).selectinload(Post.reaction_buttons),

        )
    )
    res = await session.execute(q)
    t = res.scalar_one_or_none()
    if not t:
        raise NotFound("target not found")
    return t


async def orm_schedule_target(
    session: AsyncSession,
    *,
    actor_user_id: int,
    target_id: int,
    publish_at: datetime,
) -> None:
    """Запланировать публикацию на указанное время."""
    t = await orm_get_target(session, target_id=target_id)
    await orm_require_channel_access(session, user_id=actor_user_id, channel_id=t.channel_id)

    t.publish_at = publish_at
    t.state = TargetState.scheduled
    t.last_error = None

    # Пересчитываем auto_delete_at если задан delete_after
    if t.auto_delete_after is not None:
        t.auto_delete_at = publish_at + t.auto_delete_after

    await session.flush()


async def orm_reschedule_target(
    session: AsyncSession,
    *,
    actor_user_id: int,
    target_id: int,
    new_publish_at: datetime,
) -> None:
    """
    ДОБАВЛЕНО: Изменить время публикации.
    ТЗ: кнопка "Изменить время публикации" при редактировании поста.
    """
    t = await orm_get_target(session, target_id=target_id)
    await orm_require_channel_access(session, user_id=actor_user_id, channel_id=t.channel_id)

    if t.state not in (TargetState.draft, TargetState.scheduled):
        raise ValidationError(f"Cannot reschedule target in state {t.state}")

    t.publish_at = new_publish_at
    t.state = TargetState.scheduled

    # Пересчитываем auto_delete_at
    if t.auto_delete_after is not None:
        t.auto_delete_at = new_publish_at + t.auto_delete_after

    await session.flush()


async def orm_publish_target_now(
    session: AsyncSession,
    *,
    actor_user_id: int,
    target_id: int,
) -> None:
    """
    ДОБАВЛЕНО: "Выложить сразу" - публикация немедленно.
    Ставит publish_at = now и state = queued для немедленной обработки.
    """
    t = await orm_get_target(session, target_id=target_id)
    await orm_require_channel_access(session, user_id=actor_user_id, channel_id=t.channel_id)

    if t.state not in (TargetState.draft, TargetState.scheduled):
        raise ValidationError(f"Cannot publish target in state {t.state}")

    now = datetime.utcnow()
    t.publish_at = now
    t.state = TargetState.queued  # Сразу в очередь

    if t.auto_delete_after is not None:
        t.auto_delete_at = now + t.auto_delete_after

    await session.flush()


async def orm_cancel_target(
    session: AsyncSession,
    *,
    actor_user_id: int,
    target_id: int
) -> None:
    t = await orm_get_target(session, target_id=target_id)
    await orm_require_channel_access(session, user_id=actor_user_id, channel_id=t.channel_id)

    t.state = TargetState.canceled
    await session.flush()


async def orm_set_target_autodelete(
    session: AsyncSession,
    *,
    actor_user_id: int,
    target_id: int,
    delete_after: timedelta | None,
) -> None:
    """
    Автоудаление поста через N времени после публикации.
    ТЗ: 1ч 6ч 12ч 24ч 48ч 3дня 7дней.
    """
    t = await orm_get_target(session, target_id=target_id)
    await orm_require_channel_access(session, user_id=actor_user_id, channel_id=t.channel_id)

    t.auto_delete_after = delete_after
    if delete_after is None:
        t.auto_delete_at = None
    else:
        # Если уже запланирован или отправлен - вычисляем delete_at
        base_time = t.sent_at or t.publish_at
        if base_time is not None:
            t.auto_delete_at = base_time + delete_after
        else:
            t.auto_delete_at = None
    t.auto_deleted = False
    await session.flush()


async def orm_set_target_edit_origin(
    session: AsyncSession,
    *,
    actor_user_id: int,
    target_id: int,
    origin_message_id: int | None,
) -> None:
    """
    Для "Изменить пост": хранит message_id исходного сообщения в канале.
    """
    t = await orm_get_target(session, target_id=target_id)
    await orm_require_channel_access(session, user_id=actor_user_id, channel_id=t.channel_id)

    t.edit_origin_message_id = origin_message_id
    await session.flush()


async def orm_copy_target_to_channels(
    session: AsyncSession,
    *,
    actor_user_id: int,
    source_target_id: int,
    destination_channel_ids: Sequence[int],
    copy_publish_at: datetime | None = None,
    copy_auto_delete: bool = True,  # ДОБАВЛЕНО: копировать ли настройки автоудаления
) -> list[PostTarget]:
    """
    "Копировать": создаем новые targets для того же Post в другие каналы.
    """
    src = await orm_get_target(session, target_id=source_target_id)
    await orm_require_channel_access(session, user_id=actor_user_id, channel_id=src.channel_id)

    if not destination_channel_ids:
        return []

    created: list[PostTarget] = []
    for ch_id in destination_channel_ids:
        await orm_require_channel_access(session, user_id=actor_user_id, channel_id=ch_id)

        t = PostTarget(
            post_id=src.post_id,
            channel_id=ch_id,
            state=TargetState.scheduled if copy_publish_at else TargetState.draft,
            publish_at=copy_publish_at,
            is_copy=True,
            copied_from_target_id=src.id,
            # ДОБАВЛЕНО: копируем auto_delete если нужно
            auto_delete_after=src.auto_delete_after if copy_auto_delete else None,
        )
        if copy_publish_at and t.auto_delete_after:
            t.auto_delete_at = copy_publish_at + t.auto_delete_after

        session.add(t)
        created.append(t)

    await session.flush()
    return created


# ---------------------------------------------------------------------
# Reply target (ответный пост)
# ---------------------------------------------------------------------

async def orm_set_reply_target_forwarded(
    session: AsyncSession,
    *,
    actor_user_id: int,
    target_id: int,
    reply_to_channel_id: int,
    reply_to_message_id: int,
) -> None:
    """
    Ответ на пересланное сообщение из канала.
    """
    t = await orm_get_target(session, target_id=target_id)
    await orm_require_channel_access(session, user_id=actor_user_id, channel_id=t.channel_id)

    rt = await session.get(ReplyTarget, target_id)
    if rt is None:
        rt = ReplyTarget(
            target_id=target_id,
            reply_type=ReplyType.forwarded,
            reply_to_channel_id=reply_to_channel_id,
            reply_to_message_id=reply_to_message_id,
            source_target_id=None,
        )
        session.add(rt)
    else:
        rt.reply_type = ReplyType.forwarded
        rt.reply_to_channel_id = reply_to_channel_id
        rt.reply_to_message_id = reply_to_message_id
        rt.source_target_id = None
    await session.flush()


async def orm_set_reply_target_from_content_plan(
    session: AsyncSession,
    *,
    actor_user_id: int,
    target_id: int,
    source_target_id: int,
) -> None:
    """
    Ответ на пост из контент-плана.
    ИСПРАВЛЕНО: автоматически берём channel_id и message_id из source target.
    """
    t = await orm_get_target(session, target_id=target_id)
    await orm_require_channel_access(session, user_id=actor_user_id, channel_id=t.channel_id)

    src = await orm_get_target(session, target_id=source_target_id)
    await orm_require_channel_access(session, user_id=actor_user_id, channel_id=src.channel_id)

    # Проверяем что source уже опубликован
    if src.state != TargetState.sent or src.sent_message_id is None:
        raise ValidationError("Source target is not published yet, cannot reply to it")

    rt = await session.get(ReplyTarget, target_id)
    if rt is None:
        rt = ReplyTarget(
            target_id=target_id,
            reply_type=ReplyType.content_plan,
            reply_to_channel_id=src.channel_id,
            reply_to_message_id=src.sent_message_id,
            source_target_id=source_target_id,
        )
        session.add(rt)
    else:
        rt.reply_type = ReplyType.content_plan
        rt.reply_to_channel_id = src.channel_id
        rt.reply_to_message_id = src.sent_message_id
        rt.source_target_id = source_target_id
    await session.flush()


async def orm_clear_reply_target(
    session: AsyncSession,
    *,
    actor_user_id: int,
    target_id: int
) -> None:
    t = await orm_get_target(session, target_id=target_id)
    await orm_require_channel_access(session, user_id=actor_user_id, channel_id=t.channel_id)

    await session.execute(delete(ReplyTarget).where(ReplyTarget.target_id == target_id))
    await session.flush()


# ---------------------------------------------------------------------
# Scheduler: pick queued targets, mark sent/failed, auto-delete
# ---------------------------------------------------------------------

async def orm_pick_targets_to_publish(
    session: AsyncSession,
    *,
    limit: int = 50,
    now: datetime | None = None,
) -> list[PostTarget]:
    """
    Берем scheduled targets с publish_at <= now и переводим в queued.
    """
    if now is None:
        now = datetime.utcnow()

    q = (
        select(PostTarget)
        .where(PostTarget.state == TargetState.scheduled)
        .where(PostTarget.publish_at.is_not(None))
        .where(PostTarget.publish_at <= now)
        .order_by(PostTarget.publish_at.asc(), PostTarget.id.asc())
        .limit(limit)
    )

    res = await session.execute(q)
    targets = list(res.scalars().all())
    for t in targets:
        t.state = TargetState.queued
    await session.flush()
    return targets


async def orm_mark_target_sent(
    session: AsyncSession,
    *,
    target_id: int,
    sent_message_id: int,
    sent_at: datetime | None = None,
) -> None:
    t = await orm_get_target(session, target_id=target_id)
    t.state = TargetState.sent
    t.sent_message_id = sent_message_id
    t.sent_at = sent_at or datetime.utcnow()
    t.last_error = None

    # Рассчитываем auto_delete_at от времени отправки
    if t.auto_delete_after is not None and t.auto_delete_at is None:
        t.auto_delete_at = t.sent_at + t.auto_delete_after
    await session.flush()


async def orm_mark_target_failed(
    session: AsyncSession,
    *,
    target_id: int,
    error: str,
) -> None:
    t = await orm_get_target(session, target_id=target_id)
    t.state = TargetState.failed
    t.last_error = (error or "")[:4000]
    await session.flush()


async def orm_pick_targets_to_autodelete(
    session: AsyncSession,
    *,
    limit: int = 50,
    now: datetime | None = None,
) -> list[PostTarget]:
    """Берем targets для автоудаления."""
    if now is None:
        now = datetime.utcnow()

    q = (
        select(PostTarget)
        .where(PostTarget.auto_deleted.is_(False))
        .where(PostTarget.auto_delete_at.is_not(None))
        .where(PostTarget.auto_delete_at <= now)
        .where(PostTarget.state == TargetState.sent)  # ДОБАВЛЕНО: только отправленные
        .order_by(PostTarget.auto_delete_at.asc(), PostTarget.id.asc())
        .limit(limit)
    )
    res = await session.execute(q)
    return list(res.scalars().all())


async def orm_mark_target_autodeleted(session: AsyncSession, *, target_id: int) -> None:
    t = await orm_get_target(session, target_id=target_id)
    t.auto_deleted = True
    await session.flush()


# ---------------------------------------------------------------------
# Content plan queries
# ---------------------------------------------------------------------

@dataclass(frozen=True)
class DayPlanItem:
    target_id: int
    channel_id: int
    publish_at: datetime
    state: str


async def orm_get_day_plan_for_channel(
    session: AsyncSession,
    *,
    actor_user_id: int,
    channel_id: int,
    day: date,
) -> list[DayPlanItem]:
    """Контент-план на конкретный день и канал."""
    await orm_require_channel_access(session, user_id=actor_user_id, channel_id=channel_id)
    start, end = _day_bounds(day)

    q = (
        select(PostTarget.id, PostTarget.channel_id, PostTarget.publish_at, PostTarget.state)
        .where(PostTarget.channel_id == channel_id)
        .where(PostTarget.publish_at.is_not(None))
        .where(PostTarget.publish_at >= start, PostTarget.publish_at < end)
        .where(PostTarget.state.in_([TargetState.scheduled, TargetState.queued]))
        .order_by(PostTarget.publish_at.asc())
    )
    res = await session.execute(q)
    return [DayPlanItem(r[0], r[1], r[2], r[3].value) for r in res.all()]


@dataclass
class AllChannelsDayPlanRow:
    """Для режима "Во всех сразу"."""
    channel_id: int
    channel_title: str
    times: list[datetime] = field(default_factory=list)  # ИСПРАВЛЕНО: убран frozen, добавлен field


async def orm_get_day_plan_all_channels(
    session: AsyncSession,
    *,
    actor_user_id: int,
    day: date,
) -> list[AllChannelsDayPlanRow]:
    """
    Для режима "Во всех сразу": по каждому каналу список времён.
    """
    start, end = _day_bounds(day)

    allowed_channels = (
        select(Channel.id)
        .join(ChannelAdmin, ChannelAdmin.channel_id == Channel.id)
        .where(ChannelAdmin.user_id == actor_user_id)
        .subquery()
    )

    q = (
        select(Channel.id, Channel.title, PostTarget.publish_at)
        .join(PostTarget, PostTarget.channel_id == Channel.id)
        .where(Channel.id.in_(select(allowed_channels.c.id)))
        .where(PostTarget.publish_at.is_not(None))
        .where(PostTarget.publish_at >= start, PostTarget.publish_at < end)
        .where(PostTarget.state.in_([TargetState.scheduled, TargetState.queued]))
        .order_by(Channel.title.asc(), PostTarget.publish_at.asc())
    )
    res = await session.execute(q)

    grouped: dict[int, AllChannelsDayPlanRow] = {}
    for ch_id, ch_title, pub_at in res.all():
        if ch_id not in grouped:
            grouped[ch_id] = AllChannelsDayPlanRow(channel_id=ch_id, channel_title=ch_title)
        grouped[ch_id].times.append(pub_at)

    return list(grouped.values())


@dataclass(frozen=True)
class MonthMarkers:
    """Для календаря: какие даты имеют посты (ромбик 🔸)."""
    day: date
    count: int


async def orm_get_month_markers_for_channel(
    session: AsyncSession,
    *,
    actor_user_id: int,
    channel_id: int,
    year: int,
    month: int,
) -> list[MonthMarkers]:
    await orm_require_channel_access(session, user_id=actor_user_id, channel_id=channel_id)

    start = datetime(year, month, 1)
    if month == 12:
        end = datetime(year + 1, 1, 1)
    else:
        end = datetime(year, month + 1, 1)

    day_expr = func.date_trunc("day", PostTarget.publish_at).label("day")
    q = (
        select(day_expr, func.count(PostTarget.id))
        .where(PostTarget.channel_id == channel_id)
        .where(PostTarget.publish_at.is_not(None))
        .where(PostTarget.publish_at >= start, PostTarget.publish_at < end)
        .where(PostTarget.state.in_([TargetState.scheduled, TargetState.queued]))
        .group_by(day_expr)
        .order_by(day_expr.asc())
    )
    res = await session.execute(q)

    return [MonthMarkers(day=day_dt.date(), count=int(cnt)) for day_dt, cnt in res.all()]


@dataclass(frozen=True)
class ScheduledDaySummary:
    """Для кнопок "Все отложенные посты"."""
    day: date
    posts_count: int


async def orm_get_all_scheduled_days_for_channel(
    session: AsyncSession,
    *,
    actor_user_id: int,
    channel_id: int,
    from_day: date | None = None,
    to_day: date | None = None,
) -> list[ScheduledDaySummary]:
    """
    "Все отложенные посты": только даты где есть посты.
    ТЗ: кнопки "Пн 13 Янв, 2 поста".
    """
    await orm_require_channel_access(session, user_id=actor_user_id, channel_id=channel_id)

    day_expr = func.date_trunc("day", PostTarget.publish_at).label("day")

    q = (
        select(day_expr, func.count(PostTarget.id))
        .where(PostTarget.channel_id == channel_id)
        .where(PostTarget.publish_at.is_not(None))
        .where(PostTarget.state.in_([TargetState.scheduled, TargetState.queued]))
    )
    if from_day is not None:
        q = q.where(PostTarget.publish_at >= datetime(from_day.year, from_day.month, from_day.day))
    if to_day is not None:
        q = q.where(PostTarget.publish_at < datetime(to_day.year, to_day.month, to_day.day) + timedelta(days=1))

    q = q.group_by(day_expr).order_by(day_expr.asc())
    res = await session.execute(q)

    return [ScheduledDaySummary(day=day_dt.date(), posts_count=int(cnt)) for day_dt, cnt in res.all()]


# ---------------------------------------------------------------------
# Audit log (PostEvent)
# ---------------------------------------------------------------------

async def orm_log_post_event(
    session: AsyncSession,
    *,
    post_id: int,
    event_type: PostEventType,
    actor_user_id: int | None = None,
    target_id: int | None = None,
    payload: dict | None = None,
) -> PostEvent:
    """ДОБАВЛЕНО: Запись события в аудит лог."""
    event = PostEvent(
        post_id=post_id,
        event_type=event_type,
        actor_user_id=actor_user_id,
        target_id=target_id,
        payload=payload,
    )
    session.add(event)
    await session.flush()
    return event


async def orm_get_post_events(
    session: AsyncSession,
    *,
    post_id: int,
    limit: int = 50,
) -> list[PostEvent]:
    """ДОБАВЛЕНО: История событий поста."""
    q = (
        select(PostEvent)
        .where(PostEvent.post_id == post_id)
        .order_by(PostEvent.created_at.desc())
        .limit(limit)
    )
    res = await session.execute(q)
    return list(res.scalars().all())


# ---------------------------------------------------------------------
# FSM UserState
# ---------------------------------------------------------------------

async def orm_get_user_state(session: AsyncSession, *, user_id: int) -> UserState | None:
    return await session.get(UserState, user_id)


async def orm_set_user_state(
    session: AsyncSession,
    *,
    user_id: int,
    state: str | None,
    data: dict | None
) -> None:
    row = await session.get(UserState, user_id)
    if row is None:
        row = UserState(user_id=user_id, state=state, data=data)
        session.add(row)
    else:
        row.state = state
        row.data = data
    await session.flush()


async def orm_clear_user_state(session: AsyncSession, *, user_id: int) -> None:
    await session.execute(delete(UserState).where(UserState.user_id == user_id))
    await session.flush()

def _detect_content_type(message) -> str:
    # максимально простой детектор, потом расширим
    if message.photo:
        return "photo"
    if message.video:
        return "video"
    if message.document:
        return "document"
    if message.voice:
        return "voice"
    if message.animation:
        return "animation"
    if message.sticker:
        return "sticker"
    if message.audio:
        return "audio"
    if message.video_note:
        return "video_note"
    if message.text:
        return "text"
    return "unknown"

def _extract_media_from_message(message) -> tuple[str | None, str | None, str | None]:
    """
    Извлекает тип медиа, file_id и file_unique_id из сообщения.
    """
    if message.photo:
        photo = message.photo[-1]
        return "photo", photo.file_id, photo.file_unique_id
    if message.video:
        return "video", message.video.file_id, message.video.file_unique_id
    if message.animation:
        return "gif", message.animation.file_id, message.animation.file_unique_id
    if message.document:
        return "document", message.document.file_id, message.document.file_unique_id
    if message.voice:
        return "voice", message.voice.file_id, message.voice.file_unique_id
    if message.audio:
        return "document", message.audio.file_id, message.audio.file_unique_id
    if message.video_note:
        return "video", message.video_note.file_id, message.video_note.file_unique_id
    return None, None, None


async def orm_create_post_from_message(
        session: AsyncSession,
        *,
        user_id: int,
        message,
        channel_ids: Iterable[int],
) -> int:
    text = message.text or message.caption or None

    post = Post(
        author_id=user_id,
        text=text,
        created_at=datetime.utcnow(),
        source_chat_id=message.chat.id,
        source_message_id=message.message_id,
    )
    session.add(post)
    await session.flush()

    # ========== ДОБАВЛЕНО: Сохраняем медиа ==========
    media_type_str, file_id, file_unique_id = _extract_media_from_message(message)

    if media_type_str and file_id:
        mt = MediaType(media_type_str)
        post_media = PostMedia(
            post_id=post.id,
            media_type=mt,
            file_id=file_id,
            file_unique_id=file_unique_id,
            order_index=0,
        )
        session.add(post_media)
    # ================================================

    for ch_id in channel_ids:
        session.add(
            PostTarget(
                post_id=post.id,
                channel_id=ch_id,
                state=TargetState.draft,
                publish_at=None,
            )
        )

    await session.flush()
    return int(post.id)


async def orm_create_post_from_album(
        session: AsyncSession,
        *,
        user_id: int,
        messages: list,
        channel_ids,
) -> int:
    text = None
    for msg in messages:
        if msg.caption:
            text = msg.caption
            break
    first_msg = messages[0] if messages else None

    post = Post(
        author_id=user_id,
        text=text,
        created_at=datetime.utcnow(),
        source_chat_id=first_msg.chat.id if first_msg else None,  # <-- ДОБАВИТЬ
        source_message_id=first_msg.message_id if first_msg else None,
    )
    session.add(post)
    await session.flush()

    # ========== ДОБАВЛЕНО: Сохраняем все медиа альбома ==========
    for idx, msg in enumerate(messages):
        media_type_str, file_id, file_unique_id = _extract_media_from_message(msg)

        if media_type_str and file_id:
            mt = MediaType(media_type_str)
            post_media = PostMedia(
                post_id=post.id,
                media_type=mt,
                file_id=file_id,
                file_unique_id=file_unique_id,
                order_index=idx,
            )
            session.add(post_media)
    # ============================================================

    for ch_id in channel_ids:
        session.add(PostTarget(post_id=post.id, channel_id=ch_id, state=TargetState.draft))

    await session.flush()
    return int(post.id)


async def orm_edit_post_text(session: AsyncSession, *, post_id: int, text: str | None):
    await session.execute(
        update(Post).where(Post.id == post_id).values(text=text)
    )


async def orm_add_media_to_post(
        session: AsyncSession,
        post_id: int,
        media_type: str,
        file_id: str,
        file_unique_id: str | None = None,
        caption: str | None = None,
        order_index: int = 0,
) -> int:
    """
    Добавляет медиа к существующему посту.
    Возвращает ID созданной записи PostMedia.

    Args:
        session: Сессия БД
        post_id: ID поста
        media_type: Тип медиа ('photo', 'video', 'gif', 'document', 'voice')
        file_id: Telegram file_id
        file_unique_id: Telegram file_unique_id (опционально)
        caption: Подпись к медиа (опционально)
        order_index: Порядковый номер в альбоме (по умолчанию 0)

    Returns:
        ID созданной записи PostMedia
    """
    from database.models import PostMedia, MediaType

    # Преобразуем строку в MediaType enum
    mt = MediaType(media_type)

    media = PostMedia(
        post_id=post_id,
        media_type=mt,
        file_id=file_id,
        file_unique_id=file_unique_id,
        caption=caption,
        order_index=order_index,
    )
    session.add(media)
    await session.flush()
    return media.id


async def orm_get_all_user_channels(session: AsyncSession, user_id: int) -> list:
    """
    Получает ВСЕ каналы пользователя, включая те что в папках.
    """
    from database.models import Channel, ChannelAdmin
    from sqlalchemy import select

    stmt = (
        select(Channel)
        .join(ChannelAdmin, Channel.id == ChannelAdmin.channel_id)
        .where(ChannelAdmin.user_id == user_id)
        .order_by(Channel.title)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def orm_copy_post_to_channels(
        session: AsyncSession,
        post_id: int,
        channel_ids: set[int],
) -> list[int]:
    """
    Создаёт PostTarget для копирования поста в указанные каналы.
    Возвращает список ID созданных PostTarget.
    """
    from database.models import PostTarget, TargetState

    created_ids = []

    for channel_id in channel_ids:
        target = PostTarget(
            post_id=post_id,
            channel_id=channel_id,
            state=TargetState.draft,
            is_copy=True,
        )
        session.add(target)
        await session.flush()
        created_ids.append(target.id)

    return created_ids


async def orm_get_post_buttons(session: AsyncSession, post_id: int) -> list[dict]:
    """
    Получает URL-кнопки поста.

    Returns:
        Список словарей с ключами: text, url, row, position
    """
    from database.models import PostButton
    from sqlalchemy import select

    stmt = (
        select(PostButton)
        .where(PostButton.post_id == post_id)
        .order_by(PostButton.row, PostButton.position)
    )
    result = await session.execute(stmt)
    buttons = result.scalars().all()

    return [
        {
            'text': btn.text,
            'url': btn.url,
            'row': btn.row,
            'position': btn.position,
        }
        for btn in buttons
    ]


async def orm_save_post_buttons(
        session: AsyncSession,
        post_id: int,
        buttons: list[dict],
) -> None:
    """
    Сохраняет URL-кнопки для поста.

    Args:
        buttons: Список словарей с ключами text, url, row, position
    """
    from database.models import PostButton

    for btn in buttons:
        post_button = PostButton(
            post_id=post_id,
            text=btn['text'],
            url=btn['url'],
            row=btn['row'],
            position=btn['position'],
        )
        session.add(post_button)

    await session.flush()


async def orm_delete_post_buttons(session: AsyncSession, post_id: int) -> None:
    """
    Удаляет все URL-кнопки поста.
    """
    from database.models import PostButton
    from sqlalchemy import delete

    stmt = delete(PostButton).where(PostButton.post_id == post_id)
    await session.execute(stmt)


async def orm_set_post_text_position(session: AsyncSession, *, post_id: int, position: str) -> None:
    post = await session.get(Post, post_id)
    if not post:
        raise NotFound("post not found")
    post.text_position = position
    await session.flush()


async def orm_get_hidden_part(session: AsyncSession, *, post_id: int):
    from database.models import PostHiddenPart
    return await session.get(PostHiddenPart, post_id)


async def orm_save_hidden_part(
        session: AsyncSession,
        *,
        post_id: int,
        button_text: str,
        subscriber_text: str,
        nonsubscriber_text: str | None = None,
) -> None:
    from database.models import PostHiddenPart

    existing = await session.get(PostHiddenPart, post_id)
    if existing:
        existing.button_text = button_text
        existing.subscriber_text = subscriber_text
        existing.nonsubscriber_text = nonsubscriber_text
    else:
        hidden_part = PostHiddenPart(
            post_id=post_id,
            button_text=button_text,
            subscriber_text=subscriber_text,
            nonsubscriber_text=nonsubscriber_text,
        )
        session.add(hidden_part)
    await session.flush()


async def orm_delete_hidden_part(session: AsyncSession, *, post_id: int) -> None:
    from database.models import PostHiddenPart
    from sqlalchemy import delete

    await session.execute(delete(PostHiddenPart).where(PostHiddenPart.post_id == post_id))
    await session.flush()


async def orm_get_post_with_channel(session: AsyncSession, *, post_id: int):
    """
    Загружает пост с targets для определения channel_id.
    Использует eager loading чтобы избежать greenlet error.
    """
    from database.models import Post
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    stmt = (
        select(Post)
        .where(Post.id == post_id)
        .options(selectinload(Post.targets))
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def orm_save_reply_target(
        session: AsyncSession,
        *,
        target_id: int,
        reply_type: str,
        reply_to_channel_id: int,
        reply_to_message_id: int,
        source_target_id: int | None = None,
) -> None:
    """Сохраняет настройки ответного поста."""
    from database.models import ReplyTarget, ReplyType

    existing = await session.get(ReplyTarget, target_id)

    if existing:
        existing.reply_type = ReplyType(reply_type)
        existing.reply_to_channel_id = reply_to_channel_id
        existing.reply_to_message_id = reply_to_message_id
        existing.source_target_id = source_target_id
    else:
        reply_target = ReplyTarget(
            target_id=target_id,
            reply_type=ReplyType(reply_type),
            reply_to_channel_id=reply_to_channel_id,
            reply_to_message_id=reply_to_message_id,
            source_target_id=source_target_id,
        )
        session.add(reply_target)

    await session.flush()


async def orm_delete_reply_target(session: AsyncSession, *, target_id: int) -> None:
    """Удаляет настройки ответного поста."""
    from database.models import ReplyTarget
    from sqlalchemy import delete

    await session.execute(
        delete(ReplyTarget).where(ReplyTarget.target_id == target_id)
    )
    await session.flush()

async def orm_get_channel(session: AsyncSession, *, channel_id: int):
    """Получает канал по ID."""
    return await session.get(Channel, channel_id)


async def orm_get_channels_targets_for_date(
        session: AsyncSession,
        *,
        channel_ids: list[int],
        target_date: date,
) -> list:
    """Получает все targets для нескольких каналов на конкретную дату."""
    if not channel_ids:
        return []

    start_of_day = datetime.combine(target_date, datetime.min.time())
    end_of_day = datetime.combine(target_date, datetime.max.time())

    q = (
        select(PostTarget)
        .where(PostTarget.channel_id.in_(channel_ids))
        .where(
            or_(
                and_(
                    PostTarget.state.in_([TargetState.scheduled, TargetState.queued]),
                    PostTarget.publish_at >= start_of_day,
                    PostTarget.publish_at <= end_of_day,
                ),
                and_(
                    PostTarget.state == TargetState.sent,
                    PostTarget.sent_at >= start_of_day,
                    PostTarget.sent_at <= end_of_day,
                ),
            )
        )
        .options(
            joinedload(PostTarget.post).selectinload(Post.media),
            joinedload(PostTarget.channel),
        )
        .order_by(
            func.coalesce(PostTarget.publish_at, PostTarget.sent_at).asc()
        )
    )

    res = await session.execute(q)
    return list(res.scalars().unique().all())


async def orm_get_dates_with_posts(
        session: AsyncSession,
        *,
        channel_ids: list[int],
        year: int,
        month: int,
) -> dict[int, int]:
    """Возвращает словарь {день: количество_постов} для календаря."""
    if not channel_ids:
        return {}

    start_date = datetime(year, month, 1)
    if month == 12:
        end_date = datetime(year + 1, 1, 1)
    else:
        end_date = datetime(year, month + 1, 1)

    q_scheduled = (
        select(
            func.extract('day', PostTarget.publish_at).label('day'),
            func.count(PostTarget.id).label('cnt')
        )
        .where(PostTarget.channel_id.in_(channel_ids))
        .where(PostTarget.state.in_([TargetState.scheduled, TargetState.queued]))
        .where(PostTarget.publish_at >= start_date)
        .where(PostTarget.publish_at < end_date)
        .group_by(func.extract('day', PostTarget.publish_at))
    )

    q_sent = (
        select(
            func.extract('day', PostTarget.sent_at).label('day'),
            func.count(PostTarget.id).label('cnt')
        )
        .where(PostTarget.channel_id.in_(channel_ids))
        .where(PostTarget.state == TargetState.sent)
        .where(PostTarget.sent_at >= start_date)
        .where(PostTarget.sent_at < end_date)
        .group_by(func.extract('day', PostTarget.sent_at))
    )

    result = {}

    res_scheduled = await session.execute(q_scheduled)
    for row in res_scheduled:
        day = int(row.day)
        result[day] = result.get(day, 0) + row.cnt

    res_sent = await session.execute(q_sent)
    for row in res_sent:
        day = int(row.day)
        result[day] = result.get(day, 0) + row.cnt

    return result


async def orm_get_scheduled_dates_with_count(
        session: AsyncSession,
        *,
        channel_ids: list[int],
) -> list[tuple[date, int]]:
    """Возвращает список (дата, количество_постов) для всех дат с запланированными постами."""
    if not channel_ids:
        return []

    q = (
        select(
            func.date(PostTarget.publish_at).label('dt'),
            func.count(PostTarget.id).label('cnt')
        )
        .where(PostTarget.channel_id.in_(channel_ids))
        .where(PostTarget.state.in_([TargetState.scheduled, TargetState.queued]))
        .where(PostTarget.publish_at.is_not(None))
        .group_by(func.date(PostTarget.publish_at))
        .order_by(func.date(PostTarget.publish_at).asc())
    )

    res = await session.execute(q)
    return [(row.dt, row.cnt) for row in res]


async def orm_delete_target(
        session: AsyncSession,
        *,
        target_id: int,
) -> None:
    """Удаляет target."""
    t = await session.get(PostTarget, target_id)
    if not t:
        return

    post_id = t.post_id
    await session.delete(t)
    await session.flush()

    # Проверяем, остались ли другие targets у поста
    q = select(func.count(PostTarget.id)).where(PostTarget.post_id == post_id)
    count = await session.scalar(q)

    if count == 0:
        post = await session.get(Post, post_id)
        if post:
            await session.delete(post)

    await session.flush()
