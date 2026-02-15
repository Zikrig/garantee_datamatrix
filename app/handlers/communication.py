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
        # Получаем последнюю заявку пользователя независимо от статуса
        last_claim = await db.get_last_claim(message.from_user.id)
        
        if not last_claim:
            logging.debug(f"No claims found for user {message.from_user.id}")
            return False
        
        status = last_claim.get("status", "")
        
        # Проверяем, что заявка активна (не закрыта/решена)
        if status in ["Решено", "Закрыта"]:
            logging.info(f"User {message.from_user.id} has closed/resolved claim #{last_claim['id']} (status: {status}), not forwarding message")
            return False
        
        # Проверяем только активные статусы
        if status in ["Новая", "В работе", "Нужны уточнения"]:
            logging.debug(f"User {message.from_user.id} has active claim #{last_claim['id']} (status: {status})")
            return True
        
        logging.debug(f"User {message.from_user.id} has claim #{last_claim['id']} with unknown status: {status}")
        return False

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
    claim = await db.get_last_claim(message.from_user.id)
    
    if not claim:
        logging.debug(f"No claim found for user {message.from_user.id}, not forwarding")
        return False
    
    status = claim.get("status", "")
    
    # Проверяем, что заявка не закрыта
    if status in ["Решено", "Закрыта"]:
        logging.info(f"User {message.from_user.id} message not forwarded - claim #{claim['id']} is {status}")
        return False
    
    # Проверяем, что заявка активна
    if status not in ["Новая", "В работе", "Нужны уточнения"]:
        logging.info(f"User {message.from_user.id} message not forwarded - claim #{claim['id']} has status: {status}")
        return False
    
    logging.info(f"Forwarding message from user {message.from_user.id} to admin thread (claim #{claim['id']}, status: {status})")
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
        await message.reply("❌ Пользователь не найден для этого топика.")
        return

    # Получаем последнюю заявку пользователя
    claim = await db.get_last_claim(user["tg_id"])
    
    if not claim:
        await message.reply("❌ У пользователя нет заявок. Сообщение не отправлено.")
        return
    
    status = claim.get("status", "")
    
    # Проверяем, что заявка активна (не закрыта/решена)
    if status in ["Решено", "Закрыта"]:
        await message.reply(f"❌ Заявка #{claim['id']} имеет статус «{status}». Сообщение не отправлено пользователю.")
        return
    
    # Проверяем, что заявка активна
    if status not in ["Новая", "В работе", "Нужны уточнения"]:
        await message.reply(f"❌ Заявка #{claim['id']} имеет статус «{status}». Сообщение не отправлено пользователю.")
        return

    try:
        await message.copy_to(user["tg_id"])
        # Добавляем заметку только если заявка активна
        if message.text:
            await db.add_claim_note(claim["id"], "manager", message.text)
    except Exception as e:
        logging.error(f"Failed to forward reply: {e}")
        await message.reply(f"❌ Ошибка при отправке сообщения пользователю: {e}")

