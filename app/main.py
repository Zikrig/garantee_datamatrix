import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from app.database import db
from app.handlers import common, admin, warranty, claims, kb_admin, communication, unexpected
from app.sheets import sheets_sync_scheduler

async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN is required")

    await db.init()

    bot = Bot(token=token)
    dp = Dispatcher(storage=MemoryStorage())

    # Order matters for AIogram 3.x routers
    
    # 1. Admin & KB Management
    dp.include_router(admin.router)
    dp.include_router(kb_admin.router)
    
    # 2. Specific User Flows (States)
    dp.include_router(warranty.router)
    dp.include_router(claims.router)
    
    # 3. Common handlers (Commands, Menu buttons) - must be before communication relay
    dp.include_router(common.router)
    
    # 4. Communication/Relay (catches non-command messages when no state is active)
    dp.include_router(communication.router)
    
    # 5. Unexpected/Catch-all (at the very end)
    dp.include_router(unexpected.router)

    # Start Google Sheets sync in background
    asyncio.create_task(sheets_sync_scheduler())

    logging.info("Bot started polling")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Bot stopped")
