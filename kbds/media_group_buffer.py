from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from aiogram.fsm.context import FSMContext
from aiogram.types import Message, InputMediaPhoto, InputMediaVideo, InputMediaDocument, InputMediaAudio
from sqlalchemy.ext.asyncio import AsyncSession

from database.orm_query import orm_create_post_from_album
from kbds.post_editor import EditorState, editor_state_to_dict, build_editor_kb, make_ctx_from_message

ALBUM_WAIT_SECONDS = 1.0
@dataclass
class AlbumBucket:
    messages: List[Message] = field(default_factory=list)
    task: asyncio.Task | None = None


class MediaGroupBuffer:
    """
    Буфер для сборки альбомов.
    Ключ: (chat_id, user_id, media_group_id)
    """
    def __init__(self) -> None:
        self._buckets: Dict[Tuple[int, int, str], AlbumBucket] = {}

    def add(self, key: Tuple[int, int, str], msg: Message) -> AlbumBucket:
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = AlbumBucket()
            self._buckets[key] = bucket
        bucket.messages.append(msg)
        return bucket

    def pop(self, key: Tuple[int, int, str]) -> AlbumBucket | None:
        return self._buckets.pop(key, None)

MEDIA_GROUP_BUFFER = MediaGroupBuffer()


from aiogram.types import Message

async def _finalize_album(key: tuple[int, int, str], state: FSMContext, session: AsyncSession):
    # ждём, пока Telegram пришлёт все элементы группы
    await asyncio.sleep(ALBUM_WAIT_SECONDS)

    bucket = MEDIA_GROUP_BUFFER.pop(key)
    if not bucket or not bucket.messages:
        return

    # сортировка по message_id, чтобы копировать в правильном порядке
    album_msgs: list[Message] = sorted(bucket.messages, key=lambda m: m.message_id)


    chat_id = album_msgs[0].chat.id
    bot = album_msgs[0].bot
    first_msg = album_msgs[0] if album_msgs else None
    if first_msg:
        ctx = make_ctx_from_message(first_msg)
        await state.update_data(editor_context=ctx)

    data = await state.get_data()
    selected_ids = set(data.get("selected_channel_ids") or [])
    if not selected_ids:
        # пользователь мог “сбросить” стейт — в этом случае просто ничего не делаем
        return


    # 1) создаём пост в БД как "album"
    # Минимально можно создать по первой записи (caption/текст берём из первой части)
    # Позже расширим: сохранять список media file_id в PostMedia.
    post_id = await orm_create_post_from_album(
        session=session,
        user_id=album_msgs[0].from_user.id,
        messages=album_msgs,
        channel_ids=selected_ids,
    )
    await session.commit()

    # 2) копируем все части альбома (будет “дублированный альбом”)
    sent_messages = await _send_album_as_group(bot=bot, chat_id=chat_id, album_msgs=album_msgs)

    # 3) отдельное сообщение “Настройте пост…“ + клавиатура (как в Posted)
    settings_msg = await bot.send_message(
        chat_id=chat_id,
        text="Настройте пост перед публикацией.",
    )
    target_msg = sent_messages[0] if sent_messages else None
    caption_msg = None
    for msg in album_msgs:
        if msg.caption:
            caption_msg = msg
            break

    if target_msg:
        st = EditorState(
            post_id=int(post_id),
            preview_chat_id=chat_id,
            preview_message_id=settings_msg.message_id,  # Кнопки на служебном сообщении
        )

        # Сохраняем информацию об альбоме
        caption_msg_id = None
        if sent_messages:
            if caption_msg:
                msg_index = album_msgs.index(caption_msg)
                if msg_index < len(sent_messages):
                    caption_msg_id = sent_messages[msg_index].message_id
            else:
                # альбом без текста: по умолчанию вешаем caption на 1-й элемент
                caption_msg_id = sent_messages[0].message_id

        ctx = make_ctx_from_message(album_msgs[0])
        await state.update_data(
            editor=editor_state_to_dict(st),
            editor_context=ctx,
            editor_has_media=True,
            editor_mode="media_with_text" if any(m.caption for m in album_msgs) else "media_only",
            is_album=True,  # Флаг, что это альбом
            album_caption_message_id=caption_msg_id,  # ID сообщения альбома с подписью
        )

        # Повесить inline редактор под служебным сообщением
        await bot.edit_message_reply_markup(
            chat_id=chat_id,
            message_id=settings_msg.message_id,
            reply_markup=build_editor_kb(int(post_id), st, ctx=ctx),
        )

    st = EditorState(
        post_id=int(post_id),
        preview_chat_id=chat_id,
        preview_message_id=settings_msg.message_id,  # важный момент: кнопки висят тут
    )
    await state.update_data(
        editor=editor_state_to_dict(st),
        is_album=True,
        album_caption_message_id=caption_msg_id)

    await bot.edit_message_reply_markup(
        chat_id=chat_id,
        message_id=settings_msg.message_id,
        reply_markup=build_editor_kb(int(post_id), st, ctx=ctx),
    )

def _to_input_media(msg: Message):
    """
    Преобразует Message в InputMedia*.
    Caption Telegram разрешает только на одном элементе альбома — мы выставим его там же,
    где он был у пользователя (у того msg, где caption не None).
    """
    caption = msg.caption  # может быть None
    # если хочешь форматирование — добавь parse_mode="HTML"/"MarkdownV2" единообразно

    if msg.photo:
        return InputMediaPhoto(media=msg.photo[-1].file_id, caption=caption)
    if msg.video:
        return InputMediaVideo(media=msg.video.file_id, caption=caption)
    if msg.document:
        return InputMediaDocument(media=msg.document.file_id, caption=caption)
    if msg.audio:
        return InputMediaAudio(media=msg.audio.file_id, caption=caption)

    # если попалось что-то, что нельзя в album — вернём None
    return None


async def _send_album_as_group(bot, chat_id: int, album_msgs: list[Message]):
    """
    Отправляет альбом одной группой. Caption оставляем там, где он был.
    Важно: Telegram не примет caption на нескольких элементах — поэтому перед отправкой
    обнулим caption на остальных, если вдруг клиент прислал иначе.
    """
    media = []
    caption_index = None

    for i, m in enumerate(album_msgs):
        im = _to_input_media(m)
        if im is None:
            continue
        if getattr(im, "caption", None):
            caption_index = i
        media.append(im)

    if not media:
        return

    # Telegram допускает caption только на одном элементе
    if caption_index is not None:
        for i, im in enumerate(media):
            if i != caption_index:
                im.caption = None

    return await bot.send_media_group(chat_id=chat_id, media=media)