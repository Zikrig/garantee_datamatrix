import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from app.database import db
from app.handlers import common, admin, warranty, claims, kb_admin, communication

async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN is required")

    await db.init()

    bot = Bot(token=token)
    dp = Dispatcher(storage=MemoryStorage())

    # Order matters: communication handlers (relaying) should be after state-specific handlers
    # but before generic message handlers. AIogram 3.x routers handle this via order of include.
    
    # 1. Admin & KB Management
    dp.include_router(admin.router)
    dp.include_router(kb_admin.router)
    
    # 2. Specific User Flows (States)
    dp.include_router(warranty.router)
    dp.include_router(claims.router)
    
    # 3. Communication/Relay (should be after states to not catch state messages)
    dp.include_router(communication.router)
    
    # 4. Common (Start, Menu, Unexpected)
    dp.include_router(common.router)

    logging.info("Bot started polling")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Bot stopped")
