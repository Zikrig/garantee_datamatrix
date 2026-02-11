import logging
from aiogram import F, Router, Bot
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.database import db
from app.utils import get_or_create_user_thread
from app.constants import MAIN_MENU

router = Router()

from aiogram.filters import StateFilter, Filter

class HasActiveClaim(Filter):
    async def __call__(self, message: Message) -> bool:
        claim = await db.get_last_claim_by_status(message.from_user.id, "Нужны уточнения") or \
                await db.get_last_claim_by_status(message.from_user.id, "В работе") or \
                await db.get_last_claim_by_status(message.from_user.id, "Новая")
        return bool(claim)

@router.message(
    F.chat.type == "private", 
    StateFilter(None), 
    ~F.text.startswith("/"), 
    ~F.text.in_(MAIN_MENU), 
    ~F.text.in_({"Отмена", "Готово", "Пропустить", "Чек WB", "Честный знак"}),
    HasActiveClaim()
)
async def attach_clarification(message: Message, bot: Bot, state: FSMContext) -> bool:
    # Forward user message to admin thread if user has an active claim
    claim = await db.get_last_claim_by_status(message.from_user.id, "Нужны уточнения") or \
            await db.get_last_claim_by_status(message.from_user.id, "В работе") or \
            await db.get_last_claim_by_status(message.from_user.id, "Новая")
    
    logging.info(f"Forwarding message from user {message.from_user.id} to admin thread")
    group_id_str = await db.get_setting("admin_group_id")
    if not group_id_str:
        return False

    group_id = int(group_id_str)
    thread_id = await get_or_create_user_thread(bot, db, message.from_user.id)
    if not thread_id:
        return False

    try:
        await message.copy_to(group_id, message_thread_id=thread_id)
        if message.text:
            await db.add_claim_note(claim["id"], "user", message.text)
        if message.photo:
            await db.add_claim_file(claim["id"], message.photo[-1].file_id, "photo")
        elif message.video:
            await db.add_claim_file(claim["id"], message.video.file_id, "video")
        elif message.document:
            await db.add_claim_file(claim["id"], message.document.file_id, "document")
        return True
    except Exception as e:
        logging.error(f"Failed to forward message: {e}")
        return False

@router.message(F.chat.type.in_({"supergroup", "group"}), ~F.text.startswith("/"))
async def admin_group_reply_handler(message: Message, bot: Bot) -> None:
    # Forward admin message from thread to user
    if not message.message_thread_id:
        return

    group_id_str = await db.get_setting("admin_group_id")
    if not group_id_str or str(message.chat.id) != group_id_str:
        return

    user = await db.get_user_by_thread(message.message_thread_id)
    if not user:
        return

    try:
        await message.copy_to(user["tg_id"])
        claim = await db.get_last_claim_by_status(user["tg_id"], "Нужны уточнения") or \
                await db.get_last_claim_by_status(user["tg_id"], "В работе") or \
                await db.get_last_claim_by_status(user["tg_id"], "Новая")
        if claim and message.text:
            await db.add_claim_note(claim["id"], "manager", message.text)
    except Exception as e:
        logging.error(f"Failed to forward reply: {e}")

