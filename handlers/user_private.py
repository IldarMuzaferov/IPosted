import asyncio
import re
from typing import Optional, Tuple

from aiogram import F, types, Router, Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart, StateFilter
from aiogram.filters.callback_data import CallbackData
from sqlalchemy.ext.asyncio import AsyncSession
from aiogram.types import Message, CallbackQuery, ContentType, ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext

from database.models import TgMemberStatus
from database.orm_query import orm_get_user_channels, orm_get_free_channels_for_user, orm_get_folder_channels, \
    orm_get_user_folders, orm_add_channel_admin, orm_upsert_channel, orm_upsert_user, orm_create_post_from_message, \
    orm_edit_post_text, orm_add_media_to_post
from filters.chat_types import ChatTypeFilter
from kbds.callbacks import CreatePostCD, CreatePostStates, ConnectChannelStates, EditTextStates, AttachMediaStates, \
    UrlButtonsStates
from kbds.inline import get_callback_btns, get_url_btns, get_inlineMix_btns, ik_channels_picker, ik_create_post_menu, \
    ik_create_root_menu, ik_channels_menu, ik_folders_menu, ik_after_channel_connected, ik_folders_empty, \
    ik_folder_channels, ik_folders_list, ik_edit_text_controls, ik_attach_media_controls
from datetime import datetime
# from main import bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import datetime as dt
from create_bot import bot
from datetime import datetime as dt_utc
from kbds.post_editor import CopyPostCD, build_copy_channels_kb, UrlButtonsCD
from database.orm_query import orm_get_all_user_channels, orm_copy_post_to_channels


from datetime import datetime, timedelta
import logging

from kbds.media_group_buffer import MEDIA_GROUP_BUFFER, _finalize_album
from kbds.post_editor import editor_state_to_dict, build_editor_kb, EditorState, TOGGLE_KEYS, editor_state_from_dict, \
    EditorCD, EditTextCD, make_ctx_from_message, CopyPostCD

from kbds.post_editor import UrlButtonsCD, build_url_buttons_prompt_kb, merge_url_and_editor_kb
from database.orm_query import orm_save_post_buttons, orm_delete_post_buttons, orm_get_post_buttons

user_private_router = Router()
user_private_router.message.filter(ChatTypeFilter(["private"]))


START_TEXT = (
    "✅ Posted - это простой и удобный бот для отложенного постинга, поддерживающий работу с ⭐️ анимированными эмодзи.\n\n"
    "Бот позволяет:\n\n"
    "🕔 Планировать выход публикаций в ваших каналах\n"
    "🗑 Автоматически удалять их по таймеру\n"
    "👩‍🎨 Создавать и настраивать посты любого формата\n"
    "🔄 Зацикливать публикации, добавлять кнопки и водяные знаки\n"
    "👀 И многое другое"
)

NO_CHANNELS_TEXT = (
    "У вас пока нет подключенных каналов.\n\n"
    "Чтобы подключить первый канал:\n\n"
    "1. Сделайте @IPostedBot администратором канала, дав следующие права:\n\n"
    "✅ Отправка сообщений\n"
    "✅ Удаление сообщений\n"
    "✅ Редактирование сообщений\n\n"
    "2. Перешлите в диалог с ботом любое сообщение из канала."
)

def connected_text(title: str, url: str) -> str:
    return (
        f"✅ Вы успешно подключили канал {title} ({url}) к Posted.\n\n"
        f"Чтобы дать другому пользователю возможность работать с каналом, добавьте его в канал {title} ({url}) "
        "в качестве администратора, дав права на:\n\n"
        "✅ Отправку сообщений\n"
        "✅ Удаление сообщений\n"
        "✅ Редактирование сообщений"
    )

@user_private_router.message(CommandStart())
async def cmd_start(message: types.Message, session: AsyncSession):
    # опционально: регистрация/обновление юзера в БД
    # await orm_upsert_user(
    #     session,
    #     user_id=message.from_user.id,
    #     username=message.from_user.username,
    #     first_name=message.from_user.first_name,
    # )

    await message.answer(START_TEXT, reply_markup=main_reply_kb())

def main_reply_kb() -> ReplyKeyboardMarkup:
    # 4 постоянные реплай-кнопки по ТЗ :contentReference[oaicite:2]{index=2}
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Создать пост"), KeyboardButton(text="Настройки")],
            [KeyboardButton(text="Изменить пост"), KeyboardButton(text="Контент-план")],
        ],
        resize_keyboard=True,
        selective=False,
        input_field_placeholder="Выберите действие",
    )


@user_private_router.message(F.text == "Создать пост")
async def on_create_post(message: types.Message, state: FSMContext, session: AsyncSession):
    await state.set_state(CreatePostStates.choosing_channels)
    await state.update_data(selected_channel_ids=set(), last_scope="root")

    await message.answer(
        "Куда будем публиковать пост?",
        reply_markup=ik_create_root_menu(),
    )


@user_private_router.callback_query(CreatePostCD.filter(F.action == "back"))
async def cp_back(call: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    last_scope = data.get("last_scope", "root")

    if last_scope == "channels_menu" or last_scope == "folders_menu":
        # Возвращаемся в главное меню создания поста
        await state.update_data(last_scope="root")
        await call.message.edit_text(
            "Куда будем публиковать пост?",
            reply_markup=ik_create_root_menu(),
        )
    elif last_scope == "menu":
        # Возврат из старого меню (для обратной совместимости)
        folders = await orm_get_user_folders(session, user_id=call.from_user.id)
        await call.message.edit_text(
            "Куда будем публиковать пост?",
            reply_markup=ik_create_post_menu(folders, has_free=True),
        )
    else:
        # По умолчанию возвращаемся в корневое меню
        await state.update_data(last_scope="root")
        await call.message.edit_text(
            "Куда будем публиковать пост?",
            reply_markup=ik_create_root_menu(),
        )

    await call.answer()

@user_private_router.callback_query(CreatePostCD.filter(F.action == "channels_menu"))
async def cp_channels_menu(call: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    channels = await orm_get_free_channels_for_user(session, user_id=call.from_user.id)
    await state.update_data(last_scope="channels_menu")

    if not channels:
        await state.set_state(ConnectChannelStates.waiting_channel)
        await call.message.edit_text(NO_CHANNELS_TEXT)
        await call.answer()
        return

    # если каналы есть — оставляй твою текущую логику меню
    await call.message.edit_text(f"⬆️ СОЗДАНИЕ ПОСТА \n Выберите канал, в котором хотите создать публикацию.", reply_markup=ik_channels_menu(channels))
    await call.answer()

@user_private_router.callback_query(CreatePostCD.filter(F.action == "open_folder"))
async def cp_open_folder(call: types.CallbackQuery, callback_data: CreatePostCD, state: FSMContext, session: AsyncSession):
    folder_id = int(callback_data.folder_id)

    channels = await orm_get_folder_channels(session, user_id=call.from_user.id, folder_id=folder_id)
    await state.update_data(last_scope="folder", last_folder_id=folder_id)

    if not channels:
        await call.message.edit_text(
            "В этой папке пока нет каналов.\n\nДобавить каналы в папку можно в «Настройках».",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data=CreatePostCD(action="folders_menu").pack())]
            ]),
        )
        await call.answer()
        return

    await call.message.edit_text(
        "Выберите канал или нажмите «Во всех сразу»:",
        reply_markup=ik_folder_channels(folder_id, channels),
    )
    await call.answer()

@user_private_router.callback_query(CreatePostCD.filter(F.action == "pick_folder_channel"))
async def cp_pick_folder_channel(call: types.CallbackQuery, callback_data: CreatePostCD, state: FSMContext):
    channel_id = int(callback_data.channel_id)

    # сохраняем выбранный канал
    await state.update_data(selected_channel_ids={channel_id}, last_scope="folder_pick_one")

    # следующий этап
    await state.set_state(CreatePostStates.composing)
    await call.message.edit_text(
        "Ок. Отправьте текст и/или медиа для поста одним сообщением.\n\n"
        "После этого я предложу настройки и планирование.",
    )
    await call.answer()

@user_private_router.callback_query(CreatePostCD.filter(F.action == "pick_folder_all"))
async def cp_pick_folder_all(call: types.CallbackQuery, callback_data: CreatePostCD, state: FSMContext, session: AsyncSession):
    folder_id = int(callback_data.folder_id)
    channels = await orm_get_folder_channels(session, user_id=call.from_user.id, folder_id=folder_id)

    if not channels:
        await call.answer("В папке нет каналов", show_alert=True)
        return

    selected = {int(ch.id) for ch in channels}
    await state.update_data(selected_channel_ids=selected, last_scope="folder_pick_all")

    await state.set_state(CreatePostStates.composing)
    await call.message.edit_text(
        "Ок. Публикуем во всех каналах этой папки.\n\n"
        "Отправьте текст и/или медиа для поста одним сообщением.",
    )
    await call.answer()

@user_private_router.callback_query(CreatePostCD.filter(F.action == "folders_menu"))
async def cp_folders_menu(call: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    folders = await orm_get_user_folders(session, user_id=call.from_user.id)
    await state.update_data(last_scope="folders_menu")

    if not folders:
        await call.message.edit_text(
            "У вас пока нет папок.\n\nСоздавать и управлять папками можно в меню «Настройки».",
            reply_markup=ik_folders_empty(),
        )
        await call.answer()
        return

    await call.message.edit_text("Выберите папку:", reply_markup=ik_folders_list(folders))
    await call.answer()

# @user_private_router.callback_query(CreatePostCD.filter(F.action == "free"))
# async def cp_open_free_channels(call: types.CallbackQuery, state: FSMContext, session: AsyncSession):
#     channels = await orm_get_free_channels_for_user(session, user_id=call.from_user.id)
#
#     data = await state.get_data()
#     selected: set[int] = set(data.get("selected_channel_ids") or [])
#
#     await state.update_data(last_scope="free")
#
#     if not channels:
#         # ТЗ допускает отсутствие каналов. Дальше можно предложить “добавьте бота в канал”
#         await call.message.edit_text(
#             "Свободных каналов нет. Добавьте бота администратором в канал и вернитесь сюда.",
#             reply_markup=InlineKeyboardMarkup(
#                 inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data=CreatePostCD(action="back").pack())]]
#             ),
#         )
#         await call.answer()
#         return
#
#     await call.message.edit_text(
#         "Выберите каналы (можно несколько):",
#         reply_markup=ik_channels_picker(
#             channels=channels,
#             selected_channel_ids=selected,
#             title="Каналы",
#             folder_id=0,
#         ),
#     )
#     await call.answer()


# @user_private_router.callback_query(CreatePostCD.filter(F.action == "toggle"))
# async def cp_toggle_channel(call: types.CallbackQuery, callback_data: CreatePostCD, state: FSMContext, session: AsyncSession):
#     ch_id = int(callback_data.channel_id)
#     folder_id = int(callback_data.folder_id)
#
#     data = await state.get_data()
#     selected: set[int] = set(data.get("selected_channel_ids") or [])
#
#     if ch_id in selected:
#         selected.remove(ch_id)
#     else:
#         selected.add(ch_id)
#
#     await state.update_data(selected_channel_ids=selected)
#
#     # Перерисовываем текущий экран (папка или free)
#     last_scope = data.get("last_scope")
#     if last_scope == "folder":
#         last_folder_id = int(data.get("last_folder_id") or folder_id)
#         channels = await orm_get_folder_channels(session, user_id=call.from_user.id, folder_id=last_folder_id)
#         await call.message.edit_reply_markup(
#             reply_markup=ik_channels_picker(
#                 channels=channels,
#                 selected_channel_ids=selected,
#                 title="Папка",
#                 folder_id=last_folder_id,
#             )
#         )
#     else:
#         channels = await orm_get_free_channels_for_user(session, user_id=call.from_user.id)
#         await call.message.edit_reply_markup(
#             reply_markup=ik_channels_picker(
#                 channels=channels,
#                 selected_channel_ids=selected,
#                 title="Каналы",
#                 folder_id=0,
#             )
#         )
#
#     await call.answer()

@user_private_router.callback_query(CreatePostCD.filter(F.action == "all"))
async def cp_all_channels(call: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    channels = await orm_get_user_channels(session, user_id=call.from_user.id)
    if not channels:
        await call.answer("Нет доступных каналов", show_alert=True)
        return

    selected = {int(ch.id) for ch in channels}
    await state.update_data(selected_channel_ids=selected, last_scope="all")

    # следующий этап сделаем позже (приём контента)
    await state.set_state(CreatePostStates.composing)
    await call.message.edit_text(
        "Ок. Публикуем во всех каналах.\n\nОтправьте текст и/или медиа для поста одним сообщением.",
    )
    await call.answer()

@user_private_router.callback_query(CreatePostCD.filter(F.action == "add_channel"))
async def cp_add_channel(call: types.CallbackQuery, state:FSMContext):
    # заглушка под будущий этап (инструкция/мастер подключения)
    await state.set_state(ConnectChannelStates.waiting_channel)
    await call.message.edit_text(NO_CHANNELS_TEXT)
    await call.answer()

async def cp_add_folder(call: types.CallbackQuery):
    # заглушка под будущий этап (создание папки)
    await call.message.edit_text(
        "Введите название новой папки сообщением.\n"
        "В следующем шаге я создам папку и покажу список.",
    )
    await call.answer()

@user_private_router.callback_query(CreatePostCD.filter(F.action == "open_channel"))
async def cp_pick_free_channel(call: types.CallbackQuery, callback_data: CreatePostCD, state: FSMContext):
    channel_id = int(callback_data.channel_id)

    await state.update_data(selected_channel_ids={channel_id}, last_scope="pick_free_channel")
    await state.set_state(CreatePostStates.composing)

    await call.message.edit_text(
        "Ок. Отправьте текст и/или медиа для поста одним сообщением.\n\n"
        "После этого я предложу настройки и планирование.",
    )
    await call.answer()


# Хелперы: парсинг входа и проверка прав
def _extract_channel_id_from_message(message: Message) -> Optional[int]:
    """
    Пытаемся вытащить канал из:
    - пересланного сообщения (forward_from_chat)
    - sender_chat (если пользователь писал от имени канала, редко)
    """
    if message.forward_from_chat and message.forward_from_chat.type == "channel":
        return message.forward_from_chat.id

    # aiogram/telegram менялись, поэтому подстрахуемся:
    fo = getattr(message, "forward_origin", None)
    if fo and getattr(fo, "chat", None) and fo.chat.type == "channel":
        return fo.chat.id

    if message.sender_chat and message.sender_chat.type == "channel":
        return message.sender_chat.id

    return None


def _parse_channel_ref(text: str) -> Optional[str | int]:
    """
    Возвращает:
    - int если это похоже на channel_id
    - str если это username (без @)
    """
    t = (text or "").strip()
    if not t:
        return None

    # t.me link
    m = re.search(r"(?:https?://)?t\.me/([A-Za-z0-9_]{5,})", t)
    if m:
        return m.group(1)

    # @username
    if t.startswith("@") and len(t) > 1:
        return t[1:]

    # numeric id
    if re.fullmatch(r"-?\d{5,}", t):
        try:
            return int(t)
        except ValueError:
            return None

    return None


def _chat_url(chat) -> str:
    # публичный канал
    if getattr(chat, "username", None):
        return f"https://t.me/{chat.username}"
    # приватный — ссылки не будет, но по твоему тексту предполагается username.
    # оставим заглушку
    return "https://t.me/"


#Проверка прав бота и пользователя
async def _check_bot_rights(bot: Bot, channel_id: int) -> Tuple[bool, str]:
    """
    Проверяем, что бот админ и есть нужные права.
    """
    me = await bot.get_me()
    try:
        member = await bot.get_chat_member(channel_id, me.id)
    except TelegramBadRequest:
        return False, "Не удалось получить статус бота в канале. Проверьте, что бот добавлен в канал."

    status = getattr(member, "status", None)
    if status not in ("administrator", "creator"):
        return False, "Бот не является администратором канала."

    # У creator атрибутов прав может не быть — считаем ок
    if status == "creator":
        return True, ""

    # administrator: проверяем нужные флаги
    can_post = getattr(member, "can_post_messages", False)
    can_delete = getattr(member, "can_delete_messages", False)
    can_edit = getattr(member, "can_edit_messages", False)

    if not (can_post and can_delete and can_edit):
        return False, "Боту не выданы все права: отправка, удаление, редактирование."

    return True, ""


async def _check_user_is_admin(bot: Bot, channel_id: int, user_id: int) -> Tuple[bool, str]:
    try:
        member = await bot.get_chat_member(channel_id, user_id)
    except TelegramBadRequest:
        return False, "Не удалось проверить ваши права в канале."

    status = getattr(member, "status", None)
    if status not in ("administrator", "creator"):
        return False, "Вы не являетесь администратором этого канала."
    return True, ""

#Пользователь прислал пересланное/id/username -> подключаем
@user_private_router.message(ConnectChannelStates.waiting_channel)
async def connect_channel_message(message: types.Message, state: FSMContext, session: AsyncSession, bot):
    # 1) определить канал
    channel_id = _extract_channel_id_from_message(message)
    ref = None

    if channel_id is None:
        ref = _parse_channel_ref(message.text or "")
        if ref is None:
            await message.answer("Не понял. Пришлите пересланное сообщение из канала, юзернейм (@channel) или ID канала.")
            return

        # resolve username/id -> channel_id
        try:
            chat = await bot.get_chat(ref)
            channel_id = chat.id
        except Exception:
            await message.answer("Не удалось найти канал. Проверьте юзернейм или ID и попробуйте снова.")
            return
    else:
        try:
            chat = await bot.get_chat(channel_id)
        except Exception:
            await message.answer("Не удалось получить информацию о канале. Попробуйте ещё раз.")
            return

    # 2) проверки
    ok, err = await _check_bot_rights(bot, channel_id)
    if not ok:
        await message.answer(
            "Канал не подключен.\n\n"
            f"Причина: {err}\n\n"
            "Сделайте бота администратором и выдайте права:\n"
            "✅ Отправка сообщений\n✅ Удаление сообщений\n✅ Редактирование сообщений"
        )
        return

    ok, err = await _check_user_is_admin(bot, channel_id, message.from_user.id)
    if not ok:
        await message.answer(f"Канал не подключен.\n\nПричина: {err}")
        return

    # 3) сохранить в БД (канал + связка админ)
    ch_username = getattr(chat, "username", None)
    ch_title = getattr(chat, "title", "Канал")
    is_private = False if ch_username else True

    # upsert channel
    await orm_upsert_channel(
        session,
        channel_id=channel_id,
        title=ch_title,
        username=ch_username,
        is_private=is_private,
    )
    await orm_upsert_user(
        session,
        user_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
    )

    # add admin link
    await orm_add_channel_admin(
        session,
        channel_id=channel_id,
        user_id=message.from_user.id,
        tg_status=TgMemberStatus.administrator,
        verified_at=dt_utc.now().replace(tzinfo=None),
    )

    await session.commit()

    url = _chat_url(chat)
    await state.clear()

    await message.answer(
        connected_text(ch_title, url),
        reply_markup=ik_after_channel_connected(),
        disable_web_page_preview=True,
    )

ALBUM_WAIT_SECONDS = 1.0
#Кнопки редактирования. Пользователь присалал сообщение.
@user_private_router.message(StateFilter(CreatePostStates.composing))
async def on_compose_any_message(message: types.Message, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    selected_ids = set(data.get("selected_channel_ids") or [])
    if not selected_ids:
        await message.answer("Сначала выберите каналы для публикации.")
        return

    if message.media_group_id:
        key = (message.chat.id, message.from_user.id, str(message.media_group_id))
        bucket = MEDIA_GROUP_BUFFER.add(key, message)

        # если это первое сообщение альбома — планируем финализацию
        if bucket.task is None:
            bucket.task = asyncio.create_task(
                _finalize_album(key=key, state=state, session=session)
            )

        # Ничего не отвечаем на каждую часть альбома (иначе будет спам)
        return

    post_id = await orm_create_post_from_message(
        session=session,
        user_id=message.from_user.id,
        message=message,
        channel_ids=selected_ids,
    )
    await session.commit()

    # 2) отправить превью: копируем сообщение пользователя (что угодно)
    res = await message.bot.copy_message(
        chat_id=message.chat.id,
        from_chat_id=message.chat.id,
        message_id=message.message_id,
    )

    def _message_has_media(msg: types.Message) -> bool:
        return any([
            bool(msg.photo),
            bool(msg.video),
            bool(msg.document),
            bool(msg.audio),
            bool(msg.voice),
            bool(msg.animation),
            bool(msg.video_note),
            bool(msg.sticker),
        ])

    def _detect_editor_mode(msg: types.Message) -> str:
        # 4) голосовое
        if msg.voice:
            return "voice_with_desc" if (msg.caption or msg.text) else "voice_no_desc"

        # фото
        if msg.photo:
            # 3) изначально текст+фото
            if msg.caption:
                return "photo_with_initial_text"
            # 1) только фото
            return "photo_only"

        # можно расширять под видео/документы и т.д.
        if _message_has_media(msg):
            return "media_with_text" if msg.caption else "media_only"

        # текст без медиа
        return "text_only"

    # Определяем режим редактора
    mode = _detect_editor_mode(message)

    # 3) привязать editor state к этому превью
    st = EditorState(
        post_id=post_id,
        preview_chat_id=message.chat.id,
        preview_message_id=res.message_id,
    )
    # Создаем контекст для редактора
    ctx = make_ctx_from_message(message)
    await state.update_data(
        editor=editor_state_to_dict(st),
        editor_has_media=_message_has_media(message),
        editor_mode=_detect_editor_mode(message),
        editor_context=ctx,
    )
    existing_buttons = await orm_get_post_buttons(session, post_id=post_id)
    if existing_buttons:
        st.has_url_buttons = True
        editor_kb = build_editor_kb(post_id, st, ctx=ctx)
        combined_kb = merge_url_and_editor_kb(existing_buttons, editor_kb)
    else:
        combined_kb = build_editor_kb(post_id, st, ctx=ctx)
    # 4) повесить inline редактор под превью
    await message.bot.edit_message_reply_markup(
        chat_id=st.preview_chat_id,
        message_id=st.preview_message_id,
        reply_markup=combined_kb
    )
@user_private_router.callback_query(EditorCD.filter(F.action == "toggle"))
async def editor_toggle(call: types.CallbackQuery, callback_data: EditorCD, state: FSMContext, session: AsyncSession):
    data = await state.get_data()
    if "editor" not in data:
        await call.answer("Редактор не активен", show_alert=True)
        return

    st = editor_state_from_dict(data["editor"])
    editor_ctx = data.get("editor_context")
    if not editor_ctx:
        # Если контекст не найден, создаем его из текущего сообщения
        editor_ctx = make_ctx_from_message(call.message)

    # защита: это должен быть тот же post_id
    if int(callback_data.post_id) != st.post_id:
        await call.answer("Устаревшая кнопка", show_alert=True)
        return

    key = callback_data.key
    if key not in TOGGLE_KEYS:
        await call.answer("Неизвестная настройка", show_alert=True)
        return

    # переключаем флаг
    current = getattr(st, key)
    setattr(st, key, not bool(current))

    # сохраним в FSM
    await state.update_data(editor=editor_state_to_dict(st))

    # (опционально) сразу пишем в БД настройки, чтобы не потерять при рестарте
    # await orm_update_post_settings(session, post_id=st.post_id, **{key: getattr(st, key)})
    # await session.commit()

    # перерисуем клавиатуру на том же сообщении (на превью)
    await call.message.edit_reply_markup(reply_markup=build_editor_kb(st.post_id, st, ctx=editor_ctx))
    await call.answer()


def _has_media_in_preview(msg: types.Message) -> bool:
    return any([
        bool(msg.photo),
        bool(msg.video),
        bool(msg.document),
        bool(msg.audio),
        bool(msg.voice),
        bool(msg.animation),
        bool(msg.video_note),
        bool(msg.sticker),
    ])

@user_private_router.callback_query(EditorCD.filter(F.action == "edit_text"))
async def editor_edit_text(call: types.CallbackQuery, callback_data: EditorCD, state: FSMContext):
    data = await state.get_data()
    if "editor" not in data:
        await call.answer("Редактор не активен", show_alert=True)
        return

    st = editor_state_from_dict(data["editor"])
    if int(callback_data.post_id) != st.post_id:
        await call.answer("Устаревшая кнопка", show_alert=True)
        return

    # Определяем, есть ли медиа в превью (чтобы показать "Удалить текст")
    # ВАЖНО: для одиночного превью call.message == превью.
    # Для альбома кнопки висят на сервисном сообщении, там медиа нет — поэтому can_delete_text=False.
    is_album = data.get("is_album", False)
    can_delete_text = True if is_album else _has_media_in_preview(call.message)

    # сохраняем, что именно редактируем
    await state.update_data(
        edit_text_post_id=st.post_id,
        edit_text_preview_chat_id=st.preview_chat_id,
        edit_text_preview_message_id=st.preview_message_id,
    )
    await state.set_state(EditTextStates.waiting_new_text)

    # Всегда отправляем отдельное сообщение-подсказку, а не пытаемся редактировать медиа-сообщение
    prompt = await call.message.answer(
        "Отправьте новый текст для поста",
        reply_markup=ik_edit_text_controls(st.post_id, can_delete_text=can_delete_text),
    )
    await state.update_data(edit_text_prompt_message_id=prompt.message_id)

    await call.answer()


@user_private_router.message(StateFilter(EditTextStates.waiting_new_text), F.text)
async def edit_text_receive_new_text(message: types.Message, state: FSMContext, session: AsyncSession):
    data = await state.get_data()

    post_id = int(data["edit_text_post_id"])
    preview_chat_id = int(data["edit_text_preview_chat_id"])
    preview_message_id = int(data["edit_text_preview_message_id"])
    prompt_id = data.get("edit_text_prompt_message_id")

    # Проверяем, является ли это альбомом
    is_album = data.get("is_album", False)
    album_caption_message_id = data.get("album_caption_message_id")

    new_text = (message.text or "").strip()

    await orm_edit_post_text(session, post_id=post_id, text=new_text)
    await session.commit()

    editor = editor_state_from_dict(data["editor"])

    # Получаем или создаем контекст
    editor_ctx = data.get("editor_context")
    if not editor_ctx:
        # Пытаемся получить сообщение превью
        try:
            preview_msg = await message.bot.get_message(
                chat_id=preview_chat_id,
                message_id=preview_message_id
            )
            editor_ctx = make_ctx_from_message(preview_msg)
        except Exception:
            # Если не удалось, создаем простой контекст
            editor_ctx = make_ctx_from_message(message)

    kb = build_editor_kb(editor.post_id, editor, ctx=editor_ctx)

    # Если это альбом и есть ID сообщения с подписью, меняем подпись альбома
    if is_album and album_caption_message_id:
        # Меняем подпись у сообщения альбома
        try:
            await message.bot.edit_message_caption(
                chat_id=preview_chat_id,
                message_id=album_caption_message_id,
                caption=new_text,
                reply_markup=None,
            )
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e):
                raise  # Пробрасываем другие ошибки
            # Игнорируем "not modified" — текст уже такой же

        # Обновляем кнопки на служебном сообщении (может не измениться)
        try:
            await message.bot.edit_message_reply_markup(
                chat_id=preview_chat_id,
                message_id=preview_message_id,
                reply_markup=kb,
            )
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e):
                raise
    else:
        # Стандартная логика для одиночных сообщений
        try:
            await message.bot.edit_message_caption(
                chat_id=preview_chat_id,
                message_id=preview_message_id,
                caption=new_text,
                reply_markup=kb,
            )
            mode = data.get("editor_mode")
            if mode == "photo_only":
                await state.update_data(editor_mode="photo_with_added_desc")
            if mode == "voice_no_desc":
                await state.update_data(editor_mode="voice_with_desc")
        except Exception:
            # значит это не медиа-сообщение
            await message.bot.edit_message_text(
                chat_id=preview_chat_id,
                message_id=preview_message_id,
                text=new_text if new_text else " ",
                reply_markup=kb,
            )

    # прибираем сообщения "ввода"
    try:
        await message.delete()
    except Exception:
        pass
    if prompt_id:
        try:
            await message.bot.delete_message(chat_id=message.chat.id, message_id=int(prompt_id))
        except Exception:
            pass

    await state.set_state(CreatePostStates.composing)

@user_private_router.callback_query(EditTextCD.filter(F.action == "delete"))
async def edit_text_delete(call: types.CallbackQuery, callback_data: EditTextCD, state: FSMContext, session: AsyncSession):
    data = await state.get_data()

    post_id = int(callback_data.post_id)
    preview_chat_id = int(data["edit_text_preview_chat_id"])
    preview_message_id = int(data["edit_text_preview_message_id"])
    editor_ctx = data.get("editor_context")
    is_album = data.get("is_album", False)
    album_caption_message_id = data.get("album_caption_message_id")
    if not editor_ctx:
        # Если контекст не найден, создаем его из текущего сообщения
        editor_ctx = make_ctx_from_message(call.message)
    await orm_edit_post_text(session, post_id=post_id, text=None)
    await session.commit()

    editor = editor_state_from_dict(data["editor"])
    kb = build_editor_kb(editor.post_id, editor, ctx=editor_ctx)

    if is_album and album_caption_message_id:
        try:
            await call.bot.edit_message_caption(
                chat_id=preview_chat_id,
                message_id=int(album_caption_message_id),
                caption="",  # безопаснее, чем None (не упрёмся в "no caption")
                reply_markup=None,  # у альбома клавиатуры нет
            )
        except TelegramBadRequest as e:
            s = str(e)
            if ("message is not modified" not in s) and ("there is no caption" not in s):
                raise

        # обновляем только клавиатуру у служебного сообщения
        try:
            await call.bot.edit_message_reply_markup(
                chat_id=preview_chat_id,
                message_id=preview_message_id,
                reply_markup=kb,
            )
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e):
                raise

        # 2) Одиночное медиа-сообщение: удаляем caption у превью
    else:
        try:
            await call.bot.edit_message_caption(
                chat_id=preview_chat_id,
                message_id=preview_message_id,
                caption="",
                reply_markup=kb,
            )
        except TelegramBadRequest as e:
            s = str(e)
            # если у сообщения уже нет caption — это не ошибка для UX
            if ("message is not modified" not in s) and ("there is no caption" not in s):
                raise

        # режимы (как у тебя было)
    mode = data.get("editor_mode")
    if mode in ("photo_with_added_desc", "photo_with_initial_text"):
        await state.update_data(editor_mode="photo_only")
    if mode == "voice_with_desc":
        await state.update_data(editor_mode="voice_no_desc")

    await state.set_state(CreatePostStates.composing)
    await call.answer("Текст удалён")

@user_private_router.callback_query(EditTextCD.filter(F.action == "back"))
async def edit_text_back(call: types.CallbackQuery, callback_data: EditTextCD, state: FSMContext):
    # просто выходим из режима ожидания текста
    await state.set_state(CreatePostStates.composing)
    await call.message.delete()  # удаляем сообщение “Отправьте новый текст…”
    await call.answer()

@user_private_router.callback_query(EditorCD.filter(F.action == "attach_media"))
async def editor_attach_media(call: types.CallbackQuery, callback_data: EditorCD, state: FSMContext):
    '''Кнопка "Прикрепить медиа" - переводим в режим ожидания медиа.'''
    data = await state.get_data()
    if "editor" not in data:
        await call.answer("Редактор не активен", show_alert=True)
        return

    st = editor_state_from_dict(data["editor"])
    if int(callback_data.post_id) != st.post_id:
        await call.answer("Устаревшая кнопка", show_alert=True)
        return

    # Сохраняем инфу для прикрепления
    await state.update_data(
        attach_media_post_id=st.post_id,
        attach_media_preview_chat_id=st.preview_chat_id,
        attach_media_preview_message_id=st.preview_message_id,
    )
    await state.set_state(AttachMediaStates.waiting_media)

    # Отправляем подсказку
    prompt = await call.message.answer(
        "Отправьте фото, видео, документ или голосовое сообщение для прикрепления к посту.",
        reply_markup=ik_attach_media_controls(st.post_id),
    )
    await state.update_data(attach_media_prompt_id=prompt.message_id)

    await call.answer()


@user_private_router.message(
    StateFilter(AttachMediaStates.waiting_media),
    F.content_type.in_([
        ContentType.PHOTO,
        ContentType.VIDEO,
        ContentType.DOCUMENT,
        ContentType.AUDIO,
        ContentType.VOICE,
        ContentType.ANIMATION,
        ContentType.VIDEO_NOTE,
    ])
)
async def attach_media_receive(message: types.Message, state: FSMContext, session: AsyncSession):
    '''Пользователь прислал медиа - прикрепляем к посту.'''
    data = await state.get_data()

    post_id = int(data["attach_media_post_id"])
    preview_chat_id = int(data["attach_media_preview_chat_id"])
    old_preview_message_id = int(data["attach_media_preview_message_id"])
    prompt_id = data.get("attach_media_prompt_id")

    # Определяем тип и file_id медиа
    media_type, file_id, file_unique_id = _extract_media_info(message)
    if not media_type or not file_id:
        await message.answer("Не удалось определить тип медиа. Попробуйте ещё раз.")
        return

    # Сохраняем медиа в БД
    await orm_add_media_to_post(
        session=session,
        post_id=post_id,
        media_type=media_type,
        file_id=file_id,
        file_unique_id=file_unique_id,
    )
    await session.commit()

    # Получаем текст из старого превью (текстового сообщения)
    original_text = ""
    try:
        # Пересылаем чтобы получить текст
        old_preview = await message.bot.forward_message(
            chat_id=message.chat.id,
            from_chat_id=preview_chat_id,
            message_id=old_preview_message_id,
        )
        original_text = old_preview.text or ""
        # Удаляем пересланное сообщение
        await old_preview.delete()
    except Exception:
        pass

    # Удаляем старое текстовое превью
    try:
        await message.bot.delete_message(
            chat_id=preview_chat_id,
            message_id=old_preview_message_id,
        )
    except Exception:
        pass

    # Создаём новое превью с медиа + текстом (caption)
    new_preview_msg = await _send_media_preview(
        bot=message.bot,
        chat_id=message.chat.id,
        media_type=media_type,
        file_id=file_id,
        caption=original_text if original_text else None,
    )

    # Обновляем EditorState
    st = EditorState(
        post_id=post_id,
        preview_chat_id=message.chat.id,
        preview_message_id=new_preview_msg.message_id,
    )
    # Восстанавливаем toggle-флаги из старого состояния
    old_editor = data.get("editor", {})
    for key in TOGGLE_KEYS:
        setattr(st, key, old_editor.get(key, False))

    # Обновляем контекст
    ctx = make_ctx_from_message(new_preview_msg)
    # Корректируем: текст был изначально, медиа прикреплено позже
    ctx.text_was_initial = True
    ctx.text_added_later = False

    await state.update_data(
        editor=editor_state_to_dict(st),
        editor_has_media=True,
        editor_mode="photo_with_initial_text" if media_type == "photo" else "media_with_text",
        editor_context=ctx,
    )

    # Вешаем клавиатуру на новое превью
    await message.bot.edit_message_reply_markup(
        chat_id=st.preview_chat_id,
        message_id=st.preview_message_id,
        reply_markup=build_editor_kb(post_id, st, ctx=ctx),
    )

    # Убираем сообщения ввода
    try:
        await message.delete()
    except Exception:
        pass
    if prompt_id:
        try:
            await message.bot.delete_message(chat_id=message.chat.id, message_id=int(prompt_id))
        except Exception:
            pass

    await state.set_state(CreatePostStates.composing)


@user_private_router.callback_query(EditTextCD.filter(F.action == "cancel_attach"))
async def attach_media_cancel(call: types.CallbackQuery, callback_data: EditTextCD, state: FSMContext):
    '''Отмена прикрепления медиа.'''
    await state.set_state(CreatePostStates.composing)
    await call.message.delete()
    await call.answer()


def _extract_media_info(message: types.Message) -> tuple[str | None, str | None, str | None]:
    '''Извлекает тип медиа, file_id и file_unique_id из сообщения.'''
    if message.photo:
        photo = message.photo[-1]  # Берём максимальное разрешение
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


async def _send_media_preview(
    bot,
    chat_id: int,
    media_type: str,
    file_id: str,
    caption: str | None = None,
) -> types.Message:
    '''Отправляет медиа как превью поста.'''
    if media_type == "photo":
        return await bot.send_photo(chat_id=chat_id, photo=file_id, caption=caption)
    if media_type == "video":
        return await bot.send_video(chat_id=chat_id, video=file_id, caption=caption)
    if media_type == "gif":
        return await bot.send_animation(chat_id=chat_id, animation=file_id, caption=caption)
    if media_type == "voice":
        return await bot.send_voice(chat_id=chat_id, voice=file_id, caption=caption)
    if media_type == "document":
        return await bot.send_document(chat_id=chat_id, document=file_id, caption=caption)
    # fallback
    return await bot.send_document(chat_id=chat_id, document=file_id, caption=caption)


COPY_POST_TEXT = (
    "С помощью функции «Копировать» вы можете отправить эту же публикацию "
    "в другие свои каналы, которые подключены к @IPostedBot.\\n\\n"
    "Выберите каналы, в которые нужно копировать пост."
)


@user_private_router.callback_query(EditorCD.filter(F.action == "copy_to_channels"))
async def editor_copy_to_channels(call: types.CallbackQuery, callback_data: EditorCD, state: FSMContext,
                                  session: AsyncSession):
    '''Кнопка "Копировать" - показываем список каналов для выбора.'''
    data = await state.get_data()
    if "editor" not in data:
        await call.answer("Редактор не активен", show_alert=True)
        return

    st = editor_state_from_dict(data["editor"])
    if int(callback_data.post_id) != st.post_id:
        await call.answer("Устаревшая кнопка", show_alert=True)
        return

    # Получаем ВСЕ каналы пользователя (включая те, что в папках)
    all_channels = await orm_get_all_user_channels(session, user_id=call.from_user.id)

    if not all_channels:
        await call.answer("У вас нет подключённых каналов", show_alert=True)
        return

    # Исключаем каналы, в которые пост уже будет отправлен
    current_channel_ids = set(data.get("selected_channel_ids") or [])
    available_channels = [ch for ch in all_channels if ch.id not in current_channel_ids]

    if not available_channels:
        await call.answer("Нет других каналов для копирования", show_alert=True)
        return

    # Сохраняем состояние для копирования
    await state.update_data(
        copy_post_id=st.post_id,
        copy_available_channels=[ch.id for ch in available_channels],
        copy_selected_ids=set(),
    )

    # Отправляем сообщение с выбором каналов
    await call.message.edit_text(
        COPY_POST_TEXT,
        reply_markup=build_copy_channels_kb(
            post_id=st.post_id,
            channels=available_channels,
            selected_ids=set(),
        ),
    )
    await call.answer()


@user_private_router.callback_query(CopyPostCD.filter(F.action == "select_channel"))
async def copy_select_channel(call: types.CallbackQuery, callback_data: CopyPostCD, state: FSMContext,
                              session: AsyncSession):
    '''Выбор/снятие выбора канала для копирования.'''
    data = await state.get_data()

    post_id = callback_data.post_id
    channel_id = callback_data.channel_id

    # Получаем текущий выбор (преобразуем в set если это list)
    raw_selected = data.get("copy_selected_ids") or []
    if isinstance(raw_selected, list):
        selected_ids = set(raw_selected)
    else:
        selected_ids = set(raw_selected)

    # Toggle выбора
    if channel_id in selected_ids:
        selected_ids.discard(channel_id)
    else:
        selected_ids.add(channel_id)

    # Сохраняем как list (FSM лучше работает с list)
    await state.update_data(copy_selected_ids=list(selected_ids))

    # Получаем список доступных каналов
    available_channel_ids = data.get("copy_available_channels", [])
    all_channels = await orm_get_all_user_channels(session, user_id=call.from_user.id)
    available_channels = [ch for ch in all_channels if ch.id in available_channel_ids]

    # Обновляем клавиатуру
    try:
        await call.message.edit_reply_markup(
            reply_markup=build_copy_channels_kb(
                post_id=post_id,
                channels=available_channels,
                selected_ids=selected_ids,
            )
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise

    await call.answer()


@user_private_router.callback_query(CopyPostCD.filter(F.action == "toggle_all"))
async def copy_toggle_all(call: types.CallbackQuery, callback_data: CopyPostCD, state: FSMContext,
                          session: AsyncSession):
    '''Toggle: выбрать все / убрать все.'''
    data = await state.get_data()

    available_channel_ids = set(data.get("copy_available_channels") or [])

    # Получаем текущий выбор
    raw_selected = data.get("copy_selected_ids") or []
    if isinstance(raw_selected, list):
        selected_ids = set(raw_selected)
    else:
        selected_ids = set(raw_selected)

    # Если все выбраны - убираем все, иначе - выбираем все
    if selected_ids == available_channel_ids and len(available_channel_ids) > 0:
        # Все выбраны -> убираем все
        new_selected = set()
    else:
        # Не все выбраны -> выбираем все
        new_selected = available_channel_ids.copy()

    # Сохраняем как list
    await state.update_data(copy_selected_ids=list(new_selected))

    # Получаем список каналов для отображения
    all_channels = await orm_get_all_user_channels(session, user_id=call.from_user.id)
    available_channels = [ch for ch in all_channels if ch.id in available_channel_ids]

    try:
        await call.message.edit_reply_markup(
            reply_markup=build_copy_channels_kb(
                post_id=callback_data.post_id,
                channels=available_channels,
                selected_ids=new_selected,
            )
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise

    await call.answer()


@user_private_router.callback_query(CopyPostCD.filter(F.action == "apply"))
async def copy_apply(call: types.CallbackQuery, callback_data: CopyPostCD, state: FSMContext, session: AsyncSession):
    '''Применить копирование - создаём PostTarget для выбранных каналов.'''
    data = await state.get_data()

    raw_selected = data.get("copy_selected_ids") or []
    if isinstance(raw_selected, list):
        selected_ids = set(raw_selected)
    else:
        selected_ids = set(raw_selected)

    if not selected_ids:
        await call.answer("Выберите хотя бы один канал", show_alert=True)
        return

    post_id = callback_data.post_id

    # Создаём копии поста для выбранных каналов
    await orm_copy_post_to_channels(
        session=session,
        post_id=post_id,
        channel_ids=selected_ids,
    )
    await session.commit()

    # Возвращаемся к редактору
    st = editor_state_from_dict(data["editor"])
    editor_ctx = data.get("editor_context")
    if not editor_ctx:
        editor_ctx = make_ctx_from_message(call.message)

    # Очищаем данные копирования
    await state.update_data(
        copy_post_id=None,
        copy_available_channels=None,
        copy_selected_ids=None,
    )

    await call.message.edit_text(
        f"✅ Пост будет скопирован в {len(selected_ids)} канал(ов).\\n\\nНастройте пост перед публикацией.",
        reply_markup=build_editor_kb(post_id, st, ctx=editor_ctx),
    )
    await call.answer(f"Добавлено {len(selected_ids)} канал(ов)")


@user_private_router.callback_query(CopyPostCD.filter(F.action == "back"))
async def copy_back(call: types.CallbackQuery, callback_data: CopyPostCD, state: FSMContext):
    '''Вернуться из меню копирования к редактору.'''
    data = await state.get_data()

    st = editor_state_from_dict(data["editor"])
    editor_ctx = data.get("editor_context")
    if not editor_ctx:
        editor_ctx = make_ctx_from_message(call.message)

    # Очищаем данные копирования
    await state.update_data(
        copy_post_id=None,
        copy_available_channels=None,
        copy_selected_ids=None,
    )

    await call.message.edit_text(
        "Настройте пост перед публикацией.",
        reply_markup=build_editor_kb(callback_data.post_id, st, ctx=editor_ctx),
    )
    await call.answer()


URL_BUTTONS_PROMPT = """<b>🔘 URL-кнопки</b>

Отправьте боту список URL-кнопок в следующем формате:

<code>Кнопка 1 - http://link.com</code>
<code>Кнопка 2 - http://link.com</code>

Используйте разделитель « | », чтобы добавить до 8 кнопок в один ряд (допустимо 15 рядов):

<code>Кнопка 1 - http://link.com | Кнопка 2 - http://link.com</code>"""


def parse_url_buttons(text: str) -> tuple[list[dict], str | None]:
    """
    Парсит текст с URL-кнопками.

    Формат:
    - Каждая строка = ряд кнопок
    - Кнопки в ряду разделены « | »
    - Формат кнопки: «Текст - URL»

    Returns:
        (список кнопок, ошибка или None)
        Кнопка = {'text': str, 'url': str, 'row': int, 'position': int}
    """
    import re

    lines = text.strip().split("\\n")
    buttons = []

    if len(lines) > 15:
        return [], "Максимум 15 рядов кнопок"

    for row_idx, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue

        # Разделяем кнопки в ряду по |
        btn_parts = [p.strip() for p in line.split("|")]

        if len(btn_parts) > 8:
            return [], f"Максимум 8 кнопок в ряду (ряд {row_idx + 1})"

        for pos_idx, btn_str in enumerate(btn_parts):
            btn_str = btn_str.strip()
            if not btn_str:
                continue

            # Ищем формат "Текст - URL"
            # Используем последнее вхождение " - " чтобы текст мог содержать дефисы
            match = re.match(r'^(.+)\s+-\s+(https?://\S+)$', btn_str)

            if not match:
                return [], f"Неверный формат кнопки: «{btn_str}»\\n\\nИспользуйте формат: Текст - http://link.com"

            btn_text = match.group(1).strip()
            btn_url = match.group(2).strip()

            if len(btn_text) > 64:
                return [], f"Текст кнопки слишком длинный (макс 64 символа): «{btn_text[:20]}...»"

            if not btn_text:
                return [], "Текст кнопки не может быть пустым"

            buttons.append({
                'text': btn_text,
                'url': btn_url,
                'row': row_idx,
                'position': pos_idx,
            })

    if not buttons:
        return [], "Не найдено ни одной кнопки"

    return buttons, None


@user_private_router.callback_query(EditorCD.filter(F.action == "url_buttons"))
async def editor_url_buttons(call: types.CallbackQuery, callback_data: EditorCD, state: FSMContext,
                             session: AsyncSession):
    """Кнопка 'URL-Кнопки' - показываем инструкцию и ждём ввода."""
    data = await state.get_data()
    if "editor" not in data:
        await call.answer("Редактор не активен", show_alert=True)
        return

    st = editor_state_from_dict(data["editor"])
    if int(callback_data.post_id) != st.post_id:
        await call.answer("Устаревшая кнопка", show_alert=True)
        return

    # Проверяем, есть ли уже кнопки у поста
    existing_buttons = await orm_get_post_buttons(session, post_id=st.post_id)
    has_buttons = len(existing_buttons) > 0

    # Сохраняем состояние
    await state.update_data(
        url_buttons_post_id=st.post_id,
        url_buttons_preview_chat_id=st.preview_chat_id,
        url_buttons_preview_message_id=st.preview_message_id,
    )
    await state.set_state(UrlButtonsStates.waiting_buttons)

    # Отправляем инструкцию
    prompt = await call.message.answer(
        URL_BUTTONS_PROMPT,
        parse_mode="HTML",
        reply_markup=build_url_buttons_prompt_kb(st.post_id, has_buttons=has_buttons),
    )
    await state.update_data(url_buttons_prompt_id=prompt.message_id)

    await call.answer()


@user_private_router.message(StateFilter(UrlButtonsStates.waiting_buttons), F.text)
async def url_buttons_receive(message: types.Message, state: FSMContext, session: AsyncSession):
    """Пользователь отправил текст с кнопками."""
    data = await state.get_data()

    post_id = int(data["url_buttons_post_id"])
    preview_chat_id = int(data["url_buttons_preview_chat_id"])
    preview_message_id = int(data["url_buttons_preview_message_id"])
    prompt_id = data.get("url_buttons_prompt_id")

    # Парсим кнопки
    buttons, error = parse_url_buttons(message.text)

    if error:
        await message.answer(f"❌ Ошибка: {error}")
        return

    # Удаляем старые кнопки и сохраняем новые
    await orm_delete_post_buttons(session, post_id=post_id)
    await orm_save_post_buttons(session, post_id=post_id, buttons=buttons)
    await session.commit()

    # Обновляем EditorState
    st = editor_state_from_dict(data["editor"])
    st.has_url_buttons = True
    await state.update_data(editor=editor_state_to_dict(st))

    # Получаем editor context
    editor_ctx = data.get("editor_context")
    if not editor_ctx:
        editor_ctx = make_ctx_from_message(message)

    # Строим клавиатуру: URL-кнопки + кнопки редактора
    editor_kb = build_editor_kb(post_id, st, ctx=editor_ctx)
    combined_kb = merge_url_and_editor_kb(buttons, editor_kb)

    # Обновляем превью с новой клавиатурой
    try:
        await message.bot.edit_message_reply_markup(
            chat_id=preview_chat_id,
            message_id=preview_message_id,
            reply_markup=combined_kb,
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise

    # Удаляем сообщения
    try:
        await message.delete()
    except Exception:
        pass
    if prompt_id:
        try:
            await message.bot.delete_message(chat_id=message.chat.id, message_id=int(prompt_id))
        except Exception:
            pass

    await state.set_state(CreatePostStates.composing)


@user_private_router.callback_query(UrlButtonsCD.filter(F.action == "delete"))
async def url_buttons_delete(call: types.CallbackQuery, callback_data: UrlButtonsCD, state: FSMContext,
                             session: AsyncSession):
    """Удалить все URL-кнопки."""
    data = await state.get_data()

    post_id = callback_data.post_id
    preview_chat_id = int(data["url_buttons_preview_chat_id"])
    preview_message_id = int(data["url_buttons_preview_message_id"])
    prompt_id = data.get("url_buttons_prompt_id")

    # Удаляем кнопки из БД
    await orm_delete_post_buttons(session, post_id=post_id)
    await session.commit()

    # Обновляем EditorState
    st = editor_state_from_dict(data["editor"])
    st.has_url_buttons = False
    await state.update_data(editor=editor_state_to_dict(st))

    # Получаем editor context
    editor_ctx = data.get("editor_context")
    if not editor_ctx:
        editor_ctx = make_ctx_from_message(call.message)

    # Строим клавиатуру только с кнопками редактора (без URL-кнопок)
    editor_kb = build_editor_kb(post_id, st, ctx=editor_ctx)

    # Обновляем превью
    try:
        await call.bot.edit_message_reply_markup(
            chat_id=preview_chat_id,
            message_id=preview_message_id,
            reply_markup=editor_kb,
        )
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise

    # Удаляем prompt сообщение
    if prompt_id:
        try:
            await call.bot.delete_message(chat_id=call.message.chat.id, message_id=int(prompt_id))
        except Exception:
            pass

    await state.set_state(CreatePostStates.composing)
    await call.answer("Кнопки удалены")


@user_private_router.callback_query(UrlButtonsCD.filter(F.action == "back"))
async def url_buttons_back(call: types.CallbackQuery, callback_data: UrlButtonsCD, state: FSMContext):
    """Вернуться из режима URL-кнопок в редактор."""
    await state.set_state(CreatePostStates.composing)
    await call.message.delete()
    await call.answer()