from aiogram import Router, F, types
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy.ext.asyncio import AsyncSession

from filters.chat_types import ChatTypeFilter
from kbds.inline import (
    SettingsCD, TimezoneCD, FoldersCD, FolderEditCD, FolderChannelsCD,
    build_settings_main_kb, build_timezone_kb, build_folders_list_kb,
    build_folder_edit_kb, build_folder_channels_kb, build_folder_create_channels_kb,
    build_back_to_settings_kb, TIMEZONES, get_tz_display_name,
)
from kbds.callbacks import SettingsStates
from database.orm_query import (
    orm_get_user, orm_update_user_timezone,
    orm_get_user_folders, orm_create_folder, orm_rename_folder, orm_delete_folder,
    orm_get_folder_channels, orm_add_channel_to_folder, orm_remove_channel_from_folder,
    orm_get_channels_without_folder, orm_upsert_channel, orm_add_channel_admin,
)
from kbds.inline import ik_create_root_menu

settings_router = Router()
settings_router.message.filter(ChatTypeFilter(["private"]))

SETTINGS_MAIN_TEXT = (
    "⚙️ <b>НАСТРОЙКИ</b>\n\n"
    "В этом разделе вы можете настроить работу с ботом, "
    "с отдельным каналом, а также добавить новый канал в Posted."
)

TIMEZONE_TEXT = (
    "🕐 <b>ЧАСОВОЙ ПОЯС</b>\n\n"
    "Выберите часовой пояс. Время выхода постов будет "
    "отображаться в вашем часовом поясе.\n\n"
    "Ваш часовой пояс: <b>{tz_name}</b>"
)

FOLDERS_TEXT = (
    "📁 <b>ПАПКИ</b>\n\n"
    "Группируйте каналы, объединяя их в папки."
)

FOLDER_EDIT_TEXT = (
    "📁 <b>ПАПКА «{title}»</b>\n\n"
    "В этом разделе можно настроить папку."
)

FOLDER_CHANNELS_TEXT = (
    "Выберите каналы, которые хотите добавить в папку."
)

FOLDER_NAME_PROMPT = (
    "Введите название папки:"
)

ADD_CHANNEL_FROM_SETTINGS_TEXT = (
    "➕ <b>ДОБАВЛЕНИЕ КАНАЛА</b>\n\n"
    "Чтобы подключить канал:\n\n"
    "1. Сделайте @IPostedBot администратором канала с правами:\n"
    "   ✅ Отправка сообщений\n"
    "   ✅ Удаление сообщений\n"
    "   ✅ Редактирование сообщений\n\n"
    "2. Перешлите в этот диалог любое сообщение из канала."
)


# =============================================================================
# ГЛАВНОЕ МЕНЮ НАСТРОЕК
# =============================================================================

@settings_router.callback_query(SettingsCD.filter(F.action == "main"))
async def settings_main(call: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    """Открыть главное меню настроек."""
    user = await orm_get_user(session, user_id=call.from_user.id)
    user_tz = user.timezone if user else "Europe/Moscow"

    await call.message.edit_text(
        SETTINGS_MAIN_TEXT,
        parse_mode="HTML",
        reply_markup=build_settings_main_kb(user_tz),
    )
    await call.answer()


@settings_router.callback_query(SettingsCD.filter(F.action == "back"))
async def settings_back_to_menu(call: types.CallbackQuery, state: FSMContext):
    """Возврат в главное меню бота."""
    await state.clear()
    await call.message.edit_text(
        "Выберите действие:",
        reply_markup=ik_create_root_menu(),
    )
    await call.answer()


# =============================================================================
# ДОБАВЛЕНИЕ КАНАЛА ИЗ НАСТРОЕК
# =============================================================================

@settings_router.callback_query(SettingsCD.filter(F.action == "add_channel"))
async def settings_add_channel(call: types.CallbackQuery, state: FSMContext):
    """Начать добавление канала из настроек."""
    await state.set_state(SettingsStates.waiting_channel_from_settings)

    await call.message.edit_text(
        ADD_CHANNEL_FROM_SETTINGS_TEXT,
        parse_mode="HTML",
        reply_markup=build_back_to_settings_kb(),
    )
    await call.answer()


@settings_router.message(StateFilter(SettingsStates.waiting_channel_from_settings), F.forward_from_chat)
async def settings_receive_channel(message: types.Message, state: FSMContext, session: AsyncSession):
    """Получение пересланного сообщения из канала - ИСПРАВЛЕННАЯ ВЕРСИЯ."""
    chat = message.forward_from_chat

    if not chat:
        await message.answer("❌ Перешлите сообщение из канала.")
        return

    if chat.type != "channel":
        await message.answer("❌ Это не канал. Перешлите сообщение из канала.")
        return

    # Проверяем права бота
    try:
        bot_member = await message.bot.get_chat_member(chat.id, message.bot.id)
        if bot_member.status not in ("administrator", "creator"):
            await message.answer(
                "❌ Бот не является администратором этого канала.\n\n"
                "Добавьте бота в администраторы канала и попробуйте снова."
            )
            return

        # Проверяем обязательные права (только для administrator, не creator)
        if bot_member.status == "administrator":
            can_post = getattr(bot_member, "can_post_messages", False)
            can_delete = getattr(bot_member, "can_delete_messages", False)
            can_edit = getattr(bot_member, "can_edit_messages", False)

            if not can_post:
                await message.answer(
                    "❌ Боту не выдано право на отправку сообщений.\n\n"
                    "Выдайте боту все необходимые права:\n"
                    "✅ Отправка сообщений\n"
                    "✅ Удаление сообщений\n"
                    "✅ Редактирование сообщений"
                )
                return

            if not can_delete or not can_edit:
                missing = []
                if not can_delete:
                    missing.append("удаление сообщений")
                if not can_edit:
                    missing.append("редактирование сообщений")
                await message.answer(
                    f"❌ Боту не выданы права: {', '.join(missing)}.\n\n"
                    "Выдайте боту все необходимые права и попробуйте снова."
                )
                return

    except Exception as e:
        await message.answer(f"❌ Не удалось проверить права бота в канале: {e}")
        return

    # Проверяем что пользователь - админ канала
    try:
        user_member = await message.bot.get_chat_member(chat.id, message.from_user.id)
        if user_member.status not in ("administrator", "creator"):
            await message.answer("❌ Вы не являетесь администратором этого канала.")
            return
    except Exception:
        await message.answer("❌ Не удалось проверить ваши права в канале.")
        return

    # Определяем приватность канала
    is_private = chat.username is None

    # Сохраняем канал
    await orm_upsert_channel(
        session,
        channel_id=chat.id,
        title=chat.title,
        username=chat.username,
        is_private=is_private,
    )

    await orm_add_channel_admin(
        session,
        channel_id=chat.id,
        user_id=message.from_user.id,
    )
    await session.commit()

    # Возвращаемся в настройки
    await state.clear()

    user = await orm_get_user(session, user_id=message.from_user.id)
    user_tz = user.timezone if user else "Europe/Moscow"

    SETTINGS_MAIN_TEXT = (
        "⚙️ <b>НАСТРОЙКИ</b>\n\n"
        "В этом разделе вы можете настроить работу с ботом, "
        "с отдельным каналом, а также добавить новый канал в Posted."
    )

    await message.answer(
        f"✅ Канал <b>{chat.title}</b> успешно подключен!\n\n" + SETTINGS_MAIN_TEXT,
        parse_mode="HTML",
        reply_markup=build_settings_main_kb(user_tz),
    )


# =============================================================================
# ЧАСОВОЙ ПОЯС
# =============================================================================

@settings_router.callback_query(SettingsCD.filter(F.action == "timezone"))
async def settings_timezone(call: types.CallbackQuery, session: AsyncSession):
    """Открыть выбор часового пояса."""
    user = await orm_get_user(session, user_id=call.from_user.id)
    user_tz = user.timezone if user else "Europe/Moscow"

    # Находим отображаемое имя
    tz_display = "GMT+3 Москва"
    for tz, name, gmt, offset in TIMEZONES:
        if tz == user_tz:
            tz_display = f"{gmt} {name}"
            break

    await call.message.edit_text(
        TIMEZONE_TEXT.format(tz_name=tz_display),
        parse_mode="HTML",
        reply_markup=build_timezone_kb(user_tz),
    )
    await call.answer()


@settings_router.callback_query(TimezoneCD.filter(F.action == "select"))
async def timezone_select(call: types.CallbackQuery, callback_data: TimezoneCD, session: AsyncSession):
    """Выбор часового пояса."""
    new_tz = callback_data.tz

    await orm_update_user_timezone(session, user_id=call.from_user.id, timezone=new_tz)
    await session.commit()

    # Находим отображаемое имя
    tz_display = new_tz
    for tz, name, gmt, offset in TIMEZONES:
        if tz == new_tz:
            tz_display = f"{gmt} {name}"
            break

    await call.answer(f"✅ Часовой пояс изменен: {tz_display}", show_alert=True)

    # Обновляем клавиатуру
    await call.message.edit_text(
        TIMEZONE_TEXT.format(tz_name=tz_display),
        parse_mode="HTML",
        reply_markup=build_timezone_kb(new_tz),
    )


@settings_router.callback_query(TimezoneCD.filter(F.action == "back"))
async def timezone_back(call: types.CallbackQuery, session: AsyncSession):
    """Возврат в настройки из часового пояса."""
    user = await orm_get_user(session, user_id=call.from_user.id)
    user_tz = user.timezone if user else "Europe/Moscow"

    await call.message.edit_text(
        SETTINGS_MAIN_TEXT,
        parse_mode="HTML",
        reply_markup=build_settings_main_kb(user_tz),
    )
    await call.answer()


# =============================================================================
# ПАПКИ - СПИСОК
# =============================================================================

@settings_router.callback_query(SettingsCD.filter(F.action == "folders"))
async def settings_folders(call: types.CallbackQuery, session: AsyncSession):
    """Открыть список папок."""
    folders = await orm_get_user_folders(session, user_id=call.from_user.id)

    await call.message.edit_text(
        FOLDERS_TEXT,
        parse_mode="HTML",
        reply_markup=build_folders_list_kb(folders),
    )
    await call.answer()


@settings_router.callback_query(FoldersCD.filter(F.action == "back"))
async def folders_back(call: types.CallbackQuery, session: AsyncSession):
    """Возврат в настройки из папок."""
    user = await orm_get_user(session, user_id=call.from_user.id)
    user_tz = user.timezone if user else "Europe/Moscow"

    await call.message.edit_text(
        SETTINGS_MAIN_TEXT,
        parse_mode="HTML",
        reply_markup=build_settings_main_kb(user_tz),
    )
    await call.answer()


# =============================================================================
# ПАПКИ - СОЗДАНИЕ
# =============================================================================

@settings_router.callback_query(FoldersCD.filter(F.action == "create"))
async def folder_create_start(call: types.CallbackQuery, state: FSMContext):
    """Начать создание папки."""
    await state.set_state(SettingsStates.waiting_folder_name)

    await call.message.edit_text(
        FOLDER_NAME_PROMPT,
        reply_markup=None,
    )
    await call.answer()


@settings_router.message(StateFilter(SettingsStates.waiting_folder_name), F.text)
async def folder_create_name(message: types.Message, state: FSMContext, session: AsyncSession):
    """Получение названия новой папки."""
    folder_name = message.text.strip()

    if len(folder_name) > 64:
        await message.answer("❌ Название слишком длинное (максимум 64 символа)")
        return

    if len(folder_name) < 1:
        await message.answer("❌ Введите название папки")
        return

    # Создаём папку
    folder = await orm_create_folder(session, user_id=message.from_user.id, title=folder_name)
    await session.commit()

    # Сохраняем folder_id и переходим к выбору каналов
    await state.update_data(
        new_folder_id=folder.id,
        folder_selected_channels=set(),
    )
    await state.set_state(SettingsStates.choosing_folder_channels)

    # Получаем доступные каналы
    available_channels = await orm_get_channels_without_folder(session, user_id=message.from_user.id)

    await message.answer(
        FOLDER_CHANNELS_TEXT,
        reply_markup=build_folder_create_channels_kb(available_channels, set()),
    )


# =============================================================================
# ПАПКИ - РЕДАКТИРОВАНИЕ
# =============================================================================

@settings_router.callback_query(FoldersCD.filter(F.action == "select"))
async def folder_select(call: types.CallbackQuery, callback_data: FoldersCD, session: AsyncSession):
    """Открыть редактирование папки."""
    folder_id = callback_data.folder_id
    user_id = call.from_user.id

    # Получаем папку и её каналы
    folders = await orm_get_user_folders(session, user_id=call.from_user.id)
    folder = next((f for f in folders if f.id == folder_id), None)

    if not folder:
        await call.answer("Папка не найдена", show_alert=True)
        return

    channels = await orm_get_folder_channels(session, user_id=user_id, folder_id=folder_id)
    channels_count = len(channels)

    await call.message.edit_text(
        FOLDER_EDIT_TEXT.format(title=folder.title),
        parse_mode="HTML",
        reply_markup=build_folder_edit_kb(folder_id, channels_count),
    )
    await call.answer()


@settings_router.callback_query(FolderEditCD.filter(F.action == "back"))
async def folder_edit_back(call: types.CallbackQuery, session: AsyncSession):
    """Возврат в список папок."""
    folders = await orm_get_user_folders(session, user_id=call.from_user.id)

    await call.message.edit_text(
        FOLDERS_TEXT,
        parse_mode="HTML",
        reply_markup=build_folders_list_kb(folders),
    )
    await call.answer()


# =============================================================================
# ПАПКИ - ПЕРЕИМЕНОВАНИЕ
# =============================================================================

@settings_router.callback_query(FolderEditCD.filter(F.action == "rename"))
async def folder_rename_start(call: types.CallbackQuery, callback_data: FolderEditCD, state: FSMContext):
    """Начать переименование папки."""
    await state.set_state(SettingsStates.waiting_folder_rename)
    await state.update_data(rename_folder_id=callback_data.folder_id)

    await call.message.edit_text(
        "Введите новое название папки:",
        reply_markup=None,
    )
    await call.answer()


@settings_router.message(StateFilter(SettingsStates.waiting_folder_rename), F.text)
async def folder_rename_receive(message: types.Message, state: FSMContext, session: AsyncSession):
    """Получение нового названия папки."""
    new_name = message.text.strip()

    if len(new_name) > 64:
        await message.answer("❌ Название слишком длинное (максимум 64 символа)")
        return

    data = await state.get_data()
    folder_id = data.get("rename_folder_id")


    await orm_rename_folder(session, user_id=message.from_user.id, folder_id=folder_id, new_title=new_name)
    await session.commit()

    await state.clear()

    # Показываем обновлённую папку
    channels = await orm_get_folder_channels(session, user_id=message.from_user.id, folder_id=folder_id)

    channels_count = len(channels)

    await message.answer(
        f"✅ Папка переименована!\n\n" + FOLDER_EDIT_TEXT.format(title=new_name),
        parse_mode="HTML",
        reply_markup=build_folder_edit_kb(folder_id, channels_count),
    )


# =============================================================================
# ПАПКИ - УДАЛЕНИЕ
# =============================================================================

@settings_router.callback_query(FolderEditCD.filter(F.action == "delete"))
async def folder_delete(call: types.CallbackQuery, callback_data: FolderEditCD, session: AsyncSession):
    """Удалить папку (каналы остаются)."""
    folder_id = callback_data.folder_id

    await orm_delete_folder(session, user_id=call.from_user.id, folder_id=folder_id)
    await session.commit()

    await call.answer("✅ Папка удалена", show_alert=True)

    # Возвращаемся в список папок
    folders = await orm_get_user_folders(session, user_id=call.from_user.id)

    await call.message.edit_text(
        FOLDERS_TEXT,
        parse_mode="HTML",
        reply_markup=build_folders_list_kb(folders),
    )


# =============================================================================
# ПАПКИ - КАНАЛЫ
# =============================================================================

@settings_router.callback_query(FolderEditCD.filter(F.action == "channels"))
async def folder_channels_start(call: types.CallbackQuery, callback_data: FolderEditCD, state: FSMContext,
                                session: AsyncSession):
    """Открыть выбор каналов для папки."""
    folder_id = callback_data.folder_id
    user_id = call.from_user.id


    # Получаем каналы в папке и свободные каналы
    folder_channels = await orm_get_folder_channels(session, user_id=user_id, folder_id=folder_id)

    available_channels = await orm_get_channels_without_folder(session, user_id=call.from_user.id)

    # Текущие каналы папки = выбранные по умолчанию
    selected_ids = {int(ch.id) for ch in folder_channels}

    await state.update_data(
        edit_folder_id=folder_id,
        folder_selected_channels=selected_ids,
        folder_original_channels=selected_ids.copy(),
    )
    await state.set_state(SettingsStates.choosing_folder_channels)

    await call.message.edit_text(
        FOLDER_CHANNELS_TEXT,
        reply_markup=build_folder_channels_kb(folder_id, available_channels, selected_ids, folder_channels),
    )
    await call.answer()


@settings_router.callback_query(FolderChannelsCD.filter(F.action == "toggle"),
                                StateFilter(SettingsStates.choosing_folder_channels))
async def folder_channels_toggle(call: types.CallbackQuery, callback_data: FolderChannelsCD, state: FSMContext,
                                 session: AsyncSession):
    """Переключить канал в папке."""
    data = await state.get_data()
    folder_id = data.get("edit_folder_id") or data.get("new_folder_id")
    selected_ids = set(data.get("folder_selected_channels") or [])
    channel_id = callback_data.channel_id
    user_id = call.from_user.id


    # Переключаем
    if channel_id in selected_ids:
        selected_ids.discard(channel_id)
    else:
        selected_ids.add(channel_id)

    await state.update_data(folder_selected_channels=selected_ids)

    # Обновляем клавиатуру
    if folder_id:
        folder_channels = await orm_get_folder_channels(session, user_id=user_id, folder_id=folder_id)

        available_channels = await orm_get_channels_without_folder(session, user_id=call.from_user.id)
        kb = build_folder_channels_kb(folder_id, available_channels, selected_ids, folder_channels)
    else:
        available_channels = await orm_get_channels_without_folder(session, user_id=call.from_user.id)
        kb = build_folder_create_channels_kb(available_channels, selected_ids)

    try:
        await call.message.edit_reply_markup(reply_markup=kb)
    except TelegramBadRequest:
        pass

    await call.answer()


@settings_router.callback_query(FolderChannelsCD.filter(F.action == "select_all"),
                                StateFilter(SettingsStates.choosing_folder_channels))
async def folder_channels_select_all(call: types.CallbackQuery, callback_data: FolderChannelsCD, state: FSMContext,
                                     session: AsyncSession):
    """Выбрать все каналы."""
    data = await state.get_data()
    folder_id = data.get("edit_folder_id") or data.get("new_folder_id")
    user_id = call.from_user.id

    # Получаем все доступные каналы
    available_channels = await orm_get_channels_without_folder(session, user_id=call.from_user.id)
    folder_channels = []
    if folder_id:
        folder_channels = await orm_get_folder_channels(session, user_id=user_id, folder_id=folder_id)


    # Выбираем все
    selected_ids = {int(ch.id) for ch in available_channels}
    selected_ids.update({int(ch.id) for ch in folder_channels})

    await state.update_data(folder_selected_channels=selected_ids)

    # Обновляем клавиатуру
    if folder_id:
        kb = build_folder_channels_kb(folder_id, available_channels, selected_ids, folder_channels)
    else:
        kb = build_folder_create_channels_kb(available_channels, selected_ids)

    try:
        await call.message.edit_reply_markup(reply_markup=kb)
    except TelegramBadRequest:
        pass

    await call.answer()


@settings_router.callback_query(FolderChannelsCD.filter(F.action == "deselect_all"),
                                StateFilter(SettingsStates.choosing_folder_channels))
async def folder_channels_deselect_all(call: types.CallbackQuery, callback_data: FolderChannelsCD, state: FSMContext,
                                       session: AsyncSession):
    """Снять выбор со всех каналов."""
    data = await state.get_data()
    folder_id = data.get("edit_folder_id") or data.get("new_folder_id")
    user_id = call.from_user.id

    selected_ids = set()
    await state.update_data(folder_selected_channels=selected_ids)

    # Обновляем клавиатуру
    available_channels = await orm_get_channels_without_folder(session, user_id=call.from_user.id)
    if folder_id:
        folder_channels = await orm_get_folder_channels(session, user_id=user_id, folder_id=folder_id)

        kb = build_folder_channels_kb(folder_id, available_channels, selected_ids, folder_channels)
    else:
        kb = build_folder_create_channels_kb(available_channels, selected_ids)

    try:
        await call.message.edit_reply_markup(reply_markup=kb)
    except TelegramBadRequest:
        pass

    await call.answer()


@settings_router.callback_query(FolderChannelsCD.filter(F.action == "done"),
                                StateFilter(SettingsStates.choosing_folder_channels))
async def folder_channels_done(call: types.CallbackQuery, callback_data: FolderChannelsCD, state: FSMContext,
                               session: AsyncSession):
    """Сохранить выбор каналов."""
    data = await state.get_data()
    folder_id = data.get("edit_folder_id") or data.get("new_folder_id")
    selected_ids = set(data.get("folder_selected_channels") or [])
    original_ids = set(data.get("folder_original_channels") or [])
    user_id = call.from_user.id

    if not folder_id:
        await call.answer("Ошибка: папка не найдена", show_alert=True)
        return

    # Определяем что добавить и что удалить
    to_add = selected_ids - original_ids
    to_remove = original_ids - selected_ids

    # Добавляем каналы
    for ch_id in to_add:
        try:
            await orm_add_channel_to_folder(
                session,
                user_id=call.from_user.id,
                folder_id=folder_id,
                channel_id=ch_id,
            )
        except Exception:
            pass

    # Удаляем каналы
    for ch_id in to_remove:
        try:
            await orm_remove_channel_from_folder(
                session,
                user_id=call.from_user.id,
                folder_id=folder_id,
                channel_id=ch_id,
            )
        except Exception:
            pass

    await session.commit()
    await state.clear()

    # Возвращаемся к папке
    folders = await orm_get_user_folders(session, user_id=call.from_user.id)
    folder = next((f for f in folders if f.id == folder_id), None)

    if folder:
        channels = await orm_get_folder_channels(session, user_id=user_id, folder_id=folder_id)

        channels_count = len(channels)

        await call.message.edit_text(
            f"✅ Каналы сохранены!\n\n" + FOLDER_EDIT_TEXT.format(title=folder.title),
            parse_mode="HTML",
            reply_markup=build_folder_edit_kb(folder_id, channels_count),
        )
    else:
        # Вернуться в список папок
        await call.message.edit_text(
            FOLDERS_TEXT,
            parse_mode="HTML",
            reply_markup=build_folders_list_kb(folders),
        )

    await call.answer("✅ Сохранено")


@settings_router.callback_query(FolderChannelsCD.filter(F.action == "back"),
                                StateFilter(SettingsStates.choosing_folder_channels))
async def folder_channels_back(call: types.CallbackQuery, callback_data: FolderChannelsCD, state: FSMContext,
                               session: AsyncSession):
    """Отмена выбора каналов."""
    data = await state.get_data()
    folder_id = data.get("edit_folder_id") or data.get("new_folder_id")
    user_id = call.from_user.id

    await state.clear()

    if folder_id:
        # Если редактировали существующую папку - вернуться к ней
        folders = await orm_get_user_folders(session, user_id=call.from_user.id)
        folder = next((f for f in folders if f.id == folder_id), None)

        if folder:
            channels = await orm_get_folder_channels(session, user_id=user_id, folder_id=folder_id)

            channels_count = len(channels)

            await call.message.edit_text(
                FOLDER_EDIT_TEXT.format(title=folder.title),
                parse_mode="HTML",
                reply_markup=build_folder_edit_kb(folder_id, channels_count),
            )
            await call.answer()
            return

    # Иначе вернуться в список папок
    folders = await orm_get_user_folders(session, user_id=call.from_user.id)

    await call.message.edit_text(
        FOLDERS_TEXT,
        parse_mode="HTML",
        reply_markup=build_folders_list_kb(folders),
    )
    await call.answer()
