import asyncio
import json
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
    InputMediaAnimation, MessageEntity,
)

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from database.engine import session_maker
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
import logging

from kbds.callbacks import ReactionCD

logger = logging.getLogger(__name__)


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
    # В начале _send_target добавь:
    bot_info = await bot.get_me()
    print(f"Bot has premium: {bot_info.is_premium}")
    post = t_full.post
    if post.is_repost and post.source_chat_id and post.source_message_id:
        try:
            msg = await bot.forward_message(
                chat_id=t_full.channel_id,
                from_chat_id=post.source_chat_id,
                message_id=post.source_message_id,
                disable_notification=bool(post.silent),
                protect_content=bool(post.protected),
            )


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

            return [msg.message_id]

        except TelegramBadRequest as e:
            print(f"[REPOST] Forward failed: {e}, using normal send")

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
        html_text, parse_mode = _convert_to_html_with_emoji(text, post.text_entities)
        сaption = html_text or caption


        if mt == MediaType.photo:
            msg = await bot.send_photo(
                t_full.channel_id,
                photo=m.file_id,
                caption=html_text or caption,
                parse_mode=parse_mode,
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
                caption=html_text or caption,
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
                caption=html_text or caption,
                reply_markup=kb,
                disable_notification=bool(post.silent),
                protect_content=bool(post.protected),
                reply_to_message_id=reply_to_message_id,
            )
        elif mt == MediaType.gif:
            msg = await bot.send_animation(
                t_full.channel_id,
                animation=m.file_id,
                caption=html_text or caption,
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
                caption=html_text or caption,
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
    html_text, parse_mode = _convert_to_html_with_emoji(text, post.text_entities)
    print(f"=== КОНВЕРТАЦИЯ ===")
    print(f"HTML: {html_text}")

    # Прямо перед отправкой:
    final_text = html_text or text or "​"
    print(f"=== ОТПРАВКА ===")
    print(f"final_text: {final_text}")
    print(f"parse_mode: {parse_mode}")
    msg = await bot.send_message(
        chat_id=t_full.channel_id,
        text=final_text or "​",
        parse_mode=parse_mode,
        reply_markup=kb,
        disable_notification=bool(post.silent),
        protect_content=bool(post.protected),
        reply_to_message_id=reply_to_message_id,
    )
    sent_ids.append(msg.message_id)
    print(f"=== ОТПРАВКА ПОСТА ===")
    print(f"post.text_entities: {post.text_entities}")
    entities = _parse_entities(post.text_entities)
    print(f"parsed entities: {entities}")
    # Добавь тестовый код:
    test_text = 'Тест <tg-emoji emoji-id="5368324170671202286">✅</tg-emoji> premium emoji'
    await bot.send_message(
        chat_id=t_full.channel_id,
        text=test_text,
        parse_mode="HTML"
    )

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


        # 3. Кнопки реакций
    if hasattr(post, 'reaction_buttons') and post.reaction_buttons:
        reaction_rows_map: dict[int, list] = {}
        for btn in post.reaction_buttons:
            if btn.row not in reaction_rows_map:
                reaction_rows_map[btn.row] = []
            reaction_rows_map[btn.row].append(btn)

        for row_idx in sorted(reaction_rows_map.keys()):
            sorted_btns = sorted(reaction_rows_map[row_idx], key=lambda b: b.position)
            reaction_row = []
            for btn in sorted_btns:
                count_str = f" {btn.click_count}" if btn.click_count > 0 else ""
                reaction_row.append(
                    InlineKeyboardButton(
                        text=f"{btn.emoji}{count_str}",
                        callback_data=ReactionCD(button_id=btn.id).pack()
                    )
                )
            if reaction_row:
                kb_rows.append(reaction_row)

    return InlineKeyboardMarkup(inline_keyboard=kb_rows) if kb_rows else None

async def check_auto_delete(bot: Bot):
    """
    Фоновая задача для автоудаления постов.
    Проверяет каждые 30 секунд.
    """
    logger.info("Auto-delete task started")

    while True:
        try:
            async with session_maker() as session:
                now = datetime.utcnow()

                # Находим посты для удаления
                q = (
                    select(PostTarget)
                    .where(PostTarget.auto_deleted == False)
                    .where(PostTarget.auto_delete_at.isnot(None))
                    .where(PostTarget.auto_delete_at <= now)
                    .where(PostTarget.state == TargetState.sent)
                    .where(PostTarget.sent_message_id.isnot(None))
                )

                result = await session.execute(q)
                targets = result.scalars().all()

                for target in targets:
                    try:
                        # Удаляем сообщение из канала
                        await bot.delete_message(
                            chat_id=target.channel_id,
                            message_id=target.sent_message_id,
                        )

                        # Помечаем как удалённое
                        target.auto_deleted = True

                        logger.info(f"Auto-deleted message {target.sent_message_id} from channel {target.channel_id}")

                    except Exception as e:
                        error_msg = str(e).lower()
                        logger.error(f"Failed to auto-delete target {target.id}: {e}")

                        # Если сообщение уже удалено - помечаем как удалённое
                        if "message to delete not found" in error_msg or "message can't be deleted" in error_msg:
                            target.auto_deleted = True
                            logger.info(f"Marked target {target.id} as deleted (message already gone)")

                await session.commit()

        except Exception as e:
            logger.error(f"Error in check_auto_delete: {e}")

        # Ждём 30 секунд
        await asyncio.sleep(30)

def _parse_entities(entities_json: str | None) -> list[MessageEntity] | None:
    """Парсит JSON entities обратно в объекты MessageEntity."""
    if not entities_json:
        return None
    try:
        data = json.loads(entities_json)
        return [MessageEntity(**e) for e in data]
    except:
        return None


def _convert_to_html_with_emoji(text: str, entities_json: str | None) -> tuple[str, str | None]:
    """
    Конвертирует текст с entities в HTML с <tg-emoji> тегами.
    """
    if not text or not entities_json:
        return text, None

    try:
        entities = json.loads(entities_json)
    except:
        return text, None

    # Фильтруем только custom_emoji
    custom_emojis = [e for e in entities if e.get("type") == "custom_emoji"]

    if not custom_emojis:
        return text, None

    # Сортируем по offset в обратном порядке
    custom_emojis.sort(key=lambda e: e["offset"], reverse=True)

    # Работаем с UTF-16 (Telegram использует UTF-16 для offset)
    text_utf16 = text.encode('utf-16-le')

    for e in custom_emojis:
        offset = e["offset"]
        length = e["length"]
        emoji_id = e.get("custom_emoji_id")

        if not emoji_id:
            continue

        # UTF-16 LE: 2 байта на code unit
        start_bytes = offset * 2
        end_bytes = (offset + length) * 2

        # Извлекаем эмодзи
        emoji_bytes = text_utf16[start_bytes:end_bytes]
        original_emoji = emoji_bytes.decode('utf-16-le')

        # HTML тег
        html_tag = f'<tg-emoji emoji-id="{emoji_id}">{original_emoji}</tg-emoji>'.encode('utf-16-le')

        # Заменяем в UTF-16
        text_utf16 = text_utf16[:start_bytes] + html_tag + text_utf16[end_bytes:]

    result = text_utf16.decode('utf-16-le')
    return result, "HTML"