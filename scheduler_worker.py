import asyncio
from datetime import datetime
from typing import Iterable

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InputMediaPhoto,
    InputMediaVideo,
    InputMediaDocument,
    InputMediaAnimation,
)

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from database.models import PostTarget, TargetState, MediaType, PostEventType, PostEvent
from database.orm_query import (
    orm_pick_targets_to_publish,
    orm_get_target_full,
    orm_mark_target_sent,
    orm_mark_target_failed,
    orm_pick_targets_to_autodelete,
    orm_mark_target_autodeleted,
    orm_log_post_event,
)


def _build_url_kb(buttons) -> InlineKeyboardMarkup | None:
    if not buttons:
        return None
    # buttons: list[PostButton] row/position
    rows_map: dict[int, list[InlineKeyboardButton]] = {}
    for b in buttons:
        rows_map.setdefault(int(b.row), [])
        rows_map[int(b.row)].append(
            InlineKeyboardButton(text=b.text, url=b.url)
        )
    # сортировка по position
    kb_rows = []
    for row in sorted(rows_map.keys()):
        kb_rows.append(sorted(rows_map[row], key=lambda x: x.text))  # текст не идеален, но стабильно
    return InlineKeyboardMarkup(inline_keyboard=kb_rows)


def _media_to_input(media, caption: str | None, is_first: bool, show_caption_above: bool = False):
    """Конвертирует медиа в InputMedia для альбома."""
    cap = caption if is_first else None
    mt = media.media_type

    if mt == MediaType.photo:
        return InputMediaPhoto(
            media=media.file_id,
            caption=cap,
            show_caption_above_media=show_caption_above if is_first else False
        )
    if mt == MediaType.video:
        return InputMediaVideo(
            media=media.file_id,
            caption=cap,
            show_caption_above_media=show_caption_above if is_first else False
        )
    if mt == MediaType.document:
        return InputMediaDocument(media=media.file_id, caption=cap)
    if mt == MediaType.gif:
        return InputMediaAnimation(
            media=media.file_id,
            caption=cap,
            show_caption_above_media=show_caption_above if is_first else False
        )
    return None


async def _send_target(bot: Bot, t_full: PostTarget) -> list[int]:
    """
    Возвращает список message_id отправленных сообщений (для альбома их несколько).
    """
    post = t_full.post
    kb = _build_post_kb(post)

    text = post.text or ""
    text_position = getattr(post, 'text_position', 'bottom') or 'bottom'
    show_caption_above = (text_position == "top")
    sent_ids = []
    reply_to_message_id = None
    if t_full.reply:
        reply_to_message_id = t_full.reply.reply_to_message_id

    if post.media and len(post.media) > 1:
        media_sorted = sorted(post.media, key=lambda m: int(m.order_index))

        input_media = []
        for i, m in enumerate(media_sorted):
            cap = text if i == 0 and text else None
            im = _media_to_input(m, caption=cap, is_first=(i == 0), show_caption_above=show_caption_above)
            if im is not None:
                input_media.append(im)

        msgs = await bot.send_media_group(
            chat_id=t_full.channel_id,
            media=input_media,
            disable_notification=bool(post.silent),
            protect_content=bool(post.protected),
            reply_to_message_id=reply_to_message_id,
        )
        sent_ids = [m.message_id for m in msgs]

        # Закрепление
        if bool(post.pinned) and sent_ids:
            try:
                await bot.pin_chat_message(
                    chat_id=t_full.channel_id,
                    message_id=sent_ids[0],
                    disable_notification=True,
                )
            except TelegramBadRequest:
                pass

        # URL-кнопки отдельным сообщением (media_group не поддерживает inline kb)
        if kb is not None:
            m2 = await bot.send_message(
                chat_id=t_full.channel_id,
                text="​",  # Zero-width space
                reply_markup=kb,
                disable_notification=bool(post.silent),
                protect_content=bool(post.protected),
            )
            sent_ids.append(m2.message_id)

        return sent_ids

        # ==========================================================================
        # 2) ОДИН МЕДИА-ФАЙЛ
        # ==========================================================================
    if post.media and len(post.media) == 1:
        m = post.media[0]
        mt = m.media_type
        caption = text if text else None

        if mt == MediaType.photo:
            msg = await bot.send_photo(
                t_full.channel_id,
                photo=m.file_id,
                caption=caption,
                show_caption_above_media=show_caption_above,  # <-- Текст сверху!
                reply_markup=kb,
                disable_notification=bool(post.silent),
                protect_content=bool(post.protected),
                reply_to_message_id=reply_to_message_id,
            )
        elif mt == MediaType.video:
            msg = await bot.send_video(
                t_full.channel_id,
                video=m.file_id,
                caption=caption,
                show_caption_above_media=show_caption_above,  # <-- Текст сверху!
                reply_markup=kb,
                disable_notification=bool(post.silent),
                protect_content=bool(post.protected),
                reply_to_message_id=reply_to_message_id,
            )
        elif mt == MediaType.document:
            msg = await bot.send_document(
                t_full.channel_id,
                document=m.file_id,
                caption=caption,
                reply_markup=kb,
                disable_notification=bool(post.silent),
                protect_content=bool(post.protected),
                reply_to_message_id=reply_to_message_id,
            )
        elif mt == MediaType.gif:
            msg = await bot.send_animation(
                t_full.channel_id,
                animation=m.file_id,
                caption=caption,
                show_caption_above_media=show_caption_above,  # <-- Текст сверху!
                reply_markup=kb,
                disable_notification=bool(post.silent),
                protect_content=bool(post.protected),
                reply_to_message_id=reply_to_message_id,

            )
        elif mt == MediaType.voice:
            msg = await bot.send_voice(
                t_full.channel_id,
                voice=m.file_id,
                caption=caption,
                reply_markup=kb,
                disable_notification=bool(post.silent),
                protect_content=bool(post.protected),
                reply_to_message_id=reply_to_message_id,

            )
        else:
            msg = await bot.send_document(
                t_full.channel_id,
                document=m.file_id,
                caption=caption,
                reply_markup=kb,
                disable_notification=bool(post.silent),
                protect_content=bool(post.protected),
                reply_to_message_id=reply_to_message_id,

            )

        sent_ids.append(msg.message_id)

        # Закрепление
        if bool(post.pinned):
            try:
                await bot.pin_chat_message(
                    chat_id=t_full.channel_id,
                    message_id=msg.message_id,
                    disable_notification=True,
                )
            except TelegramBadRequest:
                pass

        return sent_ids

        # ==========================================================================
        # 3) ТОЛЬКО ТЕКСТ
        # ==========================================================================
    msg = await bot.send_message(
        chat_id=t_full.channel_id,
        text=text or "​",
        reply_markup=kb,
        disable_notification=bool(post.silent),
        protect_content=bool(post.protected),
        reply_to_message_id=reply_to_message_id,
    )
    sent_ids.append(msg.message_id)

    if bool(post.pinned):
        try:
            await bot.pin_chat_message(
                chat_id=t_full.channel_id,
                message_id=msg.message_id,
                disable_notification=True,
            )
        except TelegramBadRequest:
            pass

    return sent_ids

async def _pick_queued(session: AsyncSession, limit: int = 20) -> list[PostTarget]:
    q = (
        select(PostTarget)
        .where(PostTarget.state == TargetState.queued)
        .order_by(PostTarget.publish_at.asc().nullsfirst(), PostTarget.id.asc())
        .limit(limit)
    )
    res = await session.execute(q)
    return list(res.scalars().all())


async def _get_last_sent_ids(session: AsyncSession, target_id: int) -> list[int] | None:
    q = (
        select(PostEvent)
        .where(PostEvent.target_id == target_id)
        .where(PostEvent.event_type == PostEventType.sent)
        .order_by(PostEvent.created_at.desc())
        .limit(1)
    )
    res = await session.execute(q)
    ev = res.scalar_one_or_none()
    if not ev or not ev.payload:
        return None
    ids = ev.payload.get("sent_message_ids")
    if isinstance(ids, list) and all(isinstance(x, int) for x in ids):
        return ids
    return None



async def scheduler_loop(bot: Bot, session_maker: async_sessionmaker[AsyncSession], *, tick: float = 2.0):
    """
    1) scheduled->queued по publish_at
    2) отправка queued
    3) автоудаление по auto_delete_at
    """
    while True:
        try:
            async with session_maker() as session:
                # 1) scheduled -> queued
                await orm_pick_targets_to_publish(session, limit=50, now=datetime.utcnow())

                # 2) publish queued
                queued = await _pick_queued(session, limit=20)
                for t in queued:
                    try:
                        t_full = await orm_get_target_full(session, target_id=t.id)
                        sent_ids = await _send_target(bot, t_full)

                        await orm_mark_target_sent(session, target_id=t.id, sent_message_id=sent_ids[0])
                        await orm_log_post_event(
                            session,
                            post_id=t_full.post_id,
                            target_id=t_full.id,
                            actor_user_id=None,
                            event_type=PostEventType.sent,
                            payload={"sent_message_ids": sent_ids},
                        )
                    except Exception as e:
                        await orm_mark_target_failed(session, target_id=t.id, error=str(e))
                        await orm_log_post_event(
                            session,
                            post_id=t.post_id,
                            target_id=t.id,
                            actor_user_id=None,
                            event_type=PostEventType.failed,
                            payload={"error": str(e)},
                        )

                # 3) auto-delete
                to_del = await orm_pick_targets_to_autodelete(session, limit=50, now=datetime.utcnow())
                for t in to_del:
                    ids = await _get_last_sent_ids(session, t.id) or ([t.sent_message_id] if t.sent_message_id else [])
                    for mid in ids:
                        try:
                            await bot.delete_message(chat_id=t.channel_id, message_id=mid)
                        except TelegramBadRequest as e:
                            # если уже удалено/нет прав — не валим весь воркер
                            pass

                    await orm_mark_target_autodeleted(session, target_id=t.id)
                    await orm_log_post_event(
                        session,
                        post_id=t.post_id,
                        target_id=t.id,
                        actor_user_id=None,
                        event_type=PostEventType.auto_deleted,
                        payload={"deleted_message_ids": ids},
                    )

                await session.commit()

        except Exception as e:
            print(f"[publish] target={t.id} channel={t.channel_id} error={e}")

        await asyncio.sleep(tick)


def _build_post_kb(post) -> InlineKeyboardMarkup | None:
    """
    Строит клавиатуру поста:
    1. URL-кнопки пользователя (если есть)
    2. Кнопка скрытого продолжения (если есть)
    """
    kb_rows = []

    # 1. URL-кнопки
    if post.buttons:
        rows_map: dict[int, list] = {}
        for b in post.buttons:
            row_idx = int(b.row)
            if row_idx not in rows_map:
                rows_map[row_idx] = []
            rows_map[row_idx].append((int(b.position), b.text, b.url))

        for row_idx in sorted(rows_map.keys()):
            # Сортируем по position
            sorted_btns = sorted(rows_map[row_idx], key=lambda x: x[0])
            kb_rows.append([
                InlineKeyboardButton(text=text, url=url)
                for (_, text, url) in sorted_btns
            ])

    # Кнопка скрытого продолжения
    if post.hidden_part:
        button_text = post.hidden_part.button_text or "Читать продолжение"
        kb_rows.append([
            InlineKeyboardButton(
                text=f"🔒 {button_text}",
                callback_data=f"hidden:{post.id}"
            )
        ])

    if not kb_rows:
        return None

    return InlineKeyboardMarkup(inline_keyboard=kb_rows)
