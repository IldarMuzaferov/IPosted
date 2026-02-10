from aiogram import Router, F, types, Bot
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload
from database.models import PostTarget, Channel, Post
from filters.chat_types import ChatTypeFilter

comments_router = Router()
comments_router.message.filter(ChatTypeFilter(["group", "supergroup"]))


async def _find_target_by_message_id(
        session: AsyncSession,
        channel_id: int,
        msg_id: int
) -> PostTarget | None:
    """
    Ищет PostTarget по sent_message_id ИЛИ по source_message_id (для репостов).
    """
    # Сначала ищем по sent_message_id (обычные посты)
    res = await session.execute(
        select(PostTarget)
        .where(PostTarget.channel_id == channel_id)
        .where(PostTarget.sent_message_id == msg_id)
        .options(selectinload(PostTarget.post))
    )
    target = res.scalar_one_or_none()

    if target:
        return target

    # Если не нашли - ищем по source_message_id (репосты)
    # Для репостов forward_from_message_id = post.source_message_id
    res = await session.execute(
        select(PostTarget)
        .join(Post, PostTarget.post_id == Post.id)
        .where(PostTarget.channel_id == channel_id)
        .where(Post.is_repost == True)
        .where(Post.source_message_id == msg_id)
        .options(selectinload(PostTarget.post))
    )
    target = res.scalar_one_or_none()

    return target


@comments_router.message()
async def comments_guard(message: types.Message, session: AsyncSession):
    """
    Обработчик комментариев с поддержкой репостов.
    """
    chat_id = message.chat.id

    # === 1) АВТОПЕРЕСЫЛКА ИЗ КАНАЛА В ГРУППУ ===
    if getattr(message, "is_automatic_forward", False):
        sender_chat = getattr(message, "sender_chat", None)
        channel_id = getattr(sender_chat, "id", None) if sender_chat else None
        forward_msg_id = getattr(message, "forward_from_message_id", None)
        discussion_msg_id = message.message_id  # ID в группе обсуждения

        print(
            f"[COMMENTS] Автопересылка: channel={channel_id}, forward_id={forward_msg_id}, discussion_id={discussion_msg_id}")

        if not channel_id:
            return

        channel = await session.get(Channel, channel_id)
        if not channel:
            return

        target = None

        # Случай A: Обычный пост (forward_from_message_id есть)
        if forward_msg_id:
            res = await session.execute(
                select(PostTarget)
                .where(PostTarget.channel_id == channel_id)
                .where(PostTarget.sent_message_id == forward_msg_id)
                .options(selectinload(PostTarget.post))
            )
            target = res.scalar_one_or_none()

        # Случай B: Репост (forward_from_message_id = None)
        # Ищем последний target с is_repost=True где discussion_message_id ещё не заполнен
        if not target:
            res = await session.execute(
                select(PostTarget)
                .join(Post, PostTarget.post_id == Post.id)
                .where(PostTarget.channel_id == channel_id)
                .where(Post.is_repost == True)
                .where(PostTarget.discussion_message_id.is_(None))
                .where(PostTarget.sent_message_id.isnot(None))
                .options(selectinload(PostTarget.post))
                .order_by(PostTarget.id.desc())
                .limit(1)
            )
            target = res.scalar_one_or_none()

            # Сохраняем discussion_message_id для будущих поисков
            if target:
                target.discussion_message_id = discussion_msg_id
                await session.commit()
                print(f"[COMMENTS] Сохранён discussion_message_id={discussion_msg_id} для target_id={target.id}")

        if not target or not target.post:
            print(f"[COMMENTS] Target не найден")
            return

        print(
            f"[COMMENTS] Пост id={target.post.id}, is_repost={target.post.is_repost}, comments={target.post.comments_enabled}")

        if not target.post.comments_enabled:
            try:
                await message.delete()
                print("[COMMENTS] Удалён корень обсуждения")
            except Exception as e:
                print(f"[COMMENTS] Ошибка удаления: {e}")
        return

    # === 2) КОММЕНТАРИИ ПОЛЬЗОВАТЕЛЕЙ ===
    thread_id = getattr(message, "message_thread_id", None)
    reply_msg = message.reply_to_message

    if not thread_id and not reply_msg:
        return

    # Ищем канал по linked_chat_id
    res = await session.execute(
        select(Channel).where(Channel.linked_chat_id == chat_id)
    )
    channel = res.scalar_one_or_none()
    if not channel:
        return

    target = None

    # Способ A: По forward_from_message_id (обычные посты)
    if reply_msg:
        forward_msg_id = getattr(reply_msg, "forward_from_message_id", None)
        if forward_msg_id:
            res = await session.execute(
                select(PostTarget)
                .where(PostTarget.channel_id == channel.id)
                .where(PostTarget.sent_message_id == forward_msg_id)
                .options(selectinload(PostTarget.post))
            )
            target = res.scalar_one_or_none()

    # Способ B: По discussion_message_id (репосты)
    # thread_id = ID корневого сообщения в группе = discussion_message_id
    if not target and thread_id:
        res = await session.execute(
            select(PostTarget)
            .where(PostTarget.channel_id == channel.id)
            .where(PostTarget.discussion_message_id == thread_id)
            .options(selectinload(PostTarget.post))
        )
        target = res.scalar_one_or_none()

    if not target or not target.post:
        print(f"[COMMENTS] Target не найден для комментария")
        return

    print(f"[COMMENTS] Пост id={target.post.id}, comments={target.post.comments_enabled}")

    if not target.post.comments_enabled:
        try:
            await message.delete()
            print("[COMMENTS] Удалён комментарий")
        except Exception as e:
            print(f"[COMMENTS] Ошибка: {e}")


async def update_channel_linked_chat(
        session: AsyncSession,
        bot: Bot,
        channel_id: int
) -> int | None:
    """Получает и сохраняет linked_chat_id канала."""
    try:
        chat = await bot.get_chat(channel_id)
        linked_chat_id = getattr(chat, 'linked_chat_id', None)

        if linked_chat_id:
            await session.execute(
                update(Channel)
                .where(Channel.id == channel_id)
                .values(linked_chat_id=linked_chat_id)
            )
            await session.commit()

        return linked_chat_id
    except Exception:
        return None


async def check_bot_can_delete_in_chat(bot: Bot, chat_id: int) -> bool:
    """Проверяет, может ли бот удалять сообщения в чате."""
    try:
        me = await bot.get_me()
        member = await bot.get_chat_member(chat_id, me.id)

        if hasattr(member, 'can_delete_messages'):
            return member.can_delete_messages

        if member.status == "creator":
            return True

        return False
    except Exception:
        return False


COMMENTS_DISABLED_WARNING = """
⚠️ <b>Внимание!</b>

Для корректной работы блокировки комментариев, 
дайте боту право на <b>удаление сообщений</b> 
в прикреплённом к каналу чате обсуждения.

Без этого права бот не сможет удалять комментарии.
"""


async def show_comments_warning_if_needed(
        bot: Bot,
        session: AsyncSession,
        channel_id: int,
        chat_id: int
) -> bool:
    """Показывает предупреждение если у бота нет прав удалять сообщения."""
    result = await session.execute(
        select(Channel.linked_chat_id).where(Channel.id == channel_id)
    )
    row = result.first()

    if not row or not row[0]:
        linked_chat_id = await update_channel_linked_chat(session, bot, channel_id)
        if not linked_chat_id:
            await bot.send_message(
                chat_id,
                "⚠️ К каналу не привязан чат обсуждения. Комментарии нельзя заблокировать.",
                parse_mode="HTML"
            )
            return True
    else:
        linked_chat_id = row[0]

    can_delete = await check_bot_can_delete_in_chat(bot, linked_chat_id)

    if not can_delete:
        await bot.send_message(chat_id, COMMENTS_DISABLED_WARNING, parse_mode="HTML")
        return True

    return False
