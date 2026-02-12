from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Literal

from aiogram import types
from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

PostKind = Literal["photo", "voice", "text", "other_media"]

# что у нас "переключаемое" (чтобы появлялась ✅)
TOGGLE_KEYS = (
    "hidden",
    "bell",
    "reactions",
    "content_protect",
    "comments",
    "pin",
    "copy",
    "repost",
    "reply_post",
)


class EditTextCD(CallbackData, prefix="et"):
    action: str  # back | delete | cancel_attach
    post_id: int


class EditorCD(CallbackData, prefix="ed"):
    action: str
    post_id: int = 0
    key: str = ""  # для toggle


class CopyPostCD(CallbackData, prefix="copypost"):
    """CallbackData для функции копирования поста в другие каналы."""
    action: str  # select_channel | select_all | deselect_all | apply | back
    post_id: int = 0
    channel_id: int = 0  # для выбора конкретного канала

class UrlButtonsCD(CallbackData, prefix="urlbtn"):
    """CallbackData для управления URL-кнопками."""
    action: str          # delete | back
    post_id: int = 0

@dataclass
class EditorState:
    """
    Универсальное состояние редактора, которое можно использовать в 6 местах.
    Храним в FSM.
    """
    post_id: int
    preview_chat_id: int
    preview_message_id: int

    # toggles
    hidden: bool = False
    bell: bool = True  # 🔔/🔕 - уведомление при отправке
    reactions: bool = True  # По умолчанию реакции включены
    content_protect: bool = False  # Защита контента (антикопирование)
    comments: bool = True
    pin: bool = False  # Закрепить пост
    copy: bool = False
    repost: bool = False
    reply_post: bool = False

    has_url_buttons: bool = False
    has_hidden_part: bool = False    # Есть скрытое продолжение
    text_position: str = "bottom"
    reply_to_channel_id: int | None = None
    reply_to_message_id: int | None = None
    has_reactions: bool = False


    # Количество выбранных каналов (нужно для скрытия кнопок)
    selected_channels_count: int = 1

class HiddenPartCD(CallbackData, prefix="hidden"):
    """CallbackData для скрытого продолжения."""
    action: str          # edit_name | edit_text | edit_hidden_text | delete | save | back | skip
    post_id: int = 0

def _with_check(label: str, enabled: bool) -> str:
    return f"✅ {label}" if enabled else label


def build_editor_kb(post_id: int, st: EditorState, ctx: 'EditorContext') -> InlineKeyboardMarkup:
    kb: list[list[InlineKeyboardButton]] = []

    # ========== ВЕРХНИЕ КНОПКИ (по типу контента) ==========

    if ctx.kind == "photo" and ctx.has_media and not ctx.has_text:
        kb.append([
            InlineKeyboardButton(text="Медиа", callback_data=EditorCD(action="media", post_id=post_id).pack()),
            InlineKeyboardButton(text="Добавить описание",
                                 callback_data=EditorCD(action="add_desc", post_id=post_id).pack()),
        ])

    elif ctx.kind == "photo" and ctx.has_media and ctx.has_text and ctx.text_added_later:
        kb.append([
            InlineKeyboardButton(text="Медиа", callback_data=EditorCD(action="media", post_id=post_id).pack()),
            InlineKeyboardButton(text="Изменить описание",
                                 callback_data=EditorCD(action="edit_desc", post_id=post_id).pack()),
        ])

    elif ctx.kind == "photo" and ctx.has_media and ctx.has_text and ctx.text_was_initial:
        kb.append([
            InlineKeyboardButton(text="Изменить текст",
                                 callback_data=EditorCD(action="edit_text", post_id=post_id).pack()),
            InlineKeyboardButton(text="Открепить медиа",
                                 callback_data=EditorCD(action="detach_media", post_id=post_id).pack()),
        ])

    elif ctx.kind == "voice":
        if ctx.has_text:
            kb.append([
                InlineKeyboardButton(text="Изменить описание",
                                     callback_data=EditorCD(action="edit_desc", post_id=post_id).pack()),
            ])
        else:
            kb.append([
                InlineKeyboardButton(text="Добавить описание",
                                     callback_data=EditorCD(action="add_desc", post_id=post_id).pack()),
            ])

    elif ctx.kind == "other_media" and ctx.has_media and ctx.has_text:
        kb.append([
            InlineKeyboardButton(text="Изменить текст",
                                 callback_data=EditorCD(action="edit_text", post_id=post_id).pack()),
            InlineKeyboardButton(text="Открепить медиа",
                                 callback_data=EditorCD(action="detach_media", post_id=post_id).pack()),
        ])

    else:
        kb.append([
            InlineKeyboardButton(text="Редактировать текст",
                                 callback_data=EditorCD(action="edit_text", post_id=post_id).pack()),
            InlineKeyboardButton(text="Прикрепить медиа",
                                 callback_data=EditorCD(action="attach_media", post_id=post_id).pack()),
        ])


    # ========== КНОПКА ПОЗИЦИИ ТЕКСТА (только для фото/видео с текстом) ==========
    if ctx.has_media and ctx.has_text and ctx.kind in ("photo", "other_media") and not getattr(ctx, 'is_album', False):        # Показываем ТЕКУЩУЮ позицию и что будет при нажатии
        if st.text_position == "top":
            pos_btn_text = "📝 Текст сверху → снизу"
        else:
            pos_btn_text = "📝 Текст снизу → сверху"

        kb.append([
            InlineKeyboardButton(
                text=pos_btn_text,
                callback_data=EditorCD(action="toggle_text_position", post_id=post_id).pack()
            ),
        ])

    # ========== ОБЩИЕ КНОПКИ ==========

    # Колокольчик + Реакции
    bell_label = "🔔" if st.bell else "🔕"
    kb.append([
        InlineKeyboardButton(
            text=bell_label,
            callback_data=EditorCD(action="toggle", post_id=post_id, key="bell").pack()
        ),
    ])
    reaction_text = "✅ Реакции" if st.has_reactions else "Реакции"
    kb.append([
        InlineKeyboardButton(
            text=reaction_text,
            callback_data=EditorCD(action="reactions", post_id=post_id).pack()
        ),
    ])

    # URL-Кнопки
    url_btn_text = "✅ URL-Кнопки" if st.has_url_buttons else "URL-Кнопки"
    kb.append([
        InlineKeyboardButton(
            text=url_btn_text,
            callback_data=EditorCD(action="url_buttons", post_id=post_id).pack()
        ),
    ])

    # Защита контента + Закрепить
    kb.append([
        InlineKeyboardButton(
            text=_with_check("Защита контента", st.content_protect),
            callback_data=EditorCD(action="toggle", post_id=post_id, key="content_protect").pack()
        ),
        InlineKeyboardButton(
            text=_with_check("Закрепить", st.pin),
            callback_data=EditorCD(action="toggle", post_id=post_id, key="pin").pack()
        ),
    ])
    kb.append([
        InlineKeyboardButton(
            text=_with_check("Репост", st.repost),
            callback_data=EditorCD(action="toggle", post_id=post_id, key="repost").pack()
        ),
    ])

    # Комментарии + Ответный пост
    comments_btn = InlineKeyboardButton(
        text=_with_check("Комментарии", st.comments),
        callback_data=EditorCD(action="toggle", post_id=post_id, key="comments").pack()
    )

    # Ответный пост показываем только если выбран 1 канал
    if st.selected_channels_count == 1:
        reply_text = "✅ Ответный пост" if st.reply_post else "Ответный пост"
        reply_btn = InlineKeyboardButton(
            text=reply_text,
            callback_data=ReplyPostCD(action="setup", post_id=post_id).pack()
        )
        kb.append([comments_btn, reply_btn])
    else:
        # Если несколько каналов - только комментарии
        kb.append([comments_btn])

    # Скрытое продолжение
    hidden_text = "✅ Скрытое продолжение" if st.has_hidden_part else "Скрытое продолжение"
    kb.append([
        InlineKeyboardButton(
            text=hidden_text,
            callback_data=EditorCD(action="hidden_part", post_id=post_id).pack()
        ),
    ])

    # Копировать
    if not st.reply_post:
        kb.append([
            InlineKeyboardButton(
                text="📋 Копировать",
                callback_data=EditorCD(action="copy_to_channels", post_id=post_id).pack()
            ),
        ])

    kb.append([
        InlineKeyboardButton(
            text="❌ Отменить",
            callback_data=EditorCD(action="cancel", post_id=post_id).pack()
        ),
    ])

    # Продолжить
    kb.append([
        InlineKeyboardButton(
            text="Продолжить ➡️",
            callback_data=EditorCD(action="continue", post_id=post_id).pack()
        ),
    ])

    return InlineKeyboardMarkup(inline_keyboard=kb)


def build_copy_channels_kb(
        post_id: int,
        channels: list,
        selected_ids: set[int],
) -> InlineKeyboardMarkup:
    """
    Клавиатура для выбора каналов при копировании поста.

    Args:
        post_id: ID поста
        channels: Список всех каналов пользователя
        selected_ids: Множество ID выбранных каналов
    """
    kb: list[list[InlineKeyboardButton]] = []

    # Список каналов с галочками
    for ch in channels:
        ch_id = int(ch.id)
        mark = "✅" if ch_id in selected_ids else "⬜"
        kb.append([
            InlineKeyboardButton(
                text=f"{mark} {ch.title}",
                callback_data=CopyPostCD(action="select_channel", post_id=post_id, channel_id=ch_id).pack()
            )
        ])

    # Кнопка "Выбрать все" / "Убрать все" - работает как toggle
    all_channel_ids = {int(ch.id) for ch in channels}
    all_selected = selected_ids == all_channel_ids and len(channels) > 0

    toggle_all_text = "☑️ Убрать все" if all_selected else "✅ Выбрать все"
    kb.append([
        InlineKeyboardButton(
            text=toggle_all_text,
            callback_data=CopyPostCD(action="toggle_all", post_id=post_id).pack()
        ),
    ])

    # Кнопка "Применить"
    kb.append([
        InlineKeyboardButton(
            text="✅ Применить",
            callback_data=CopyPostCD(action="apply", post_id=post_id).pack()
        ),
    ])

    # Кнопка "Назад"
    kb.append([
        InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=CopyPostCD(action="back", post_id=post_id).pack()
        ),
    ])

    return InlineKeyboardMarkup(inline_keyboard=kb)


def build_url_buttons_kb(buttons: list[dict]) -> InlineKeyboardMarkup:
    """
    Строит клавиатуру из пользовательских URL-кнопок.

    Args:
        buttons: Список словарей с ключами 'text', 'url', 'row', 'position'

    Returns:
        InlineKeyboardMarkup с URL-кнопками
    """
    if not buttons:
        return None

    # Группируем кнопки по рядам
    rows: dict[int, list[dict]] = {}
    for btn in buttons:
        row_num = btn.get('row', 0)
        if row_num not in rows:
            rows[row_num] = []
        rows[row_num].append(btn)

    # Сортируем кнопки в каждом ряду по position
    kb: list[list[InlineKeyboardButton]] = []
    for row_num in sorted(rows.keys()):
        row_buttons = sorted(rows[row_num], key=lambda x: x.get('position', 0))
        kb_row = [
            InlineKeyboardButton(text=btn['text'], url=btn['url'])
            for btn in row_buttons
        ]
        kb.append(kb_row)

    return InlineKeyboardMarkup(inline_keyboard=kb)


def merge_url_and_editor_kb(
        url_buttons: list[dict],
        editor_kb: InlineKeyboardMarkup
) -> InlineKeyboardMarkup:
    """
    Объединяет URL-кнопки пользователя с кнопками редактора.
    URL-кнопки идут первыми, затем кнопки редактора.
    """
    kb: list[list[InlineKeyboardButton]] = []

    # Сначала добавляем URL-кнопки
    if url_buttons:
        url_kb = build_url_buttons_kb(url_buttons)
        if url_kb:
            kb.extend(url_kb.inline_keyboard)

    # Затем добавляем кнопки редактора
    kb.extend(editor_kb.inline_keyboard)

    return InlineKeyboardMarkup(inline_keyboard=kb)


def build_url_buttons_prompt_kb(post_id: int, has_buttons: bool = False) -> InlineKeyboardMarkup:
    """
    Клавиатура для режима ввода URL-кнопок.
    """
    kb = []

    if has_buttons:
        kb.append([
            InlineKeyboardButton(
                text="🗑 Удалить кнопки",
                callback_data=UrlButtonsCD(action="delete", post_id=post_id).pack()
            )
        ])

    kb.append([
        InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=UrlButtonsCD(action="back", post_id=post_id).pack()
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=kb)

def build_hidden_part_input_kb(post_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=HiddenPartCD(action="back", post_id=post_id).pack())]
    ])


def build_hidden_part_skip_kb(post_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⬅️ Назад", callback_data=HiddenPartCD(action="back", post_id=post_id).pack()),
            InlineKeyboardButton(text="Пропустить ➡️", callback_data=HiddenPartCD(action="skip", post_id=post_id).pack()),
        ]
    ])


def build_hidden_part_settings_kb(post_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить название кнопки", callback_data=HiddenPartCD(action="edit_name", post_id=post_id).pack())],
        [InlineKeyboardButton(text="📝 Изменить текст", callback_data=HiddenPartCD(action="edit_text", post_id=post_id).pack())],
        [InlineKeyboardButton(text="🔒 Изменить скрытый текст", callback_data=HiddenPartCD(action="edit_hidden_text", post_id=post_id).pack())],
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=HiddenPartCD(action="delete", post_id=post_id).pack())],
        [InlineKeyboardButton(text="✅ Сохранить и продолжить", callback_data=HiddenPartCD(action="save", post_id=post_id).pack())],
    ])




def editor_state_to_dict(st: EditorState) -> dict:
    return {
        "post_id": st.post_id,
        "preview_chat_id": st.preview_chat_id,
        "preview_message_id": st.preview_message_id,
        "hidden": st.hidden,
        "bell": st.bell,
        "reactions": st.reactions,
        "content_protect": st.content_protect,
        "comments": st.comments,
        "pin": st.pin,
        "copy": st.copy,
        "repost": st.repost,
        "reply_post": st.reply_post,
        "has_url_buttons": st.has_url_buttons,
        "has_hidden_part": st.has_hidden_part,
        "has_reactions": st.has_reactions,
        "text_position": st.text_position,
        "reply_to_channel_id": st.reply_to_channel_id,
        "reply_to_message_id": st.reply_to_message_id,
        "selected_channels_count": st.selected_channels_count,
    }


def editor_state_from_dict(d: dict) -> EditorState:
    return EditorState(
        post_id=int(d["post_id"]),
        preview_chat_id=int(d["preview_chat_id"]),
        preview_message_id=int(d["preview_message_id"]),
        hidden=bool(d.get("hidden", False)),
        bell=bool(d.get("bell", True)),
        reactions=bool(d.get("reactions", True)),
        content_protect=bool(d.get("content_protect", False)),
        comments=bool(d.get("comments", True)),
        pin=bool(d.get("pin", False)),
        copy=bool(d.get("copy", False)),
        repost=bool(d.get("repost", False)),
        reply_post=bool(d.get("reply_post", False)),
        has_url_buttons=bool(d.get("has_url_buttons", False)),
        has_hidden_part=bool(d.get("has_hidden_part", False)),
        has_reactions=bool(d.get("has_reactions", False)),
        text_position=d.get("text_position", "bottom"),
        reply_to_channel_id = d.get("reply_to_channel_id"),
        reply_to_message_id = d.get("reply_to_message_id"),
        selected_channels_count = int(d.get("selected_channels_count", 1)),
    )

def editor_ctx_to_dict(ctx: 'EditorContext') -> dict:
    """Сериализует EditorContext в словарь для FSM."""
    return {
        "kind": ctx.kind,
        "has_media": ctx.has_media,
        "has_text": ctx.has_text,
        "text_was_initial": ctx.text_was_initial,
        "text_added_later": ctx.text_added_later,
    }


def editor_ctx_from_dict(d: dict) -> 'EditorContext':
    """Десериализует EditorContext из словаря."""
    return EditorContext(
        kind=d.get("kind", "text"),
        has_media=bool(d.get("has_media", False)),
        has_text=bool(d.get("has_text", True)),
        text_was_initial=bool(d.get("text_was_initial", True)),
        text_added_later=bool(d.get("text_added_later", False)),
    )


@dataclass
class EditorContext:
    kind: PostKind
    has_media: bool
    has_text: bool
    text_was_initial: bool
    text_added_later: bool
    is_album: bool = False

class ReplyPostCD(CallbackData, prefix="reply"):
    """CallbackData для ответного поста."""
    action: str  # setup | content_plan | back | remove
    post_id: int = 0

def make_ctx_from_message(message: types.Message) -> EditorContext:
    if message.voice:
        return EditorContext(
            kind="voice",
            has_media=True,
            has_text=bool(message.caption or message.text),
            text_was_initial=bool(message.caption),
            text_added_later=False
        )
    if message.photo:
        has_text = bool(message.caption)
        return EditorContext(
            kind="photo",
            has_media=True,
            has_text=has_text,
            text_was_initial=has_text,
            text_added_later=False
        )
    if message.video or message.document or message.animation:
        has_text = bool(message.caption)
        return EditorContext(
            kind="other_media",
            has_media=True,
            has_text=has_text,
            text_was_initial=has_text,
            text_added_later=False
        )
    if message.text and not (message.photo or message.voice or message.video or message.document):
        return EditorContext(
            kind="text",
            has_media=False,
            has_text=True,
            text_was_initial=True,
            text_added_later=False
        )

    return EditorContext(
        kind="other_media",
        has_media=True,
        has_text=bool(message.caption),
        text_was_initial=bool(message.caption),
        text_added_later=False
    )

def build_reply_post_setup_kb(post_id: int) -> InlineKeyboardMarkup:
    """Клавиатура для настройки ответного поста."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="📋 Выбрать пост из контент плана",
            callback_data=ReplyPostCD(action="content_plan", post_id=post_id).pack()
        )],
        [InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=ReplyPostCD(action="back", post_id=post_id).pack()
        )],
    ])


def build_reply_post_settings_kb(post_id: int) -> InlineKeyboardMarkup:
    """Клавиатура настроек когда ответный пост уже выбран."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🔄 Изменить",
            callback_data=ReplyPostCD(action="change", post_id=post_id).pack()
        )],
        [InlineKeyboardButton(
            text="🗑 Удалить",
            callback_data=ReplyPostCD(action="remove", post_id=post_id).pack()
        )],
        [InlineKeyboardButton(
            text="✅ Сохранить",
            callback_data=ReplyPostCD(action="save", post_id=post_id).pack()
        )],
    ])


def build_reply_post_input_kb(post_id: int) -> InlineKeyboardMarkup:
    """Клавиатура во время ожидания пересланного сообщения."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="📋 Выбрать пост из контент плана",
            callback_data=ReplyPostCD(action="content_plan", post_id=post_id).pack()
        )],
        [InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=ReplyPostCD(action="back", post_id=post_id).pack()
        )],
    ])
