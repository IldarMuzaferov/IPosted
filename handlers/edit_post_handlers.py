from datetime import datetime, timedelta
import re

from aiogram import Router, F, types
from aiogram.filters import StateFilter
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from sqlalchemy.orm import joinedload

from filters.chat_types import ChatTypeFilter
from handlers.user_private import PREMIUM_EMOJI
from kbds.post_editor import (
    EditorState, EditorContext,
    editor_state_to_dict, editor_state_from_dict,
    editor_ctx_to_dict, editor_ctx_from_dict,
    _with_check,
)
from database.orm_query import orm_get_user
from database.models import PostTarget, Post, TargetState, PostHiddenPart

edit_post_router = Router()
edit_post_router.message.filter(ChatTypeFilter(["private"]))


# =============================================================================
# СОБСТВЕННЫЕ CallbackData
# =============================================================================

class EditPostCD(CallbackData, prefix="editpost"):
    action: str
    target_id: int = 0


class EditEditorCD(CallbackData, prefix="editeditor"):
    action: str
    post_id: int = 0
    key: str = ""


class EditTimerCD(CallbackData, prefix="edittimer"):
    action: str
    minutes: int = 0


class EditPublishCD(CallbackData, prefix="editpub"):
    action: str


# =============================================================================
# FSM СОСТОЯНИЯ
# =============================================================================

class EditPostStates(StatesGroup):
    waiting_forwarded_post = State()
    editing = State()
    selecting_timer = State()
    entering_publish_time = State()
    editing_text = State()


# =============================================================================
# ТЕКСТЫ И КОНСТАНТЫ
# =============================================================================

EDIT_POST_START_TEXT = (
    f"<tg-emoji emoji-id=\"{PREMIUM_EMOJI['edit_post']}\">✍️</tg-emoji> <b>ИЗМЕНЕНИЕ ПОСТА</b>\n\n"
    "Перешлите пост из вашего канала, который хотите изменить."
)

TIMER_SELECT_TEXT = (
    f"<tg-emoji emoji-id=\"{PREMIUM_EMOJI['planing']}\">✍️</tg-emoji> <b>ТАЙМЕР УДАЛЕНИЯ</b>\n\n"
    "Выберите через какое время пост будет автоматически удалён."
)

CONFIRM_TEXT = "❓ <b>Сохранить изменения?</b>"

EDIT_TEXT_PROMPT = "✏️ Отправьте новый текст для поста:"

TIMER_OPTIONS = [
    (0, "Не нужно"),
    (5, "5 минут"),
    (10, "10 минут"),
    (30, "30 минут"),
    (60, "1 час"),
    (180, "3 часа"),
    (360, "6 часов"),
    (720, "12 часов"),
    (1440, "24 часа"),
    (2880, "2 дня"),
    (4320, "3 дня"),
    (10080, "7 дней"),
]


def format_timer(minutes: int) -> str:
    if minutes == 0:
        return "Не нужно"
    if minutes < 60:
        return f"{minutes} мин"
    if minutes < 1440:
        return f"{minutes // 60} ч"
    return f"{minutes // 1440} дн"


def get_publish_time_text(user_tz: str = "Europe/Moscow") -> str:
    tz_names = {
        "Europe/Moscow": "Москва GMT+3",
        "Europe/London": "Лондон GMT+0",
        "Europe/Kiev": "Киев GMT+2",
        "Asia/Almaty": "Алматы GMT+6",
    }
    return (
        f"<tg-emoji emoji-id=\"{PREMIUM_EMOJI['cont_plan']}\">🗓</tg-emoji> <b>ВРЕМЯ ПУБЛИКАЦИИ</b>\n\n"
        f"Введите время в вашем часовом поясе ({tz_names.get(user_tz, user_tz)}).\n\n"
        f"Например: <code>18:01 16.8.2025</code>"
    )


# =============================================================================
# КЛАВИАТУРЫ
# =============================================================================

def build_edit_post_cancel_kb() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="❌ Отменить", callback_data=EditPostCD(action="cancel").pack())],
    ])


def build_timer_select_kb(current_minutes: int = 0) -> types.InlineKeyboardMarkup:
    kb = []
    for minutes, label in TIMER_OPTIONS:
        text = f"✅ {label}" if minutes == current_minutes else label
        kb.append(
            [types.InlineKeyboardButton(text=text, callback_data=EditTimerCD(action="select", minutes=minutes).pack())])
    kb.append([types.InlineKeyboardButton(text="⬅️ Назад", callback_data=EditTimerCD(action="back").pack())])
    return types.InlineKeyboardMarkup(inline_keyboard=kb)


def build_publish_time_kb() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="🚀 Применить сразу", callback_data=EditPublishCD(action="now").pack())],
        [types.InlineKeyboardButton(text="📅 Запланировать", callback_data=EditPublishCD(action="schedule").pack())],
        [types.InlineKeyboardButton(text="⬅️ Назад", callback_data=EditPublishCD(action="back").pack())],
    ])


def build_confirm_kb(target_id: int) -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(text="✅ Да",
                                       callback_data=EditPostCD(action="confirm_yes", target_id=target_id).pack()),
            types.InlineKeyboardButton(text="❌ Нет",
                                       callback_data=EditPostCD(action="confirm_no", target_id=target_id).pack()),
        ],
    ])


def build_back_to_edit_kb() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="⬅️ Назад", callback_data=EditPublishCD(action="back").pack())],
    ])


def build_edit_post_kb(
        post_id: int,
        st: EditorState,
        ctx: EditorContext,
        timer_minutes: int = 0,
        publish_now: bool = True,
        publish_time: datetime | None = None,
) -> types.InlineKeyboardMarkup:
    """Клавиатура редактора с СОБСТВЕННЫМИ EditEditorCD."""
    kb = []

    # === КНОПКИ РЕДАКТИРОВАНИЯ КОНТЕНТА ===

    if ctx.has_media and not ctx.has_text:
        # Медиа без текста
        kb.append([
            types.InlineKeyboardButton(
                text="Добавить описание",
                callback_data=EditEditorCD(action="add_desc", post_id=post_id).pack()
            ),
        ])
    elif ctx.has_media and ctx.has_text:
        # Медиа с текстом
        kb.append([
            types.InlineKeyboardButton(
                text="Изменить описание",
                callback_data=EditEditorCD(action="edit_desc", post_id=post_id).pack()
            ),
        ])
    elif not ctx.has_media and ctx.has_text:
        # Только текст - можно добавить медиа
        kb.append([
            types.InlineKeyboardButton(
                text="Редактировать текст",
                callback_data=EditEditorCD(action="edit_text", post_id=post_id).pack()
            ),
            types.InlineKeyboardButton(
                text="📎 Прикрепить медиа",
                callback_data=EditEditorCD(action="attach_media", post_id=post_id).pack()
            ),
        ])
    else:
        # Пустой пост (не должно быть)
        kb.append([
            types.InlineKeyboardButton(
                text="Редактировать текст",
                callback_data=EditEditorCD(action="edit_text", post_id=post_id).pack()
            ),
        ])

    # === ПОЗИЦИЯ ТЕКСТА ===
    if ctx.has_media and ctx.has_text:
        pos_text = "📝 Текст сверху → снизу" if st.text_position == "top" else "📝 Текст снизу → сверху"
        kb.append([types.InlineKeyboardButton(
            text=pos_text,
            callback_data=EditEditorCD(action="toggle_text_position", post_id=post_id).pack()
        )])

    # === КОЛОКОЛЬЧИК + РЕАКЦИИ ===
    bell_label = "🔔" if st.bell else "🔕"
    kb.append([
        types.InlineKeyboardButton(
            text=bell_label,
            callback_data=EditEditorCD(action="toggle", post_id=post_id, key="bell").pack()
        ),
        types.InlineKeyboardButton(
            text=_with_check("Реакции", st.reactions),
            callback_data=EditEditorCD(action="toggle", post_id=post_id, key="reactions").pack()
        ),
    ])

    # === URL-КНОПКИ ===
    url_text = "✅ URL-Кнопки" if st.has_url_buttons else "URL-Кнопки"
    kb.append([types.InlineKeyboardButton(
        text=url_text,
        callback_data=EditEditorCD(action="url_buttons", post_id=post_id).pack()
    )])

    # === ЗАЩИТА + ЗАКРЕПИТЬ ===
    kb.append([
        types.InlineKeyboardButton(
            text=_with_check("Защита контента", st.content_protect),
            callback_data=EditEditorCD(action="toggle", post_id=post_id, key="content_protect").pack()
        ),
        types.InlineKeyboardButton(
            text=_with_check("Закрепить", st.pin),
            callback_data=EditEditorCD(action="toggle", post_id=post_id, key="pin").pack()
        ),
    ])

    # === КОММЕНТАРИИ ===
    kb.append([types.InlineKeyboardButton(
        text=_with_check("Комментарии", st.comments),
        callback_data=EditEditorCD(action="toggle", post_id=post_id, key="comments").pack()
    )])

    # === СКРЫТОЕ ПРОДОЛЖЕНИЕ ===
    hidden_text = "✅ Скрытое продолжение" if st.has_hidden_part else "Скрытое продолжение"
    kb.append([types.InlineKeyboardButton(
        text=hidden_text,
        callback_data=EditEditorCD(action="hidden_part", post_id=post_id).pack()
    )])

    # === ТАЙМЕР УДАЛЕНИЯ ===
    kb.append([types.InlineKeyboardButton(
        text=f"⏱ Таймер удаления: {format_timer(timer_minutes)}",
        callback_data=EditPostCD(action="timer", target_id=post_id).pack()
    )])

    # === ВРЕМЯ ПУБЛИКАЦИИ ===
    if publish_now:
        pub_text = "🚀 Применить сразу"
    elif publish_time:
        pt = publish_time if isinstance(publish_time, datetime) else datetime.fromisoformat(str(publish_time))
        pub_text = f"📅 {pt.strftime('%H:%M %d.%m.%Y')}"
    else:
        pub_text = "📅 Выбрать время"
    kb.append([types.InlineKeyboardButton(
        text=pub_text,
        callback_data=EditPostCD(action="publish_time", target_id=post_id).pack()
    )])

    # === СОХРАНИТЬ ===
    kb.append([types.InlineKeyboardButton(
        text="✅ Сохранить изменения",
        callback_data=EditPostCD(action="save", target_id=post_id).pack()
    )])

    return types.InlineKeyboardMarkup(inline_keyboard=kb)


async def _refresh_edit_kb(state: FSMContext, bot, chat_id: int, message_id: int):
    """Обновляет клавиатуру редактора."""
    data = await state.get_data()
    st = editor_state_from_dict(data.get("editor", {}))
    ctx = editor_ctx_from_dict(data.get("editor_context", {}))
    post_id = data.get("edit_post_id") or data.get("edit_message_id", 0)

    kb = build_edit_post_kb(
        post_id=post_id,
        st=st,
        ctx=ctx,
        timer_minutes=data.get("timer_minutes", 0),
        publish_now=data.get("publish_now", True),
        publish_time=data.get("publish_time"),
    )

    try:
        await bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=kb)
    except TelegramBadRequest:
        pass


# =============================================================================
# НАЧАЛО
# =============================================================================

@edit_post_router.message(F.text == "Изменить пост")
async def edit_post_start(message: types.Message, state: FSMContext):
    await state.clear()
    await state.set_state(EditPostStates.waiting_forwarded_post)
    await message.answer(EDIT_POST_START_TEXT, parse_mode="HTML", reply_markup=build_edit_post_cancel_kb())


@edit_post_router.callback_query(EditPostCD.filter(F.action == "cancel"))
async def edit_post_cancel(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text("❌ Изменение поста отменено.")
    await call.answer()


# =============================================================================
# ПОЛУЧЕНИЕ ПЕРЕСЛАННОГО ПОСТА
# =============================================================================

@edit_post_router.message(StateFilter(EditPostStates.waiting_forwarded_post), F.forward_from_chat)
async def edit_post_receive_forward(message: types.Message, state: FSMContext, session: AsyncSession):
    chat = message.forward_from_chat
    forward_msg_id = message.forward_from_message_id

    if not chat or chat.type != "channel":
        await message.answer("❌ Перешлите сообщение из канала.")
        return
    if not forward_msg_id:
        await message.answer("❌ Не удалось определить ID сообщения.")
        return

    try:
        user_member = await message.bot.get_chat_member(chat.id, message.from_user.id)
        if user_member.status not in ("administrator", "creator"):
            await message.answer("❌ Вы не являетесь администратором этого канала.")
            return
    except Exception:
        await message.answer("❌ Не удалось проверить права.")
        return

    # Ищем в БД
    q = (
        select(PostTarget)
        .where(PostTarget.channel_id == chat.id)
        .where(PostTarget.sent_message_id == forward_msg_id)
        .options(
            joinedload(PostTarget.post).selectinload(Post.media),
            joinedload(PostTarget.post).selectinload(Post.buttons),
            joinedload(PostTarget.post).joinedload(Post.hidden_part),
        )
    )
    result = await session.execute(q)
    target = result.unique().scalars().first()

    # Извлекаем данные
    post_id, target_id, timer_minutes = 0, 0, 0
    original_text = None
    db_bell, db_reactions, db_protect, db_pin, db_comments = True, False, False, False, False
    db_text_pos, db_has_btns, db_has_hidden = "bottom", False, False

    if target:
        target_id, post_id = target.id, target.post_id
        post = target.post
        original_text = post.text
        db_bell = not post.silent
        db_reactions = post.reactions_enabled
        db_protect = post.protected
        db_pin = post.pinned
        db_comments = post.comments_enabled
        db_text_pos = post.text_position or "bottom"
        db_has_btns = bool(post.buttons)
        db_has_hidden = bool(post.hidden_part)
        if target.auto_delete_after:
            timer_minutes = int(target.auto_delete_after.total_seconds() // 60)

    # Тип контента
    has_media, has_text, kind = False, False, "text"
    if message.photo:
        has_media, kind = True, "photo"
    elif message.video:
        has_media, kind = True, "photo"
    elif message.document:
        has_media, kind = True, "other_media"
    elif message.voice or message.audio:
        has_media, kind = True, "voice"

    text_content = message.caption or message.text
    if text_content:
        has_text = True
        if not original_text:
            original_text = text_content

    ctx = EditorContext(
        kind=kind,
        has_media=has_media,
        has_text=has_text,
        text_was_initial=has_text,
        text_added_later=False
    )
    st = EditorState(
        post_id=post_id,
        preview_chat_id=message.chat.id,
        preview_message_id=0,
        bell=db_bell,
        reactions=db_reactions,
        content_protect=db_protect,
        comments=db_comments,
        pin=db_pin,
        text_position=db_text_pos,
        selected_channels_count=1,
        has_url_buttons=db_has_btns,
        has_hidden_part=db_has_hidden,
    )

    await state.update_data(
        edit_channel_id=chat.id,
        edit_channel_title=chat.title,
        edit_message_id=forward_msg_id,
        edit_target_id=target_id,
        edit_post_id=post_id,
        edit_new_text=original_text,
        text_changed=False,
        timer_minutes=timer_minutes,
        publish_now=True,
        publish_time=None,
        editor=editor_state_to_dict(st),
        editor_context=editor_ctx_to_dict(ctx),
    )
    await state.set_state(EditPostStates.editing)

    # Копируем сообщение
    try:
        copied = await message.bot.copy_message(
            chat_id=message.chat.id,
            from_chat_id=chat.id,
            message_id=forward_msg_id
        )
        st.preview_message_id = copied.message_id
        await state.update_data(
            edit_preview_message_id=copied.message_id,
            editor=editor_state_to_dict(st)
        )

        kb = build_edit_post_kb(post_id or forward_msg_id, st, ctx, timer_minutes, True, None)
        await message.bot.edit_message_reply_markup(
            chat_id=message.chat.id,
            message_id=copied.message_id,
            reply_markup=kb
        )
    except Exception:
        kb = build_edit_post_kb(post_id or forward_msg_id, st, ctx, timer_minutes, True, None)
        await message.answer(
            f"📝 Редактирование поста из <b>{chat.title}</b>",
            parse_mode="HTML",
            reply_markup=kb
        )


# =============================================================================
# ОБРАБОТЧИКИ EditEditorCD
# =============================================================================

@edit_post_router.callback_query(EditEditorCD.filter(F.action == "toggle"))
async def edit_post_toggle(call: types.CallbackQuery, callback_data: EditEditorCD, state: FSMContext):
    """Переключение настроек поста."""
    current_state = await state.get_state()
    if current_state != EditPostStates.editing:
        await call.answer("Сессия редактирования не активна", show_alert=True)
        return

    key = callback_data.key
    data = await state.get_data()
    st = editor_state_from_dict(data.get("editor", {}))

    if key == "bell":
        st.bell = not st.bell
        await call.answer("🔔 С уведомлением" if st.bell else "🔕 Без уведомления")
    elif key == "reactions":
        # ОГРАНИЧЕНИЕ API: реакции настраиваются на уровне канала!
        await call.answer(
            "⚠️ Реакции настраиваются в настройках канала, не отдельного поста.\n\n"
            "Telegram API не позволяет включить/выключить реакции для конкретного сообщения.",
            show_alert=True
        )
        return
    elif key == "content_protect":
        st.content_protect = not st.content_protect
        await call.answer("🔒 Защита включена" if st.content_protect else "🔓 Защита выключена")
    elif key == "pin":
        st.pin = not st.pin
        await call.answer("📌 Будет закреплён" if st.pin else "📌 Без закрепления")
    elif key == "comments":
        # ОГРАНИЧЕНИЕ API: комментарии зависят от привязанного чата обсуждения!
        await call.answer(
            "⚠️ Комментарии работают если к каналу привязан чат обсуждения.\n\n"
            "Telegram API не позволяет включить/выключить комментарии для конкретного сообщения.",
            show_alert=True
        )
        return

    await state.update_data(editor=editor_state_to_dict(st))
    await _refresh_edit_kb(state, call.bot, call.message.chat.id, data.get("edit_preview_message_id"))


@edit_post_router.callback_query(EditEditorCD.filter(F.action == "toggle_text_position"))
async def edit_post_toggle_pos(call: types.CallbackQuery, state: FSMContext):
    """Переключение позиции текста."""
    current_state = await state.get_state()
    if current_state != EditPostStates.editing:
        await call.answer("Сессия редактирования не активна", show_alert=True)
        return

    data = await state.get_data()
    st = editor_state_from_dict(data.get("editor", {}))
    st.text_position = "top" if st.text_position == "bottom" else "bottom"
    await state.update_data(editor=editor_state_to_dict(st))
    await _refresh_edit_kb(state, call.bot, call.message.chat.id, data.get("edit_preview_message_id"))
    await call.answer("📝 Текст сверху" if st.text_position == "top" else "📝 Текст снизу")


@edit_post_router.callback_query(EditEditorCD.filter(F.action.in_(["edit_text", "edit_desc", "add_desc"])))
async def edit_post_edit_text(call: types.CallbackQuery, state: FSMContext):
    """Начать редактирование текста."""
    current_state = await state.get_state()
    if current_state != EditPostStates.editing:
        await call.answer("Сессия редактирования не активна", show_alert=True)
        return

    await state.set_state(EditPostStates.editing_text)
    await call.message.answer(EDIT_TEXT_PROMPT, reply_markup=build_back_to_edit_kb())
    await call.answer()


@edit_post_router.message(StateFilter(EditPostStates.editing_text), F.text)
async def edit_post_receive_new_text(message: types.Message, state: FSMContext):
    """Получение нового текста."""
    new_text = message.text
    data = await state.get_data()
    ctx = editor_ctx_from_dict(data.get("editor_context", {}))

    ctx.has_text = True
    if not ctx.text_was_initial:
        ctx.text_added_later = True

    await state.update_data(
        edit_new_text=new_text,
        text_changed=True,
        editor_context=editor_ctx_to_dict(ctx)
    )
    await state.set_state(EditPostStates.editing)

    preview_msg_id = data.get("edit_preview_message_id")
    if preview_msg_id:
        try:
            if ctx.has_media:
                await message.bot.edit_message_caption(
                    chat_id=message.chat.id,
                    message_id=preview_msg_id,
                    caption=new_text
                )
            else:
                await message.bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=preview_msg_id,
                    text=new_text
                )
        except Exception:
            pass

    await _refresh_edit_kb(state, message.bot, message.chat.id, preview_msg_id)
    await message.answer("✅ Текст обновлён")


@edit_post_router.callback_query(EditEditorCD.filter(F.action == "attach_media"))
async def edit_post_attach_media(call: types.CallbackQuery, state: FSMContext):
    """Прикрепление медиа - ограничение API."""
    await call.answer(
        "⚠️ Telegram API не позволяет добавить медиа к уже опубликованному текстовому сообщению.\n\n"
        "Для добавления медиа нужно удалить текущий пост и создать новый.",
        show_alert=True
    )


@edit_post_router.callback_query(EditEditorCD.filter(F.action.in_(["url_buttons", "hidden_part"])))
async def edit_post_unsupported(call: types.CallbackQuery):
    """Неподдерживаемые действия."""
    await call.answer("⚠️ Недоступно при редактировании существующего поста", show_alert=True)


# =============================================================================
# ТАЙМЕР УДАЛЕНИЯ
# =============================================================================

@edit_post_router.callback_query(EditPostCD.filter(F.action == "timer"))
async def edit_post_timer(call: types.CallbackQuery, state: FSMContext):
    current_state = await state.get_state()
    if current_state != EditPostStates.editing:
        await call.answer("Сессия редактирования не активна", show_alert=True)
        return

    data = await state.get_data()
    await state.set_state(EditPostStates.selecting_timer)
    await call.message.answer(
        TIMER_SELECT_TEXT,
        parse_mode="HTML",
        reply_markup=build_timer_select_kb(data.get("timer_minutes", 0))
    )
    await call.answer()


@edit_post_router.callback_query(EditTimerCD.filter(F.action == "select"))
async def edit_post_timer_chosen(call: types.CallbackQuery, callback_data: EditTimerCD, state: FSMContext):
    minutes = callback_data.minutes
    await state.update_data(timer_minutes=minutes)
    await state.set_state(EditPostStates.editing)

    try:
        await call.message.delete()
    except:
        pass

    data = await state.get_data()
    await _refresh_edit_kb(state, call.bot, call.message.chat.id, data.get("edit_preview_message_id"))
    await call.answer(f"<tg-emoji emoji-id=\"{PREMIUM_EMOJI['sign']}\">✅</tg-emoji> Таймер: {format_timer(minutes)}")


@edit_post_router.callback_query(EditTimerCD.filter(F.action == "back"))
async def edit_post_timer_back(call: types.CallbackQuery, state: FSMContext):
    await state.set_state(EditPostStates.editing)
    try:
        await call.message.delete()
    except:
        pass
    await call.answer()


# =============================================================================
# ВРЕМЯ ПУБЛИКАЦИИ
# =============================================================================

@edit_post_router.callback_query(EditPostCD.filter(F.action == "publish_time"))
async def edit_post_pub_time(call: types.CallbackQuery, state: FSMContext):
    current_state = await state.get_state()
    if current_state != EditPostStates.editing:
        await call.answer("Сессия редактирования не активна", show_alert=True)
        return

    await call.message.answer(
        f"<tg-emoji emoji-id=\"{PREMIUM_EMOJI['cont_plan']}\">🗓</tg-emoji> <b>ВРЕМЯ ПУБЛИКАЦИИ</b>",
        parse_mode="HTML",
        reply_markup=build_publish_time_kb()
    )
    await call.answer()


@edit_post_router.callback_query(EditPublishCD.filter(F.action == "now"))
async def edit_post_pub_now(call: types.CallbackQuery, state: FSMContext):
    await state.update_data(publish_now=True, publish_time=None)
    await state.set_state(EditPostStates.editing)

    try:
        await call.message.delete()
    except:
        pass

    data = await state.get_data()
    await _refresh_edit_kb(state, call.bot, call.message.chat.id, data.get("edit_preview_message_id"))
    await call.answer("✅ Применить сразу")


@edit_post_router.callback_query(EditPublishCD.filter(F.action == "schedule"))
async def edit_post_schedule(call: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    user = await orm_get_user(session, user_id=call.from_user.id)
    user_tz = user.timezone if user else "Europe/Moscow"
    await state.set_state(EditPostStates.entering_publish_time)
    await call.message.edit_text(
        get_publish_time_text(user_tz),
        parse_mode="HTML",
        reply_markup=build_back_to_edit_kb()
    )
    await call.answer()


@edit_post_router.message(StateFilter(EditPostStates.entering_publish_time), F.text)
async def edit_post_receive_time(message: types.Message, state: FSMContext):
    text = message.text.strip()
    patterns = [
        r"^(\d{1,2}):(\d{2})\s+(\d{1,2})\.(\d{1,2})\.(\d{4})$",
        r"^(\d{1,2}):(\d{2})\s+(\d{1,2})\.(\d{1,2})\.(\d{2})$"
    ]

    parsed = None
    for pattern in patterns:
        match = re.match(pattern, text)
        if match:
            h, m, d, mo, y = match.groups()
            h, m, d, mo, y = int(h), int(m), int(d), int(mo), int(y)
            if y < 100:
                y += 2000
            try:
                parsed = datetime(y, mo, d, h, m)
            except:
                pass
            break

    if not parsed:
        await message.answer("❌ Неверный формат. Пример: <code>18:01 16.8.2025</code>", parse_mode="HTML")
        return
    if parsed <= datetime.now():
        await message.answer("❌ Время должно быть в будущем.", parse_mode="HTML")
        return

    await state.update_data(publish_now=False, publish_time=parsed)
    await state.set_state(EditPostStates.editing)
    data = await state.get_data()
    await _refresh_edit_kb(state, message.bot, message.chat.id, data.get("edit_preview_message_id"))
    await message.answer(f"<tg-emoji emoji-id=\"{PREMIUM_EMOJI['sign']}\">✅</tg-emoji> Запланировано на {parsed.strftime('%H:%M %d.%m.%Y')}", parse_mode="HTML")


@edit_post_router.callback_query(EditPublishCD.filter(F.action == "back"))
async def edit_post_pub_back(call: types.CallbackQuery, state: FSMContext):
    await state.set_state(EditPostStates.editing)
    try:
        await call.message.delete()
    except:
        pass
    await call.answer()


# =============================================================================
# СОХРАНЕНИЕ
# =============================================================================

@edit_post_router.callback_query(EditPostCD.filter(F.action == "save"))
async def edit_post_save(call: types.CallbackQuery, state: FSMContext):
    current_state = await state.get_state()
    if current_state != EditPostStates.editing:
        await call.answer("Сессия редактирования не активна", show_alert=True)
        return

    data = await state.get_data()
    await call.message.answer(
        CONFIRM_TEXT,
        parse_mode="HTML",
        reply_markup=build_confirm_kb(data.get("edit_target_id") or data.get("edit_message_id", 0))
    )
    await call.answer()


@edit_post_router.callback_query(EditPostCD.filter(F.action == "confirm_no"))
async def edit_post_no(call: types.CallbackQuery):
    try:
        await call.message.delete()
    except:
        pass
    await call.answer()


@edit_post_router.callback_query(EditPostCD.filter(F.action == "confirm_yes"))
async def edit_post_confirm(call: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    data = await state.get_data()

    channel_id = data.get("edit_channel_id")
    message_id = data.get("edit_message_id")
    target_id = data.get("edit_target_id")
    new_text = data.get("edit_new_text")
    text_changed = data.get("text_changed", False)

    st = editor_state_from_dict(data.get("editor", {}))
    ctx = editor_ctx_from_dict(data.get("editor_context", {}))

    timer_minutes = data.get("timer_minutes", 0)
    publish_now = data.get("publish_now", True)

    auto_delete_after = timedelta(minutes=timer_minutes) if timer_minutes > 0 else None

    errors, success = [], []

    try:
        if publish_now:
            # 1. Редактируем текст ТОЛЬКО ЕСЛИ ОН БЫЛ ИЗМЕНЁН
            if new_text and text_changed:
                try:
                    if ctx.has_media:
                        await call.bot.edit_message_caption(
                            chat_id=channel_id,
                            message_id=message_id,
                            caption=new_text
                        )
                    else:
                        await call.bot.edit_message_text(
                            chat_id=channel_id,
                            message_id=message_id,
                            text=new_text
                        )
                    success.append("текст")
                except TelegramBadRequest as e:
                    if "not modified" not in str(e):
                        errors.append(f"текст")

            # 2. Закрепляем
            if st.pin:
                try:
                    await call.bot.pin_chat_message(
                        chat_id=channel_id,
                        message_id=message_id,
                        disable_notification=not st.bell
                    )
                    success.append("закреплено")
                except Exception:
                    errors.append("закрепление")

            # 3. БД
            if target_id:
                target = await session.get(PostTarget, target_id)
                if target:
                    update_values = {
                        "silent": not st.bell,
                        "reactions_enabled": st.reactions,
                        "protected": st.content_protect,
                        "pinned": st.pin,
                        "comments_enabled": st.comments,
                        "text_position": st.text_position,
                    }
                    if new_text and text_changed:
                        update_values["text"] = new_text

                    await session.execute(
                        update(Post).where(Post.id == target.post_id).values(**update_values)
                    )
                    target.auto_delete_after = auto_delete_after
                    target.auto_delete_at = (datetime.utcnow() + auto_delete_after) if auto_delete_after else None
                    await session.commit()
                    success.append("БД")

            await state.clear()

            result = f"<tg-emoji emoji-id=\"{PREMIUM_EMOJI['sign']}\">✍✅</tg-emoji> <b>Изменения применены!</b>\n\nКанал: {data.get('edit_channel_title', 'Канал')}"
            if success:
                result += f"\nОбновлено: {', '.join(success)}"
            if errors:
                result += f"\n⚠️ Ошибки: {', '.join(errors)}"
            if timer_minutes > 0:
                result += f"\n⏱ Автоудаление через: {format_timer(timer_minutes)}"

            await call.message.edit_text(result, parse_mode="HTML")
        else:
            # Запланировано
            if target_id:
                target = await session.get(PostTarget, target_id)
                if target:
                    update_values = {
                        "silent": not st.bell,
                        "reactions_enabled": st.reactions,
                        "protected": st.content_protect,
                        "pinned": st.pin,
                        "comments_enabled": st.comments,
                        "text_position": st.text_position,
                    }
                    if new_text and text_changed:
                        update_values["text"] = new_text

                    await session.execute(
                        update(Post).where(Post.id == target.post_id).values(**update_values)
                    )
                    target.auto_delete_after = auto_delete_after
                    await session.commit()

            await state.clear()
            pt = data.get("publish_time")
            time_str = pt.strftime('%H:%M %d.%m.%Y') if pt else 'сразу'
            await call.message.edit_text(
                f"<tg-emoji emoji-id=\"{PREMIUM_EMOJI['sign']}\">✅</tg-emoji> <b>Сохранено!</b>\n\nКанал: {data.get('edit_channel_title')}\n"
                f"<tg-emoji emoji-id=\"{PREMIUM_EMOJI['clock']}\">🕔</tg-emoji> Время: {time_str}\nТаймер: {format_timer(timer_minutes)}",
                parse_mode="HTML"
            )

        await call.answer("✅ Сохранено!")
    except Exception as e:
        await call.answer(f"❌ Ошибка: {str(e)[:50]}", show_alert=True)