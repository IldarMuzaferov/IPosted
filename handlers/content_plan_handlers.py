# =============================================================================
# handlers/content_plan_handlers.py - Обработчики контент-плана
# =============================================================================

from datetime import datetime, date, timedelta

from aiogram import Router, F, types
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy.ext.asyncio import AsyncSession

from filters.chat_types import ChatTypeFilter
from kbds.inline import (
    ContentPlanCD, ContentPlanDayCD, ContentPlanCalendarCD, ContentPlanPostCD,
    build_content_plan_main_kb, build_content_plan_channels_kb,
    build_content_plan_day_kb, build_content_plan_calendar_kb,
    build_all_scheduled_posts_kb, build_post_view_kb, build_delete_confirm_kb,
    build_no_posts_kb,
)
from kbds.callbacks import ContentPlanStates, format_date_full, MONTH_NAMES_GENITIVE, MONTH_NAMES
from database.orm_query import (
    orm_get_user, orm_get_user_folders, orm_get_folder_channels,
    orm_get_channels_without_folder, orm_get_user_channels,
    orm_get_target_full, orm_get_post_buttons, orm_get_dates_with_posts, orm_get_scheduled_dates_with_count,
    orm_delete_target, orm_get_channels_targets_for_date,
)

from kbds.inline import ik_create_root_menu


# =============================================================================
# ВРЕМЕННЫЕ ORM ФУНКЦИИ (перенести в orm_query.py)
# =============================================================================

content_plan_router = Router()
content_plan_router.message.filter(ChatTypeFilter(["private"]))


# Обработчик для пустых кнопок календаря
@content_plan_router.callback_query(F.data == "ignore")
async def ignore_callback(call: types.CallbackQuery):
    """Игнорируем нажатия на пустые кнопки."""
    await call.answer()


# =============================================================================
# ТЕКСТЫ
# =============================================================================

CONTENT_PLAN_MAIN_TEXT = (
    "📊 <b>КОНТЕНТ-ПЛАН</b>\n\n"
    "В этом разделе вы можете просматривать и изменять "
    "запланированные публикации.\n\n"
    "Выберите канал, в котором хотите увидеть контент-план."
)

CONTENT_PLAN_DAY_TEXT = (
    "📊 <b>КОНТЕНТ-ПЛАН</b>\n\n"
    "На {date_str} в канале <b>{channel_name}</b> "
    "{posts_text}."
)

CONTENT_PLAN_NO_POSTS_TEXT = (
    "📊 <b>КОНТЕНТ-ПЛАН</b>\n\n"
    "В выбранных каналах нет запланированных постов."
)

POST_VIEW_TEXT = (
    "📝 <b>Пост</b>\n\n"
    "Статус: {status}\n"
    "{link_text}"
    "Дата: {date_str}"
)


def get_utc_offset_for_user(user) -> int:
    """Получает UTC offset из timezone пользователя."""
    if not user or not user.timezone:
        return 3  # Default Moscow

    # Простой маппинг для основных зон
    tz_offsets = {
        "Europe/Moscow": 3,
        "Europe/London": 0,
        "Europe/Paris": 1,
        "Europe/Berlin": 1,
        "Europe/Kiev": 2,
        "Europe/Istanbul": 3,
        "Asia/Dubai": 4,
        "Asia/Tashkent": 5,
        "Asia/Almaty": 6,
        "Asia/Bangkok": 7,
        "Asia/Shanghai": 8,
        "Asia/Tokyo": 9,
        "Australia/Sydney": 10,
        "Pacific/Auckland": 12,
        "America/New_York": -5,
        "America/Chicago": -6,
        "America/Denver": -7,
        "America/Los_Angeles": -8,
        "America/Anchorage": -9,
        "Pacific/Honolulu": -10,
    }
    return tz_offsets.get(user.timezone, 3)


def posts_count_text(count: int) -> str:
    """Склонение слова 'пост'."""
    if count == 0:
        return "нет постов"
    if count == 1:
        return "запланирован 1 пост"
    if 2 <= count <= 4:
        return f"запланировано {count} поста"
    return f"запланировано {count} постов"


def get_status_text(state: str) -> str:
    """Текст статуса поста."""
    statuses = {
        "draft": "📝 Черновик",
        "scheduled": "⏰ Запланирован",
        "queued": "🔄 В очереди",
        "sent": "✅ Опубликован",
        "failed": "❌ Ошибка",
        "canceled": "🚫 Отменён",
    }
    return statuses.get(state, state)


# =============================================================================
# ГЛАВНОЕ МЕНЮ КОНТЕНТ-ПЛАНА
# =============================================================================

@content_plan_router.message(F.text == "Контент-план")
async def content_plan_start(message: types.Message, state: FSMContext, session: AsyncSession):
    """Reply-кнопка 'Контент-план'."""
    await state.clear()

    user_id = message.from_user.id

    # Получаем папки
    folders = await orm_get_user_folders(session, user_id=user_id)

    # Проверяем есть ли каналы без папок
    channels_no_folder = await orm_get_channels_without_folder(session, user_id=user_id)
    has_no_folder = len(channels_no_folder) > 0

    await message.answer(
        CONTENT_PLAN_MAIN_TEXT,
        parse_mode="HTML",
        reply_markup=build_content_plan_main_kb(folders, has_no_folder),
    )


@content_plan_router.callback_query(ContentPlanCD.filter(F.action == "main"))
async def content_plan_main(call: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    """Возврат в главное меню контент-плана."""
    await state.clear()

    user_id = call.from_user.id

    folders = await orm_get_user_folders(session, user_id=user_id)
    channels_no_folder = await orm_get_channels_without_folder(session, user_id=user_id)
    has_no_folder = len(channels_no_folder) > 0

    await call.message.edit_text(
        CONTENT_PLAN_MAIN_TEXT,
        parse_mode="HTML",
        reply_markup=build_content_plan_main_kb(folders, has_no_folder),
    )
    await call.answer()


@content_plan_router.callback_query(ContentPlanCD.filter(F.action == "back"))
async def content_plan_back_to_root(call: types.CallbackQuery, state: FSMContext):
    """Возврат в главное меню бота."""
    await state.clear()
    await call.message.edit_text(
        "Выберите действие:",
        reply_markup=ik_create_root_menu(),
    )
    await call.answer()


# =============================================================================
# ВЫБОР ПАПКИ / КАНАЛОВ БЕЗ ПАПКИ
# =============================================================================

@content_plan_router.callback_query(ContentPlanCD.filter(F.action == "folder"))
async def content_plan_folder(call: types.CallbackQuery, callback_data: ContentPlanCD, state: FSMContext,
                              session: AsyncSession):
    """Выбор папки - показать каналы в папке."""
    folder_id = callback_data.folder_id
    user_id = call.from_user.id

    channels = await orm_get_folder_channels(session, user_id=user_id, folder_id=folder_id)

    if not channels:
        await call.answer("В этой папке нет каналов", show_alert=True)
        return

    await state.update_data(cp_folder_id=folder_id)

    await call.message.edit_text(
        CONTENT_PLAN_MAIN_TEXT,
        parse_mode="HTML",
        reply_markup=build_content_plan_channels_kb(channels, folder_id),
    )
    await call.answer()


@content_plan_router.callback_query(ContentPlanCD.filter(F.action == "no_folder"))
async def content_plan_no_folder(call: types.CallbackQuery, state: FSMContext, session: AsyncSession):
    """Каналы без папок."""
    user_id = call.from_user.id

    channels = await orm_get_channels_without_folder(session, user_id=user_id)

    if not channels:
        await call.answer("Нет каналов без папок", show_alert=True)
        return

    await state.update_data(cp_folder_id=0)

    await call.message.edit_text(
        CONTENT_PLAN_MAIN_TEXT,
        parse_mode="HTML",
        reply_markup=build_content_plan_channels_kb(channels, folder_id=0),
    )
    await call.answer()


# =============================================================================
# ВЫБОР КАНАЛА / ВСЕХ КАНАЛОВ
# =============================================================================

@content_plan_router.callback_query(ContentPlanCD.filter(F.action == "channel"))
async def content_plan_select_channel(call: types.CallbackQuery, callback_data: ContentPlanCD, state: FSMContext,
                                      session: AsyncSession):
    """Выбор конкретного канала."""
    channel_id = callback_data.channel_id
    user_id = call.from_user.id

    # Сохраняем выбранные каналы
    await state.update_data(
        cp_channel_ids=[channel_id],
        cp_single_channel=True,
    )

    # Показываем посты на сегодня
    await _show_day_view(call, state, session, date.today())


@content_plan_router.callback_query(ContentPlanCD.filter(F.action == "all"))
async def content_plan_select_all(call: types.CallbackQuery, callback_data: ContentPlanCD, state: FSMContext,
                                  session: AsyncSession):
    """Выбор всех каналов (из папки или всех)."""
    user_id = call.from_user.id
    folder_id = callback_data.folder_id

    data = await state.get_data()

    if folder_id:
        # Все каналы из папки
        channels = await orm_get_folder_channels(session, user_id=user_id, folder_id=folder_id)
    else:
        # Все каналы пользователя
        channels = await orm_get_user_channels(session, user_id=user_id)

    if not channels:
        await call.answer("Нет каналов", show_alert=True)
        return

    channel_ids = [ch.id for ch in channels]

    await state.update_data(
        cp_channel_ids=channel_ids,
        cp_single_channel=False,
    )

    # Показываем посты на сегодня
    await _show_day_view(call, state, session, date.today())


# =============================================================================
# ПРОСМОТР ДНЯ
# =============================================================================

async def _show_day_view(call: types.CallbackQuery, state: FSMContext, session: AsyncSession, target_date: date):
    """Показывает посты на конкретный день."""
    data = await state.get_data()
    channel_ids = data.get("cp_channel_ids", [])
    single_channel = data.get("cp_single_channel", False)

    user = await orm_get_user(session, user_id=call.from_user.id)
    utc_offset = get_utc_offset_for_user(user)

    # Получаем посты на день
    targets = await orm_get_channels_targets_for_date(
        session,
        channel_ids=channel_ids,
        target_date=target_date,
    )

    # Формируем текст
    date_str = format_date_full(target_date)

    if single_channel and channel_ids:
        # Получаем название канала
        from database.orm_query import orm_get_channel
        try:
            channel = await orm_get_channel(session, channel_id=channel_ids[0])
            channel_name = channel.title if channel else "канал"
        except Exception:
            channel_name = "канал"
    else:
        channel_name = "выбранных каналах"

    posts_text = posts_count_text(len(targets))

    text = CONTENT_PLAN_DAY_TEXT.format(
        date_str=date_str,
        channel_name=channel_name,
        posts_text=posts_text,
    )

    # Сохраняем текущую дату
    await state.update_data(
        cp_current_date=target_date.isoformat(),
    )

    kb = build_content_plan_day_kb(targets, target_date, utc_offset)

    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except TelegramBadRequest:
        pass

    await call.answer()


@content_plan_router.callback_query(ContentPlanDayCD.filter(F.action == "view"))
async def content_plan_day_view(call: types.CallbackQuery, callback_data: ContentPlanDayCD, state: FSMContext,
                                session: AsyncSession):
    """Просмотр конкретного дня."""
    target_date = date(callback_data.year, callback_data.month, callback_data.day)
    await _show_day_view(call, state, session, target_date)


# =============================================================================
# КАЛЕНДАРЬ
# =============================================================================

@content_plan_router.callback_query(ContentPlanCalendarCD.filter(F.action == "back"))
async def content_plan_calendar_show(call: types.CallbackQuery, callback_data: ContentPlanCalendarCD, state: FSMContext,
                                     session: AsyncSession):
    """Показать календарь."""
    data = await state.get_data()
    channel_ids = data.get("cp_channel_ids", [])

    year = callback_data.year or datetime.now().year
    month = callback_data.month or datetime.now().month
    day = callback_data.day or datetime.now().day

    user = await orm_get_user(session, user_id=call.from_user.id)
    utc_offset = get_utc_offset_for_user(user)

    # Получаем дни с постами
    days_with_posts = await orm_get_dates_with_posts(
        session,
        channel_ids=channel_ids,
        year=year,
        month=month,
    )

    # Получаем посты на выбранный день
    target_date = date(year, month, day)
    targets = await orm_get_channels_targets_for_date(
        session,
        channel_ids=channel_ids,
        target_date=target_date,
    )

    kb = build_content_plan_calendar_kb(targets, year, month, days_with_posts, utc_offset)

    text = f"📅 <b>Календарь</b>\n\nВыбран: {day} {MONTH_NAMES_GENITIVE[month]} {year} г."

    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except TelegramBadRequest:
        pass

    await call.answer()


@content_plan_router.callback_query(ContentPlanCalendarCD.filter(F.action == "prev_month"))
@content_plan_router.callback_query(ContentPlanCalendarCD.filter(F.action == "next_month"))
async def content_plan_calendar_nav(call: types.CallbackQuery, callback_data: ContentPlanCalendarCD, state: FSMContext,
                                    session: AsyncSession):
    """Навигация по месяцам."""
    data = await state.get_data()
    channel_ids = data.get("cp_channel_ids", [])

    year = callback_data.year
    month = callback_data.month

    user = await orm_get_user(session, user_id=call.from_user.id)
    utc_offset = get_utc_offset_for_user(user)

    # Получаем дни с постами
    days_with_posts = await orm_get_dates_with_posts(
        session,
        channel_ids=channel_ids,
        year=year,
        month=month,
    )

    # Пустой список targets (день не выбран)
    kb = build_content_plan_calendar_kb([], year, month, days_with_posts, utc_offset)

    text = f"📅 <b>Календарь - {MONTH_NAMES[month]} {year}</b>"

    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except TelegramBadRequest:
        pass

    await call.answer()


@content_plan_router.callback_query(ContentPlanCalendarCD.filter(F.action == "select_day"))
async def content_plan_calendar_select_day(call: types.CallbackQuery, callback_data: ContentPlanCalendarCD,
                                           state: FSMContext, session: AsyncSession):
    """Выбор дня в календаре."""
    data = await state.get_data()
    channel_ids = data.get("cp_channel_ids", [])

    year = callback_data.year
    month = callback_data.month
    day = callback_data.day

    user = await orm_get_user(session, user_id=call.from_user.id)
    utc_offset = get_utc_offset_for_user(user)

    # Получаем дни с постами
    days_with_posts = await orm_get_dates_with_posts(
        session,
        channel_ids=channel_ids,
        year=year,
        month=month,
    )

    # Получаем посты на выбранный день
    target_date = date(year, month, day)
    targets = await orm_get_channels_targets_for_date(
        session,
        channel_ids=channel_ids,
        target_date=target_date,
    )

    kb = build_content_plan_calendar_kb(targets, year, month, days_with_posts, utc_offset)

    text = f"📅 <b>Календарь</b>\n\nВыбран: {day} {MONTH_NAMES_GENITIVE[month]} {year} г.\nПостов: {len(targets)}"

    try:
        await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except TelegramBadRequest:
        pass

    await call.answer()


# =============================================================================
# ВСЕ ОТЛОЖЕННЫЕ ПОСТЫ
# =============================================================================

@content_plan_router.callback_query(ContentPlanCalendarCD.filter(F.action == "all_posts"))
async def content_plan_all_posts(call: types.CallbackQuery, callback_data: ContentPlanCalendarCD, state: FSMContext,
                                 session: AsyncSession):
    """Все отложенные посты."""
    data = await state.get_data()
    channel_ids = data.get("cp_channel_ids", [])

    dates_with_count = await orm_get_scheduled_dates_with_count(
        session,
        channel_ids=channel_ids,
    )

    if not dates_with_count:
        await call.message.edit_text(
            CONTENT_PLAN_NO_POSTS_TEXT,
            parse_mode="HTML",
            reply_markup=build_no_posts_kb(),
        )
        await call.answer()
        return

    kb = build_all_scheduled_posts_kb(dates_with_count)

    text = f"📋 <b>Все отложенные посты</b>\n\nВсего дат: {len(dates_with_count)}"

    await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await call.answer()


# =============================================================================
# ПРОСМОТР ПОСТА
# =============================================================================

@content_plan_router.callback_query(ContentPlanPostCD.filter(F.action == "view"))
async def content_plan_post_view(call: types.CallbackQuery, callback_data: ContentPlanPostCD, state: FSMContext,
                                 session: AsyncSession):
    """Просмотр поста."""
    target_id = callback_data.target_id

    try:
        target = await orm_get_target_full(session, target_id=target_id)
    except Exception:
        await call.answer("Пост не найден", show_alert=True)
        return

    user = await orm_get_user(session, user_id=call.from_user.id)
    utc_offset = get_utc_offset_for_user(user)

    # Формируем текст статуса
    status = get_status_text(target.state.value)

    # Ссылка на сообщение
    link_text = ""
    if target.state.value == "sent" and target.sent_message_id:
        # Формируем ссылку
        channel_id = target.channel_id
        msg_id = target.sent_message_id
        # Для приватных каналов: t.me/c/CHANNEL_ID/MSG_ID (без минуса и первых цифр)
        channel_link_id = str(channel_id).replace("-100", "")
        link_text = f"Ссылка: t.me/c/{channel_link_id}/{msg_id}\n"

    # Дата
    post_time = target.sent_at or target.publish_at
    if post_time:
        local_time = post_time + timedelta(hours=utc_offset)
        date_str = f"{local_time.day} {MONTH_NAMES_GENITIVE[local_time.month]} {local_time.year} г. в {local_time.strftime('%H:%M')}"
    else:
        date_str = "не указана"

    text = POST_VIEW_TEXT.format(
        status=status,
        link_text=link_text,
        date_str=date_str,
    )

    # Сохраняем target_id для возврата
    await state.update_data(cp_viewing_target_id=target_id)

    # Отправляем пост
    post = target.post

    # Удаляем старое сообщение
    try:
        await call.message.delete()
    except Exception:
        pass

    # Отправляем превью поста
    if post.media:
        media = sorted(post.media, key=lambda m: m.order_index)
        first_media = media[0]

        if first_media.media_type.value == "photo":
            await call.message.answer_photo(
                photo=first_media.file_id,
                caption=post.text,
            )
        elif first_media.media_type.value == "video":
            await call.message.answer_video(
                video=first_media.file_id,
                caption=post.text,
            )
        elif first_media.media_type.value == "document":
            await call.message.answer_document(
                document=first_media.file_id,
                caption=post.text,
            )
        else:
            await call.message.answer(post.text or "Пост без текста")
    else:
        await call.message.answer(post.text or "Пост без текста")

    # Отправляем информацию
    await call.message.answer(
        text,
        parse_mode="HTML",
        reply_markup=build_post_view_kb(target_id),
    )

    await call.answer()


@content_plan_router.callback_query(ContentPlanPostCD.filter(F.action == "back"))
async def content_plan_post_back(call: types.CallbackQuery, callback_data: ContentPlanPostCD, state: FSMContext,
                                 session: AsyncSession):
    """Возврат из просмотра поста."""
    data = await state.get_data()

    # Возвращаемся к просмотру дня
    current_date_str = data.get("cp_current_date")
    if current_date_str:
        target_date = date.fromisoformat(current_date_str)
    else:
        target_date = date.today()

    await _show_day_view(call, state, session, target_date)


# =============================================================================
# УДАЛЕНИЕ ПОСТА
# =============================================================================

@content_plan_router.callback_query(ContentPlanPostCD.filter(F.action == "delete"))
async def content_plan_post_delete(call: types.CallbackQuery, callback_data: ContentPlanPostCD, state: FSMContext):
    """Запрос подтверждения удаления."""
    target_id = callback_data.target_id

    await call.message.edit_text(
        "❓ Вы уверены, что хотите удалить этот пост?",
        reply_markup=build_delete_confirm_kb(target_id),
    )
    await call.answer()


@content_plan_router.callback_query(ContentPlanPostCD.filter(F.action == "delete_confirm"))
async def content_plan_post_delete_confirm(call: types.CallbackQuery, callback_data: ContentPlanPostCD,
                                           state: FSMContext, session: AsyncSession):
    """Подтверждение удаления."""
    target_id = callback_data.target_id

    await orm_delete_target(session, target_id=target_id)
    await session.commit()

    await call.answer("✅ Пост удалён", show_alert=True)

    # Возвращаемся к просмотру дня
    data = await state.get_data()
    current_date_str = data.get("cp_current_date")
    if current_date_str:
        target_date = date.fromisoformat(current_date_str)
    else:
        target_date = date.today()

    await _show_day_view(call, state, session, target_date)


# =============================================================================
# ДУБЛИРОВАНИЕ И ИЗМЕНЕНИЕ
# =============================================================================

@content_plan_router.callback_query(ContentPlanPostCD.filter(F.action == "duplicate"))
async def content_plan_post_duplicate(call: types.CallbackQuery, callback_data: ContentPlanPostCD, state: FSMContext,
                                      session: AsyncSession):
    """Дублирование поста - переход к выбору каналов."""
    target_id = callback_data.target_id

    # Сохраняем что это дублирование
    await state.update_data(
        duplicate_source_target_id=target_id,
        edit_mode=False,
    )

    # Переходим к выбору канала (как при создании поста)
    await state.set_state(ContentPlanStates.duplicate_choosing_channel)

    user_id = call.from_user.id
    folders = await orm_get_user_folders(session, user_id=user_id)
    channels_no_folder = await orm_get_channels_without_folder(session, user_id=user_id)
    has_no_folder = len(channels_no_folder) > 0

    await call.message.edit_text(
        "📋 <b>ДУБЛИРОВАНИЕ ПОСТА</b>\n\n"
        "Выберите канал или папку для публикации копии поста.",
        parse_mode="HTML",
        reply_markup=build_content_plan_main_kb(folders, has_no_folder),
    )
    await call.answer()


@content_plan_router.callback_query(ContentPlanPostCD.filter(F.action == "edit"))
async def content_plan_post_edit(call: types.CallbackQuery, callback_data: ContentPlanPostCD, state: FSMContext,
                                 session: AsyncSession):
    """Изменение поста - переход в редактор."""
    target_id = callback_data.target_id

    try:
        target = await orm_get_target_full(session, target_id=target_id)
    except Exception:
        await call.answer("Пост не найден", show_alert=True)
        return

    # Сохраняем что это редактирование
    await state.update_data(
        edit_target_id=target_id,
        edit_post_id=target.post_id,
        edit_mode=True,
        selected_channel_ids={target.channel_id},
    )

    # Переходим в редактор (CreatePostStates.composing)
    from kbds.callbacks import CreatePostStates
    await state.set_state(CreatePostStates.composing)

    post = target.post

    # Отправляем превью поста
    from kbds.post_editor import (
        EditorState, editor_state_to_dict, build_editor_kb,
        make_ctx_from_message, merge_url_and_editor_kb,
    )
    from database.orm_query import orm_get_post_buttons

    # Удаляем старое сообщение
    try:
        await call.message.delete()
    except Exception:
        pass

    # Отправляем пост как превью
    if post.media:
        media = sorted(post.media, key=lambda m: m.order_index)
        first_media = media[0]

        if first_media.media_type.value == "photo":
            res = await call.message.answer_photo(
                photo=first_media.file_id,
                caption=post.text,
            )
        elif first_media.media_type.value == "video":
            res = await call.message.answer_video(
                video=first_media.file_id,
                caption=post.text,
            )
        else:
            res = await call.message.answer(post.text or "​")
    else:
        res = await call.message.answer(post.text or "​")

    # Создаём EditorState
    from kbds.post_editor import EditorContext

    has_media = bool(post.media)
    has_text = bool(post.text)

    if has_media and not has_text:
        kind = "photo"
    elif has_media and has_text:
        kind = "photo"
    else:
        kind = "text"

    ctx = EditorContext(
        kind=kind,
        has_media=has_media,
        has_text=has_text,
        text_was_initial=has_text,
        text_added_later=False,
    )

    st = EditorState(
        post_id=post.id,
        preview_chat_id=call.message.chat.id,
        preview_message_id=res.message_id,
        bell=not post.silent,
        reactions=post.reactions_enabled,
        content_protect=post.protected,
        comments=post.comments_enabled,
        pin=post.pinned,
        text_position=post.text_position or "bottom",
        selected_channels_count=1,
    )

    # Проверяем URL-кнопки
    existing_buttons = await orm_get_post_buttons(session, post_id=post.id)
    if existing_buttons:
        st.has_url_buttons = True

    from kbds.post_editor import editor_ctx_to_dict

    await state.update_data(
        editor=editor_state_to_dict(st),
        editor_context=editor_ctx_to_dict(ctx),
    )

    # Строим клавиатуру
    editor_kb = build_editor_kb(post.id, st, ctx=ctx)
    if existing_buttons:
        combined_kb = merge_url_and_editor_kb(existing_buttons, editor_kb)
    else:
        combined_kb = editor_kb

    # Добавляем клавиатуру
    await call.bot.edit_message_reply_markup(
        chat_id=call.message.chat.id,
        message_id=res.message_id,
        reply_markup=combined_kb,
    )

    await call.answer()