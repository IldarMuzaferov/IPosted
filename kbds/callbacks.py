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

