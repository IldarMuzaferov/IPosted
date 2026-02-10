from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from kbds.callbacks import CreatePostCD, PublishCD, NavCD, TIMEZONES, SettingsCD, TimezoneCD, FolderChannelsCD, \
    FolderEditCD, FoldersCD, ContentPlanCD, ContentPlanCalendarCD, ContentPlanDayCD, format_date_short, \
    ContentPlanPostCD, MONTH_NAMES, WEEKDAY_NAMES, format_date_medium, EditPostCD, EditTimerCD, EditPublishCD
from kbds.post_editor import EditTextCD
from datetime import datetime, timezone, timedelta, date
from zoneinfo import ZoneInfo
import calendar

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
        [InlineKeyboardButton(text="Добавить еще канал", callback_data=CreatePostCD(action="add_channel").pack())], #add_channel
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

def ik_send_mode(post_id: int, channel_title: str, channel_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="Выложить сразу",
                callback_data=PublishCD(action="now", post_id=post_id).pack()
            ),
            InlineKeyboardButton(
                text="Отложить",
                callback_data=PublishCD(action="later", post_id=post_id).pack()
            ),
        ]
    ])

def ik_delete_after(post_id: int) -> InlineKeyboardMarkup:
    options = [
        ("1час", "1h"),
        ("6 часов", "6h"),
        ("12 часов", "12h"),
        ("24 часов", "24h"),
        ("48 часов", "48h"),
        ("3 дня", "3d"),
        ("7 дней", "7d"),
        ("Не нужно", "none"),
    ]
    rows = []
    # по 2 кнопки в ряд
    for i in range(0, len(options), 2):
        row = []
        for text, val in options[i:i+2]:
            row.append(InlineKeyboardButton(
                text=text,
                callback_data=PublishCD(action="del", post_id=post_id, value=val).pack()
            ))
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)

def ik_confirm_publish(post_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Да", callback_data=PublishCD(action="confirm_yes", post_id=post_id).pack()),
            InlineKeyboardButton(text="Нет", callback_data=PublishCD(action="confirm_no", post_id=post_id).pack()),
        ]
    ])

def ik_finish_nav() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Контент план", callback_data="finish:content_plan"),
            InlineKeyboardButton(text="Создать", callback_data="finish:create"),
        ]
    ])

def get_current_time_in_tz(utc_offset_hours: int) -> str:
    utc_now = datetime.now(timezone.utc)
    tz = timezone(timedelta(hours=utc_offset_hours))
    local_time = utc_now.astimezone(tz)
    return local_time.strftime("%H:%M")


def get_tz_display_name(tz_name: str) -> str:
    """Получает отображаемое имя часового пояса с текущим временем."""
    for tz, name, gmt, offset in TIMEZONES:
        if tz == tz_name:
            time_str = get_current_time_in_tz(offset)
            return f"{name} ({time_str})"
    # Default
    time_str = get_current_time_in_tz(3)
    return f"Москва ({time_str})"


def build_settings_main_kb(user_timezone: str = "Europe/Moscow") -> InlineKeyboardMarkup:
    """Главное меню настроек."""
    tz_display = get_tz_display_name(user_timezone)

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="➕ Добавить новый канал",
            callback_data=SettingsCD(action="add_channel").pack()
        )],
        [InlineKeyboardButton(
            text=f"🕐 Часовой пояс: {tz_display}",
            callback_data=SettingsCD(action="timezone").pack()
        )],
        [InlineKeyboardButton(
            text="📁 Папки",
            callback_data=SettingsCD(action="folders").pack()
        )],
        # [InlineKeyboardButton(
        #     text="⬅️ Назад",
        #     callback_data=SettingsCD(action="back").pack()
        # )],
    ])


def build_timezone_kb(current_tz: str = "Europe/Moscow") -> InlineKeyboardMarkup:
    """Клавиатура выбора часового пояса."""
    kb = []

    for tz_name, city_name, gmt, offset in TIMEZONES:
        time_str = get_current_time_in_tz(offset)

        # Отмечаем текущий часовой пояс
        if tz_name == current_tz:
            text = f"✅ {city_name} ({time_str})"
        else:
            text = f"{city_name} ({time_str})"

        kb.append([InlineKeyboardButton(
            text=text,
            callback_data=TimezoneCD(action="select", tz=tz_name).pack()
        )])

    kb.append([InlineKeyboardButton(
        text="⬅️ Назад",
        callback_data=TimezoneCD(action="back").pack()
    )])

    return InlineKeyboardMarkup(inline_keyboard=kb)


def build_folders_list_kb(folders: list) -> InlineKeyboardMarkup:
    """Список папок пользователя."""
    kb = []

    for folder in folders:
        kb.append([InlineKeyboardButton(
            text=f"📁 {folder.title}",
            callback_data=FoldersCD(action="select", folder_id=folder.id).pack()
        )])

    kb.append([InlineKeyboardButton(
        text="➕ Создать папку",
        callback_data=FoldersCD(action="create").pack()
    )])

    kb.append([InlineKeyboardButton(
        text="⬅️ Назад",
        callback_data=FoldersCD(action="back").pack()
    )])

    return InlineKeyboardMarkup(inline_keyboard=kb)


def build_folder_edit_kb(folder_id: int, channels_count: int) -> InlineKeyboardMarkup:
    """Клавиатура редактирования папки."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="✏️ Сменить название",
            callback_data=FolderEditCD(action="rename", folder_id=folder_id).pack()
        )],
        [InlineKeyboardButton(
            text=f"📺 Каналы: {channels_count} шт",
            callback_data=FolderEditCD(action="channels", folder_id=folder_id).pack()
        )],
        [InlineKeyboardButton(
            text="🗑 Удалить",
            callback_data=FolderEditCD(action="delete", folder_id=folder_id).pack()
        )],
        [InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=FolderEditCD(action="back", folder_id=folder_id).pack()
        )],
    ])


def build_folder_channels_kb(
        folder_id: int,
        available_channels: list,
        selected_ids: set[int],
        folder_channels: list,
) -> InlineKeyboardMarkup:
    """Клавиатура выбора каналов для папки."""
    kb = []

    # Каналы уже в папке
    for ch in folder_channels:
        ch_id = int(ch.id)
        mark = "✅" if ch_id in selected_ids else "⬜"
        kb.append([InlineKeyboardButton(
            text=f"{mark} {ch.title}",
            callback_data=FolderChannelsCD(
                action="toggle", folder_id=folder_id, channel_id=ch_id
            ).pack()
        )])

    # Свободные каналы
    for ch in available_channels:
        ch_id = int(ch.id)
        if ch_id not in {int(fc.id) for fc in folder_channels}:
            mark = "✅" if ch_id in selected_ids else "⬜"
            kb.append([InlineKeyboardButton(
                text=f"{mark} {ch.title}",
                callback_data=FolderChannelsCD(
                    action="toggle", folder_id=folder_id, channel_id=ch_id
                ).pack()
            )])

    # Кнопки управления
    kb.append([
        InlineKeyboardButton(
            text="✅ Выбрать все",
            callback_data=FolderChannelsCD(action="select_all", folder_id=folder_id).pack()
        ),
        InlineKeyboardButton(
            text="☑️ Снять все",
            callback_data=FolderChannelsCD(action="deselect_all", folder_id=folder_id).pack()
        ),
    ])

    kb.append([
        InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=FolderChannelsCD(action="back", folder_id=folder_id).pack()
        ),
        InlineKeyboardButton(
            text="✅ Готово",
            callback_data=FolderChannelsCD(action="done", folder_id=folder_id).pack()
        ),
    ])

    return InlineKeyboardMarkup(inline_keyboard=kb)


def build_folder_create_channels_kb(
        available_channels: list,
        selected_ids: set[int],
) -> InlineKeyboardMarkup:
    """Клавиатура выбора каналов при создании папки."""
    kb = []

    for ch in available_channels:
        ch_id = int(ch.id)
        mark = "✅" if ch_id in selected_ids else "⬜"
        kb.append([InlineKeyboardButton(
            text=f"{mark} {ch.title}",
            callback_data=FolderChannelsCD(
                action="toggle", folder_id=0, channel_id=ch_id
            ).pack()
        )])

    kb.append([
        InlineKeyboardButton(
            text="✅ Выбрать все",
            callback_data=FolderChannelsCD(action="select_all", folder_id=0).pack()
        ),
        InlineKeyboardButton(
            text="☑️ Снять все",
            callback_data=FolderChannelsCD(action="deselect_all", folder_id=0).pack()
        ),
    ])

    kb.append([
        InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=FolderChannelsCD(action="back", folder_id=0).pack()
        ),
        InlineKeyboardButton(
            text="✅ Готово",
            callback_data=FolderChannelsCD(action="done", folder_id=0).pack()
        ),
    ])

    return InlineKeyboardMarkup(inline_keyboard=kb)


def build_back_to_settings_kb() -> InlineKeyboardMarkup:
    """Кнопка возврата в настройки (после добавления канала)."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="⬅️ Назад в настройки",
            callback_data=SettingsCD(action="main").pack()
        )],
    ])

#========================================================================================
#Content plan

def build_content_plan_main_kb(folders: list, has_no_folder_channels: bool) -> InlineKeyboardMarkup:
    """
    Главное меню: выбор папки или каналов.
    """
    kb = []

    # Папки
    for folder in folders:
        kb.append([InlineKeyboardButton(
            text=f"📁 {folder.title}",
            callback_data=ContentPlanCD(action="folder", folder_id=folder.id).pack()
        )])

    # Каналы без папок
    if has_no_folder_channels:
        kb.append([InlineKeyboardButton(
            text="📺 Каналы без папок",
            callback_data=ContentPlanCD(action="no_folder").pack()
        )])

    # Все каналы
    kb.append([InlineKeyboardButton(
        text="📋 Все",
        callback_data=ContentPlanCD(action="all").pack()
    )])

    kb.append([InlineKeyboardButton(
        text="⬅️ Назад",
        callback_data=ContentPlanCD(action="back").pack()
    )])

    return InlineKeyboardMarkup(inline_keyboard=kb)


def build_content_plan_channels_kb(channels: list, folder_id: int = 0) -> InlineKeyboardMarkup:
    """
    Список каналов для выбора.
    """
    kb = []

    for ch in channels:
        kb.append([InlineKeyboardButton(
            text=f"📺 {ch.title}",
            callback_data=ContentPlanCD(action="channel", channel_id=ch.id).pack()
        )])

    # Все каналы из этой папки
    kb.append([InlineKeyboardButton(
        text="📋 Все",
        callback_data=ContentPlanCD(action="all", folder_id=folder_id).pack()
    )])

    kb.append([InlineKeyboardButton(
        text="⬅️ Назад",
        callback_data=ContentPlanCD(action="main").pack()
    )])

    return InlineKeyboardMarkup(inline_keyboard=kb)


def build_content_plan_day_kb(
        targets: list,
        current_date: date,
        utc_offset: int = 3,
) -> InlineKeyboardMarkup:
    """
    Клавиатура для просмотра постов на день.
    - Кнопки времени постов
    - Пагинация по дням
    - Кнопка календаря
    """
    kb = []

    # Кнопки времени постов (в ряд по 3)
    time_buttons = []
    for t in targets:
        post_time = t.publish_at or t.sent_at
        if post_time:
            # Конвертируем UTC в локальное время
            local_time = post_time + timedelta(hours=utc_offset)
            time_str = local_time.strftime("%H:%M")

            # Иконка статуса
            if t.state.value == "sent":
                icon = "✅"
            elif t.state.value == "scheduled":
                icon = "⏰"
            else:
                icon = "📝"

            time_buttons.append(InlineKeyboardButton(
                text=f"{icon} {time_str}",
                callback_data=ContentPlanPostCD(action="view", target_id=t.id).pack()
            ))

    # Группируем по 3 в ряд
    for i in range(0, len(time_buttons), 3):
        kb.append(time_buttons[i:i + 3])

    # Пагинация по дням
    prev_date = current_date - timedelta(days=1)
    next_date = current_date + timedelta(days=1)

    kb.append([
        InlineKeyboardButton(
            text=f"← {format_date_short(prev_date)}",
            callback_data=ContentPlanDayCD(
                action="view",
                year=prev_date.year,
                month=prev_date.month,
                day=prev_date.day
            ).pack()
        ),
        InlineKeyboardButton(
            text=format_date_short(current_date),
            callback_data=ContentPlanDayCD(
                action="view",
                year=current_date.year,
                month=current_date.month,
                day=current_date.day
            ).pack()
        ),
        InlineKeyboardButton(
            text=f"{format_date_short(next_date)} →",
            callback_data=ContentPlanDayCD(
                action="view",
                year=next_date.year,
                month=next_date.month,
                day=next_date.day
            ).pack()
        ),
    ])

    # Кнопка календаря
    kb.append([InlineKeyboardButton(
        text="📅 Развернуть календарь",
        callback_data=ContentPlanCalendarCD(
            action="back",
            year=current_date.year,
            month=current_date.month,
            day=current_date.day
        ).pack()
    )])

    # Назад
    kb.append([InlineKeyboardButton(
        text="⬅️ Назад",
        callback_data=ContentPlanCD(action="main").pack()
    )])

    return InlineKeyboardMarkup(inline_keyboard=kb)


def build_content_plan_calendar_kb(
        targets: list,
        year: int,
        month: int,
        days_with_posts: dict[int, int],
        utc_offset: int = 3,
) -> InlineKeyboardMarkup:
    """
    Клавиатура календаря.
    - Кнопки времени постов (для текущего выбранного дня)
    - Пагинация по месяцам
    - Календарь с отметками
    """
    kb = []

    # Кнопки времени постов (в ряд по 3)
    time_buttons = []
    for t in targets:
        post_time = t.publish_at or t.sent_at
        if post_time:
            local_time = post_time + timedelta(hours=utc_offset)
            time_str = local_time.strftime("%H:%M")

            if t.state.value == "sent":
                icon = "✅"
            elif t.state.value == "scheduled":
                icon = "⏰"
            else:
                icon = "📝"

            time_buttons.append(InlineKeyboardButton(
                text=f"{icon} {time_str}",
                callback_data=ContentPlanPostCD(action="view", target_id=t.id).pack()
            ))

    for i in range(0, len(time_buttons), 3):
        kb.append(time_buttons[i:i + 3])

    # Пагинация по месяцам
    if month == 1:
        prev_month, prev_year = 12, year - 1
    else:
        prev_month, prev_year = month - 1, year

    if month == 12:
        next_month, next_year = 1, year + 1
    else:
        next_month, next_year = month + 1, year

    kb.append([
        InlineKeyboardButton(
            text=f"← {MONTH_NAMES[prev_month]}",
            callback_data=ContentPlanCalendarCD(
                action="prev_month",
                year=prev_year,
                month=prev_month
            ).pack()
        ),
        InlineKeyboardButton(
            text=MONTH_NAMES[month],
            callback_data=ContentPlanCalendarCD(
                action="back",
                year=year,
                month=month
            ).pack()
        ),
        InlineKeyboardButton(
            text=f"{MONTH_NAMES[next_month]} →",
            callback_data=ContentPlanCalendarCD(
                action="next_month",
                year=next_year,
                month=next_month
            ).pack()
        ),
    ])

    # Заголовок дней недели
    kb.append([
        InlineKeyboardButton(text=day, callback_data="ignore")
        for day in WEEKDAY_NAMES
    ])

    # Календарь
    cal = calendar.Calendar(firstweekday=0)
    month_days = cal.monthdayscalendar(year, month)

    for week in month_days:
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(text=" ", callback_data="ignore"))
            else:
                # Проверяем есть ли посты в этот день
                has_posts = day in days_with_posts

                if has_posts:
                    text = f"◆{day}"  # Ромбик для дней с постами
                else:
                    text = str(day)

                row.append(InlineKeyboardButton(
                    text=text,
                    callback_data=ContentPlanCalendarCD(
                        action="select_day",
                        year=year,
                        month=month,
                        day=day
                    ).pack()
                ))
        kb.append(row)

    # Все отложенные посты
    kb.append([InlineKeyboardButton(
        text="📋 Все отложенные посты",
        callback_data=ContentPlanCalendarCD(action="all_posts", year=year, month=month).pack()
    )])

    # Назад
    kb.append([InlineKeyboardButton(
        text="⬅️ Назад",
        callback_data=ContentPlanCD(action="main").pack()
    )])

    return InlineKeyboardMarkup(inline_keyboard=kb)


def build_all_scheduled_posts_kb(dates_with_count: list[tuple[date, int]]) -> InlineKeyboardMarkup:
    """
    Клавиатура со всеми датами, где есть запланированные посты.
    """
    kb = []

    # Группируем по 2 в ряд
    buttons = []
    for dt, count in dates_with_count:
        posts_word = "пост" if count == 1 else ("поста" if 2 <= count <= 4 else "постов")
        text = f"{format_date_medium(dt)}, {count} {posts_word}"
        buttons.append(InlineKeyboardButton(
            text=text,
            callback_data=ContentPlanDayCD(
                action="view",
                year=dt.year,
                month=dt.month,
                day=dt.day
            ).pack()
        ))

    for i in range(0, len(buttons), 2):
        kb.append(buttons[i:i + 2])

    # Назад
    kb.append([InlineKeyboardButton(
        text="⬅️ Назад",
        callback_data=ContentPlanCalendarCD(
            action="back",
            year=datetime.now().year,
            month=datetime.now().month
        ).pack()
    )])

    return InlineKeyboardMarkup(inline_keyboard=kb)

def build_post_view_kb(target_id: int) -> InlineKeyboardMarkup:
    """
    Клавиатура просмотра поста.
    """
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="📋 Дублировать",
                callback_data=ContentPlanPostCD(action="duplicate", target_id=target_id).pack()
            ),
            InlineKeyboardButton(
                text="✏️ Изменить",
                callback_data=ContentPlanPostCD(action="edit", target_id=target_id).pack()
            ),
        ],
        [InlineKeyboardButton(
            text="🗑 Удалить",
            callback_data=ContentPlanPostCD(action="delete", target_id=target_id).pack()
        )],
        [InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=ContentPlanPostCD(action="back", target_id=target_id).pack()
        )],
    ])


def build_delete_confirm_kb(target_id: int) -> InlineKeyboardMarkup:
    """Подтверждение удаления."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Да, удалить",
                callback_data=ContentPlanPostCD(action="delete_confirm", target_id=target_id).pack()
            ),
            InlineKeyboardButton(
                text="❌ Отмена",
                callback_data=ContentPlanPostCD(action="view", target_id=target_id).pack()
            ),
        ],
    ])

def build_no_posts_kb() -> InlineKeyboardMarkup:
    """Клавиатура когда нет постов."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=ContentPlanCD(action="main").pack()
        )],
    ])


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
    """Форматирует таймер для отображения."""
    if minutes == 0:
        return "Не нужно"
    if minutes < 60:
        return f"{minutes} мин"
    if minutes < 1440:
        hours = minutes // 60
        return f"{hours} ч"
    days = minutes // 1440
    return f"{days} дн"


def build_edit_post_cancel_kb() -> InlineKeyboardMarkup:
    """Кнопка отмены при ожидании пересланного поста."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="❌ Отменить",
            callback_data=EditPostCD(action="cancel").pack()
        )],
    ])


def build_edit_post_editor_kb(
        target_id: int,
        timer_minutes: int = 0,
        publish_now: bool = True,
        publish_time: datetime | None = None,
        # Стандартные настройки поста
        bell: bool = True,
        reactions: bool = False,
        content_protect: bool = False,
        pin: bool = False,
        comments: bool = False,
) -> InlineKeyboardMarkup:
    """
    Клавиатура редактирования существующего поста.
    Включает стандартные кнопки + Таймер удаления + Время публикации.
    """
    kb = []

    # Ряд 1: Уведомление и реакции
    kb.append([
        InlineKeyboardButton(
            text=f"{'🔔' if bell else '🔕'} Уведомление",
            callback_data=f"editpost_toggle:bell:{target_id}"
        ),
        InlineKeyboardButton(
            text=f"{'❤️' if reactions else '🤍'} Реакции",
            callback_data=f"editpost_toggle:reactions:{target_id}"
        ),
    ])

    # Ряд 2: Защита и закрепление
    kb.append([
        InlineKeyboardButton(
            text=f"{'🔒' if content_protect else '🔓'} Защита",
            callback_data=f"editpost_toggle:protect:{target_id}"
        ),
        InlineKeyboardButton(
            text=f"{'📌' if pin else '📍'} Закрепить",
            callback_data=f"editpost_toggle:pin:{target_id}"
        ),
    ])

    # Ряд 3: Комментарии
    kb.append([
        InlineKeyboardButton(
            text=f"{'💬' if comments else '🚫'} Комментарии",
            callback_data=f"editpost_toggle:comments:{target_id}"
        ),
    ])

    # Ряд 4: Таймер удаления
    timer_text = f"⏱ Таймер удаления: {format_timer(timer_minutes)}"
    kb.append([InlineKeyboardButton(
        text=timer_text,
        callback_data=EditPostCD(action="timer", target_id=target_id).pack()
    )])

    # Ряд 5: Время публикации
    if publish_now:
        publish_text = "🚀 Выложить сразу"
    elif publish_time:
        publish_text = f"📅 {publish_time.strftime('%d.%m.%Y %H:%M')}"
    else:
        publish_text = "📅 Выбрать время"

    kb.append([InlineKeyboardButton(
        text=publish_text,
        callback_data=EditPostCD(action="publish_time", target_id=target_id).pack()
    )])

    # Ряд 6: Продолжить
    kb.append([InlineKeyboardButton(
        text="✅ Сохранить изменения",
        callback_data=EditPostCD(action="continue", target_id=target_id).pack()
    )])

    return InlineKeyboardMarkup(inline_keyboard=kb)


def build_timer_select_kb(current_minutes: int = 0) -> InlineKeyboardMarkup:
    """Клавиатура выбора таймера удаления."""
    kb = []

    for minutes, label in TIMER_OPTIONS:
        if minutes == current_minutes:
            text = f"✅ {label}"
        else:
            text = label

        kb.append([InlineKeyboardButton(
            text=text,
            callback_data=EditTimerCD(action="select", minutes=minutes).pack()
        )])

    kb.append([InlineKeyboardButton(
        text="⬅️ Назад",
        callback_data=EditTimerCD(action="back").pack()
    )])

    return InlineKeyboardMarkup(inline_keyboard=kb)


def build_publish_time_kb() -> InlineKeyboardMarkup:
    """Клавиатура выбора времени публикации."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🚀 Выложить сразу",
            callback_data=EditPublishCD(action="now").pack()
        )],
        [InlineKeyboardButton(
            text="📅 Запланировать",
            callback_data=EditPublishCD(action="schedule").pack()
        )],
        [InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=EditPublishCD(action="back").pack()
        )],
    ])


def build_date_picker_kb(year: int, month: int, selected_day: int = 0) -> InlineKeyboardMarkup:
    """Клавиатура выбора даты."""
    import calendar

    MONTH_NAMES = {
        1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
        5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
        9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь"
    }

    WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

    kb = []

    # Навигация по месяцам
    if month == 1:
        prev_m, prev_y = 12, year - 1
    else:
        prev_m, prev_y = month - 1, year

    if month == 12:
        next_m, next_y = 1, year + 1
    else:
        next_m, next_y = month + 1, year

    kb.append([
        InlineKeyboardButton(
            text="◀️",
            callback_data=EditPublishCD(action="set_date", year=prev_y, month=prev_m).pack()
        ),
        InlineKeyboardButton(
            text=f"{MONTH_NAMES[month]} {year}",
            callback_data="ignore"
        ),
        InlineKeyboardButton(
            text="▶️",
            callback_data=EditPublishCD(action="set_date", year=next_y, month=next_m).pack()
        ),
    ])

    # Заголовок дней недели
    kb.append([
        InlineKeyboardButton(text=day, callback_data="ignore")
        for day in WEEKDAYS
    ])

    # Дни месяца
    cal = calendar.Calendar(firstweekday=0)
    today = datetime.now().date()

    for week in cal.monthdayscalendar(year, month):
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(text=" ", callback_data="ignore"))
            else:
                # Проверяем что дата не в прошлом
                check_date = datetime(year, month, day).date()
                if check_date < today:
                    row.append(InlineKeyboardButton(text="·", callback_data="ignore"))
                elif day == selected_day:
                    row.append(InlineKeyboardButton(
                        text=f"[{day}]",
                        callback_data=EditPublishCD(action="set_date", year=year, month=month, day=day).pack()
                    ))
                else:
                    row.append(InlineKeyboardButton(
                        text=str(day),
                        callback_data=EditPublishCD(action="set_date", year=year, month=month, day=day).pack()
                    ))
        kb.append(row)

    kb.append([InlineKeyboardButton(
        text="⬅️ Назад",
        callback_data=EditPublishCD(action="back").pack()
    )])

    return InlineKeyboardMarkup(inline_keyboard=kb)


def build_time_picker_kb(selected_hour: int = 12, selected_minute: int = 0) -> InlineKeyboardMarkup:
    """Клавиатура выбора времени."""
    kb = []

    # Часы (по 6 в ряд)
    kb.append([InlineKeyboardButton(text="Выберите час:", callback_data="ignore")])

    hours_row1 = []
    hours_row2 = []
    hours_row3 = []
    hours_row4 = []

    for h in range(0, 6):
        mark = "✓" if h == selected_hour else ""
        hours_row1.append(InlineKeyboardButton(
            text=f"{mark}{h:02d}",
            callback_data=EditPublishCD(action="set_time", hour=h, minute=selected_minute).pack()
        ))

    for h in range(6, 12):
        mark = "✓" if h == selected_hour else ""
        hours_row2.append(InlineKeyboardButton(
            text=f"{mark}{h:02d}",
            callback_data=EditPublishCD(action="set_time", hour=h, minute=selected_minute).pack()
        ))

    for h in range(12, 18):
        mark = "✓" if h == selected_hour else ""
        hours_row3.append(InlineKeyboardButton(
            text=f"{mark}{h:02d}",
            callback_data=EditPublishCD(action="set_time", hour=h, minute=selected_minute).pack()
        ))

    for h in range(18, 24):
        mark = "✓" if h == selected_hour else ""
        hours_row4.append(InlineKeyboardButton(
            text=f"{mark}{h:02d}",
            callback_data=EditPublishCD(action="set_time", hour=h, minute=selected_minute).pack()
        ))

    kb.append(hours_row1)
    kb.append(hours_row2)
    kb.append(hours_row3)
    kb.append(hours_row4)

    # Минуты (по 6 в ряд: 00, 10, 20, 30, 40, 50)
    kb.append([InlineKeyboardButton(text="Выберите минуты:", callback_data="ignore")])

    minutes_row = []
    for m in [0, 10, 20, 30, 40, 50]:
        mark = "✓" if m == selected_minute else ""
        minutes_row.append(InlineKeyboardButton(
            text=f"{mark}{m:02d}",
            callback_data=EditPublishCD(action="set_time", hour=selected_hour, minute=m).pack()
        ))
    kb.append(minutes_row)

    # Подтвердить
    kb.append([InlineKeyboardButton(
        text=f"✅ Подтвердить {selected_hour:02d}:{selected_minute:02d}",
        callback_data=EditPublishCD(action="confirm", hour=selected_hour, minute=selected_minute).pack()
    )])

    kb.append([InlineKeyboardButton(
        text="⬅️ Назад",
        callback_data=EditPublishCD(action="back").pack()
    )])

    return InlineKeyboardMarkup(inline_keyboard=kb)


def build_confirm_kb(target_id: int) -> InlineKeyboardMarkup:
    """Клавиатура подтверждения сохранения."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Да",
                callback_data=EditPostCD(action="confirm_yes", target_id=target_id).pack()
            ),
            InlineKeyboardButton(
                text="❌ Нет",
                callback_data=EditPostCD(action="confirm_no", target_id=target_id).pack()
            ),
        ],
    ])

def build_back_to_edit_kb() -> InlineKeyboardMarkup:
    """Кнопка назад при вводе времени."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=EditPublishCD(action="back").pack()
        )],
    ])

