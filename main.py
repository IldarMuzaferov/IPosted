from create_bot import dp, bot
import asyncio
from middlewares.db import DataBaseSession
from database.engine import create_db, drop_db, session_maker
from handlers.user_private import user_private_router


dp.include_router(user_private_router)
# dp.include_router(user_group_router)
# dp.include_router(admin_router)


async def on_startup():
    run_param = False
    if run_param:
        await drop_db()
    await create_db()



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
