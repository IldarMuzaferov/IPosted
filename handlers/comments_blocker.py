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


# @comments_router.message()
# async def test_all_messages(message: types.Message):
#     print(f"[TEST] chat_id={message.chat.id}, type={message.chat.type}")
#     print(f"[TEST] reply_to_message = {message.reply_to_message}")
#     print(f"[TEST] is_topic_message = {getattr(message, 'is_topic_message', None)}")
#     print(f"[TEST] message_thread_id = {getattr(message, 'message_thread_id', None)}")
#
#     if message.reply_to_message:
#         print(f"[TEST] reply_to sender_chat = {message.reply_to_message.sender_chat}")


@comments_router.message()
async def check_and_delete_comment(message: types.Message, session: AsyncSession):
    """Удаляет либо корневой автопост (чтобы вырубить комменты), либо сами комментарии."""

    chat_id = message.chat.id
    is_auto = bool(getattr(message, "is_automatic_forward", False))

    print(f"[DEBUG] msg chat_id={chat_id} auto={is_auto} mid={message.message_id} thread_id={getattr(message,'message_thread_id',None)}")

    # 1) Находим канал по linked_chat_id группы
    result = await session.execute(select(Channel).where(Channel.linked_chat_id == chat_id))
    channel = result.scalar_one_or_none()
    if not channel:
        print(f"[DEBUG] Канал не найден для linked_chat_id={chat_id}")
        return

    # 2) Определяем message_id поста в КАНАЛЕ (он же ключ для поиска PostTarget)
    channel_msg_id = None

    if is_auto:
        # Автопост в группе обсуждений
        channel_msg_id = getattr(message, "forward_from_message_id", None) or message.message_id
    else:
        # Комментарий в треде
        # Самый стабильный вариант — message_thread_id (в TG он равен id корневого сообщения)
        channel_msg_id = getattr(message, "message_thread_id", None)

        # fallback: если вдруг thread_id нет, попробуем через reply_to_message
        if not channel_msg_id and message.reply_to_message:
            channel_msg_id = getattr(message.reply_to_message, "forward_from_message_id", None) \
                             or getattr(message.reply_to_message, "message_id", None)

    if not channel_msg_id:
        print("[DEBUG] Не удалось определить channel_msg_id")
        return

    # 3) Ищем опубликованный target
    target_result = await session.execute(
        select(PostTarget)
        .where(PostTarget.channel_id == channel.id)
        .where(PostTarget.sent_message_id == int(channel_msg_id))
        .options(selectinload(PostTarget.post))
    )
    target = target_result.scalar_one_or_none()

    if not target or not target.post:
        print(f"[DEBUG] Target не найден: channel_id={channel.id}, sent_message_id={channel_msg_id}")
        return

    if target.post.comments_enabled:
        print("[DEBUG] comments_enabled=True -> ничего не удаляем")
        return

    # 4) Удаление
    try:
        if is_auto:
            print("[DEBUG] comments_enabled=False -> удаляем КОРЕНЬ треда (автопост) чтобы отключить комментарии")
        else:
            print("[DEBUG] comments_enabled=False -> удаляем комментарий")
        await message.delete()
    except Exception as e:
        print(f"[DEBUG] Ошибка удаления: {e}")


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

@comments_router.message()
async def comments_guard(message: types.Message, session: AsyncSession):
    chat_id = message.chat.id

    # 1) Ловим автопересылку поста из канала в группу обсуждения
    if getattr(message, "is_automatic_forward", False):
        sender_chat = getattr(message, "sender_chat", None)  # у автопересылки это канал
        channel_id = getattr(sender_chat, "id", None)
        channel_msg_id = getattr(message, "forward_from_message_id", None)

        print(f"[DBG-AF] linked_chat={chat_id} sender_chat={channel_id} channel_msg_id={channel_msg_id}")

        if not channel_id or not channel_msg_id:
            return

        # Находим канал (можно и по id, и по linked_chat_id — но по sender_chat.id надёжнее)
        channel = await session.get(Channel, channel_id)
        if not channel:
            return

        # Находим PostTarget по (channel_id, sent_message_id)
        res = await session.execute(
            select(PostTarget)
            .where(PostTarget.channel_id == channel.id)
            .where(PostTarget.sent_message_id == channel_msg_id)
            .options(selectinload(PostTarget.post))
        )
        target = res.scalar_one_or_none()
        if not target or not target.post:
            print("[DBG-AF] target/post not found")
            return

        print(f"[DBG-AF] post_id={target.post.id} comments_enabled={target.post.comments_enabled}")

        if not target.post.comments_enabled:
            try:
                await message.delete()  # <-- удаляем корень обсуждения => комментарии исчезают
                print("[DBG-AF] deleted auto-forward root message")
            except Exception as e:
                print(f"[DBG-AF] delete failed: {e}")

        return

    # 2) Ловим обычные комментарии (ответы) и удаляем, если надо
    thread_id = getattr(message, "message_thread_id", None)
    if not thread_id:
        return

    if not message.reply_to_message:
        return

    channel_msg_id = getattr(message.reply_to_message, "forward_from_message_id", None)
    if not channel_msg_id:
        return

    # Ищем канал по linked_chat_id
    res = await session.execute(select(Channel).where(Channel.linked_chat_id == chat_id))
    channel = res.scalar_one_or_none()
    if not channel:
        return

    res = await session.execute(
        select(PostTarget)
        .where(PostTarget.channel_id == channel.id)
        .where(PostTarget.sent_message_id == channel_msg_id)
        .options(selectinload(PostTarget.post))
    )
    target = res.scalar_one_or_none()
    if not target or not target.post:
        return

    if not target.post.comments_enabled:
        try:
            await message.delete()
            print("[DBG-CM] deleted comment")
        except Exception as e:
            print(f"[DBG-CM] delete comment failed: {e}")
