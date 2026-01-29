from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from kbds.callbacks import CreatePostCD
from kbds.post_editor import EditTextCD


def get_callback_btns(
        *,
        btns: dict[str, str],
        sizes: tuple[int] = (1,)):
    keyboard = InlineKeyboardBuilder()

    for text, data in btns.items():
        keyboard.add(InlineKeyboardButton(text=text, callback_data=data))

    return keyboard.adjust(*sizes).as_markup()


def get_url_btns(
        *,
        btns: dict[str, str],
        sizes: tuple[int] = (2,)):
    keyboard = InlineKeyboardBuilder()

    for text, url in btns.items():
        keyboard.add(InlineKeyboardButton(text=text, url=url))

    return keyboard.adjust(*sizes).as_markup()


# Создать микс из CallBack и URL кнопок
def get_inlineMix_btns(
        *,
        btns: dict[str, str],
        sizes: tuple[int] = (2,)):
    keyboard = InlineKeyboardBuilder()

    for text, value in btns.items():
        if '://' in value:
            keyboard.add(InlineKeyboardButton(text=text, url=value))
        else:
            keyboard.add(InlineKeyboardButton(text=text, callback_data=value))

    return keyboard.adjust(*sizes).as_markup()


from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# ORM (твои функции)
# from db.orm import (
#     orm_get_user_folders,
#     orm_get_folder_channels,
#     orm_get_free_channels_for_user,
#     orm_get_user_channels,
# )

def ik_create_root_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Каналы", callback_data=CreatePostCD(action="channels_menu").pack()),
            InlineKeyboardButton(text="Папки", callback_data=CreatePostCD(action="folders_menu").pack()),
        ],
        [
            InlineKeyboardButton(text="Во всех сразу", callback_data=CreatePostCD(action="all").pack()),
        ],
    ])

def ik_folders_menu(folders: list) -> InlineKeyboardMarkup:
    kb = []
    for f in folders:
        kb.append([InlineKeyboardButton(text=f"📁 {f.title}", callback_data=CreatePostCD(action="open_folder", folder_id=int(f.id)).pack())])

    if not folders:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕Добавить папку", callback_data=CreatePostCD(action="add_folder").pack())],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=CreatePostCD(action="back").pack())],
        ])

    kb.append([
        InlineKeyboardButton(text="Во всех сразу", callback_data=CreatePostCD(action="all").pack()),
        InlineKeyboardButton(text="➕Добавить папку", callback_data=CreatePostCD(action="add_folder").pack()),
    ])
    kb.append([
        InlineKeyboardButton(text="⬅️ Назад", callback_data=CreatePostCD(action="back").pack())
    ])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def ik_channels_menu(channels: list) -> InlineKeyboardMarkup:
    kb = []
    # список каналов (пока без toggle; позже добавим выбор)
    for ch in channels:
        kb.append([InlineKeyboardButton(text=ch.title, callback_data=CreatePostCD(action="open_channel", channel_id=int(ch.id)).pack())])

    # если каналов нет — только "Добавить канал"
    if not channels:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕Добавить канал", callback_data=CreatePostCD(action="add_channel").pack())],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=CreatePostCD(action="back").pack())],
        ])

    # если есть — плюс "Во всех сразу" и "Добавить канал"
    kb.append([
        InlineKeyboardButton(text="Во всех сразу", callback_data=CreatePostCD(action="all").pack()),
        InlineKeyboardButton(text="➕Добавить канал", callback_data=CreatePostCD(action="add_channel").pack()),
    ])
    kb.append([
        InlineKeyboardButton(text="⬅️ Назад", callback_data=CreatePostCD(action="back").pack())
    ])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def ik_create_post_menu(folders: list, has_free: bool = True) -> InlineKeyboardMarkup:
    kb: list[list[InlineKeyboardButton]] = []

    # Папки (по одной в ряд — аккуратнее для UX)
    for f in folders:
        kb.append([
            InlineKeyboardButton(
                text=f"📁 {f.title}",
                callback_data=CreatePostCD(action="folder", folder_id=int(f.id)).pack(),
            )
        ])

    # Нижние кнопки как в ТЗ: "Каналы" и "Во всех сразу"
    row: list[InlineKeyboardButton] = []
    if has_free:
        row.append(
            InlineKeyboardButton(text="Каналы", callback_data=CreatePostCD(action="free").pack())
        )
    row.append(
        InlineKeyboardButton(text="Во всех сразу", callback_data=CreatePostCD(action="all").pack())
    )
    kb.append(row)

    return InlineKeyboardMarkup(inline_keyboard=kb)


def ik_channels_picker(
    *,
    channels: list,
    selected_channel_ids: set[int],
    title: str,
    folder_id: int = 0,
    include_back: bool = True,
) -> InlineKeyboardMarkup:
    kb: list[list[InlineKeyboardButton]] = []

    # Каналы (тоггл)
    for ch in channels:
        ch_id = int(ch.id)
        mark = "✅" if ch_id in selected_channel_ids else "☑️"
        text = f"{mark} {ch.title}"
        kb.append([
            InlineKeyboardButton(
                text=text,
                callback_data=CreatePostCD(action="toggle", folder_id=folder_id, channel_id=ch_id).pack(),
            )
        ])

    # Управление
    kb.append([
        InlineKeyboardButton(text="Готово", callback_data=CreatePostCD(action="done", folder_id=folder_id).pack())
    ])
    if include_back:
        kb.append([
            InlineKeyboardButton(text="⬅️ Назад", callback_data=CreatePostCD(action="back").pack())
        ])

    return InlineKeyboardMarkup(inline_keyboard=kb)


def ik_after_channel_connected() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Создать пост", callback_data=CreatePostCD(action="menu").pack())],
        [InlineKeyboardButton(text="Добавить еще канал", callback_data=CreatePostCD(action="add_channel").pack())],
    ])

def ik_folders_list(folders: list) -> InlineKeyboardMarkup:
    kb = []
    for f in folders:
        kb.append([
            InlineKeyboardButton(
                text=f"📁 {f.title}",
                callback_data=CreatePostCD(action="open_folder", folder_id=int(f.id)).pack(),
            )
        ])
    # назад (ты говорил, что уже добавил — оставляю тут как эталон)
    kb.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=CreatePostCD(action="back").pack())])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def ik_folders_empty() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=CreatePostCD(action="back").pack())]
    ])

def ik_folder_channels(folder_id: int, channels: list) -> InlineKeyboardMarkup:
    kb = []

    for ch in channels:
        kb.append([
            InlineKeyboardButton(
                text=ch.title,
                callback_data=CreatePostCD(action="pick_folder_channel", folder_id=folder_id, channel_id=int(ch.id)).pack(),
            )
        ])

    # "Во всех сразу" — во все каналы папки
    if channels:
        kb.append([
            InlineKeyboardButton(
                text="Во всех сразу",
                callback_data=CreatePostCD(action="pick_folder_all", folder_id=folder_id).pack(),
            )
        ])

    kb.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=CreatePostCD(action="folders_menu").pack())])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def ik_edit_text_controls(post_id: int, *, can_delete_text: bool) -> InlineKeyboardMarkup:
    row = [InlineKeyboardButton(text="⬅️ Назад", callback_data=EditTextCD(action="back", post_id=post_id).pack())]
    if can_delete_text:
        row.insert(0, InlineKeyboardButton(text="Удалить текст", callback_data=EditTextCD(action="delete", post_id=post_id).pack()))
    return InlineKeyboardMarkup(inline_keyboard=[row])

def ik_attach_media_controls(post_id: int) -> InlineKeyboardMarkup:
    """Клавиатура для режима ожидания медиа (кнопка отмены)."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Отмена", callback_data=EditTextCD(action="cancel_attach", post_id=post_id).pack())]
    ])