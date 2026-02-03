from aiogram import Router, F, types, Bot
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from database.models import PostTarget, Channel
from filters.chat_types import ChatTypeFilter

# from database.models import Channel, PostTarget, Post

comments_router = Router()
# Этот роутер обрабатывает сообщения в группах/супергруппах
comments_router.message.filter(ChatTypeFilter(["group", "supergroup"]))


@comments_router.message()
async def test_all_messages(message: types.Message):
    print(f"[TEST] chat_id={message.chat.id}, type={message.chat.type}")
    print(f"[TEST] reply_to_message = {message.reply_to_message}")
    print(f"[TEST] is_topic_message = {getattr(message, 'is_topic_message', None)}")
    print(f"[TEST] message_thread_id = {getattr(message, 'message_thread_id', None)}")

    if message.reply_to_message:
        print(f"[TEST] reply_to sender_chat = {message.reply_to_message.sender_chat}")


@comments_router.message()
async def check_and_delete_comment(message: types.Message, session: AsyncSession):
    """Проверяет и удаляет комментарий если comments_enabled=False."""

    # Пропускаем автоматически пересланные посты из канала
    if getattr(message, 'is_automatic_forward', False):
        print(f"[DEBUG] Пропускаем автопересылку")
        return

    chat_id = message.chat.id
    print(f"[DEBUG 1] Сообщение в чате {chat_id}")

    # Проверяем что это комментарий (есть message_thread_id)
    thread_id = getattr(message, 'message_thread_id', None)
    if not thread_id:
        print(f"[DEBUG 1.1] Нет thread_id - это не комментарий")
        return

    print(f"[DEBUG 2] thread_id = {thread_id}")

    # Ищем канал по linked_chat_id
    result = await session.execute(
        select(Channel).where(Channel.linked_chat_id == chat_id)
    )
    channel = result.scalar_one_or_none()

    if not channel:
        print(f"[DEBUG 3] Канал не найден для linked_chat_id={chat_id}")
        return

    print(f"[DEBUG 4] Канал: {channel.title}, id={channel.id}")

    # Ищем пост по thread_id (message_id пересланного поста в группе = thread_id)
    # Или по forward_from_message_id из reply_to_message

    channel_msg_id = None
    if message.reply_to_message:
        channel_msg_id = getattr(message.reply_to_message, 'forward_from_message_id', None)
        print(f"[DEBUG 5] forward_from_message_id = {channel_msg_id}")

    if not channel_msg_id:
        print(f"[DEBUG 5.1] Не удалось определить ID поста в канале")
        return

    # Ищем пост
    target_result = await session.execute(
        select(PostTarget)
        .where(PostTarget.channel_id == channel.id)
        .where(PostTarget.sent_message_id == channel_msg_id)
        .options(selectinload(PostTarget.post))
    )
    target = target_result.scalar_one_or_none()

    if not target or not target.post:
        print(f"[DEBUG 6] Пост не найден: channel_id={channel.id}, sent_message_id={channel_msg_id}")
        return

    print(f"[DEBUG 7] Пост найден: id={target.post.id}, comments_enabled={target.post.comments_enabled}")

    if target.post.comments_enabled:
        print(f"[DEBUG 7.1] Комментарии включены - не удаляем")
        return

    # Удаляем!
    print(f"[DEBUG 8] УДАЛЯЕМ комментарий!")
    try:
        await message.delete()
        print(f"[DEBUG 9] Удалено!")
    except Exception as e:
        print(f"[DEBUG 9] Ошибка: {e}")


async def update_channel_linked_chat(
        session: AsyncSession,
        bot: Bot,
        channel_id: int
) -> int | None:
    """
    Получает и сохраняет ID привязанного чата обсуждения для канала.
    Вызывать при добавлении канала или периодически.
    """
    from database.models import Channel

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
    """
    Проверяет, может ли бот удалять сообщения в чате.
    """
    try:
        me = await bot.get_me()
        member = await bot.get_chat_member(chat_id, me.id)

        if hasattr(member, 'can_delete_messages'):
            return member.can_delete_messages

        # Для creator всегда True
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
        chat_id: int  # куда отправить предупреждение
) -> bool:
    """
    Показывает предупреждение если у бота нет прав удалять сообщения.
    Возвращает True если предупреждение показано.
    """
    from database.models import Channel

    # Получаем linked_chat_id
    result = await session.execute(
        select(Channel.linked_chat_id).where(Channel.id == channel_id)
    )
    row = result.first()

    if not row or not row[0]:
        # Нет привязанного чата - обновляем
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

    # Проверяем права
    can_delete = await check_bot_can_delete_in_chat(bot, linked_chat_id)

    if not can_delete:
        await bot.send_message(chat_id, COMMENTS_DISABLED_WARNING, parse_mode="HTML")
        return True

    return False
