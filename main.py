from create_bot import dp, bot
import asyncio

from handlers.hidden_callback import hidden_callback_router
from middlewares.db import DataBaseSession
from database.engine import create_db, drop_db, session_maker
from handlers.user_private import user_private_router
from scheduler_worker import scheduler_loop

dp.include_router(user_private_router)
dp.include_router(hidden_callback_router)
# dp.include_router(admin_router)


async def on_startup():
    run_param = False
    if run_param:
        await drop_db()
    await create_db()
    dp["scheduler_task"] = asyncio.create_task(scheduler_loop(bot, session_maker))



async def main():
    dp.startup.register(on_startup)
    dp.update.middleware(DataBaseSession(session_pool=session_maker))
    await bot.delete_webhook(drop_pending_updates=True)

    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот выключился")
