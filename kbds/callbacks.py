from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.state import StatesGroup, State


class CreatePostStates(StatesGroup):
    choosing_channels = State()
    composing = State()  # следующий этап: принимаем контент поста
    # дальше добавим: editing_settings, choosing_time, confirming

class ConnectChannelStates(StatesGroup):
    waiting_channel = State()

class EditTextStates(StatesGroup):
    waiting_new_text = State()

class AttachMediaStates(StatesGroup):
    waiting_media = State()

class UrlButtonsStates(StatesGroup):
    waiting_buttons = State()

class PublishStates(StatesGroup):
    choosing_send_mode = State()     # "Выложить сразу" / "Отложить"
    waiting_datetime = State()       # ждём ввод "18:01 16.8.2020"
    choosing_delete_after = State()  # выбор таймера удаления
    confirming = State()             # "Да" / "Нет"

class HiddenPartStates(StatesGroup):
    waiting_button_name = State()
    waiting_subscriber_text = State()
    waiting_nonsubscriber_text = State()
    editing_button_name = State()
    editing_subscriber_text = State()
    editing_nonsubscriber_text = State()

class PublishCD(CallbackData, prefix="pub"):
    action: str          # now | later | del | confirm_yes | confirm_no
    post_id: int = 0
    value: str = ""      # для del: "1h", "6h", ... "none"


class NavCD(CallbackData, prefix="nav"):
    action: str

class ReplyPostStates(StatesGroup):
    """Состояния для настройки ответного поста."""
    waiting_forward = State()         # Ожидание пересланного сообщения
    choosing_from_plan = State()

class CreatePostCD(CallbackData, prefix="cp"):
    """
    action:
      - menu: открыть меню создания
      - folder: открыть папку
      - free: открыть "Каналы" (без папки)
      - all: выбрать "Во всех сразу"
      - toggle: переключить канал (выбор/снятие)
      - done: подтвердить выбор каналов
      - back: назад в меню
    """
    action: str
    folder_id: int = 0
    channel_id: int = 0

#======================================================================================
class SettingsStates(StatesGroup):
    waiting_channel_from_settings = State()
    waiting_folder_name = State()
    waiting_folder_rename = State()
    choosing_folder_channels = State()

class SettingsCD(CallbackData, prefix="settings"):
    """Главное меню настроек."""
    action: str  # main | add_channel | timezone | folders | back


class TimezoneCD(CallbackData, prefix="tz"):
    """Выбор часового пояса."""
    action: str  # select | back
    tz: str = ""  # IANA timezone name


class FoldersCD(CallbackData, prefix="folders"):
    """Управление папками."""
    action: str  # list | create | select | back
    folder_id: int = 0


class FolderEditCD(CallbackData, prefix="folder_edit"):
    """Редактирование папки."""
    action: str  # rename | channels | delete | back | save
    folder_id: int = 0


class FolderChannelsCD(CallbackData, prefix="folder_ch"):
    """Выбор каналов для папки."""
    action: str  # toggle | select_all | deselect_all | done | back
    folder_id: int = 0
    channel_id: int = 0

TIMEZONES = [
    ("Europe/Moscow", "Москва", "GMT+3", 3),
    ("Europe/London", "Лондон", "GMT+0", 0),
    ("Europe/Paris", "Париж", "GMT+1", 1),
    ("Europe/Berlin", "Берлин", "GMT+1", 1),
    ("Europe/Kiev", "Киев", "GMT+2", 2),
    ("Europe/Istanbul", "Стамбул", "GMT+3", 3),
    ("Asia/Dubai", "Дубай", "GMT+4", 4),
    ("Asia/Tashkent", "Ташкент", "GMT+5", 5),
    ("Asia/Almaty", "Алматы", "GMT+6", 6),
    ("Asia/Bangkok", "Бангкок", "GMT+7", 7),
    ("Asia/Shanghai", "Шанхай", "GMT+8", 8),
    ("Asia/Tokyo", "Токио", "GMT+9", 9),
    ("Australia/Sydney", "Сидней", "GMT+10", 10),
    ("Pacific/Auckland", "Окленд", "GMT+12", 12),
    ("America/New_York", "Нью-Йорк", "GMT-5", -5),
    ("America/Chicago", "Чикаго", "GMT-6", -6),
    ("America/Denver", "Денвер", "GMT-7", -7),
    ("America/Los_Angeles", "Лос-Анджелес", "GMT-8", -8),
    ("America/Anchorage", "Аляска", "GMT-9", -9),
    ("Pacific/Honolulu", "Гавайи", "GMT-10", -10),
]
