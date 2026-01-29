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
