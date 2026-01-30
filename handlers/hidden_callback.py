from aiogram import Router, F, types
from sqlalchemy.ext.asyncio import AsyncSession
from database.orm_query import orm_get_hidden_part, orm_get_post_with_channel

hidden_callback_router = Router()


@hidden_callback_router.callback_query(F.data.startswith("hidden:"))
async def hidden_content_click(call: types.CallbackQuery, session: AsyncSession):
    try:
        post_id = int(call.data.split(":")[1])
    except (IndexError, ValueError):
        await call.answer("Ошибка", show_alert=True)
        return

    user_id = call.from_user.id

    hidden = await orm_get_hidden_part(session, post_id=post_id)
    if not hidden:
        await call.answer("Контент недоступен", show_alert=True)
        return

    post = await orm_get_post_with_channel(session, post_id=post_id)
    if not post or not post.targets:
        await call.answer("Контент недоступен", show_alert=True)
        return

    channel_id = post.targets[0].channel_id

    try:
        member = await call.bot.get_chat_member(chat_id=channel_id, user_id=user_id)
        is_subscribed = member.status in ("member", "administrator", "creator")
    except Exception:
        is_subscribed = False

    if is_subscribed:
        text = hidden.subscriber_text
        if len(text) > 200:
            try:
                await call.bot.send_message(
                    chat_id=user_id,
                    text=f"🔓 <b>Скрытый контент</b>\n\n{text}",
                    parse_mode="HTML",
                )
                await call.answer("Отправлено в ЛС", show_alert=True)
            except Exception:
                await call.answer(text[:190] + "...", show_alert=True)
        else:
            await call.answer(text, show_alert=True)
    else:
        msg = hidden.nonsubscriber_text or "🔒 Подпишитесь на канал!"
        await call.answer(msg, show_alert=True)
