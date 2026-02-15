import asyncio
import json
import logging
import os
from html import escape
from aiogram import Bot
from aiogram.types import FSInputFile
from app.scanner import extract_datamatrix
from app.constants import CARE_TEXT, TRUST_TEXT

KB_JSON_PATH = "kb.json"

CATALOG_URL = os.getenv("CATALOG_URL", "https://example.com/catalog")
WB_URL = os.getenv("WB_URL", "https://www.wildberries.ru/")
TG_CHANNEL_URL = os.getenv("TG_CHANNEL_URL", "https://t.me/your_channel")
CERTS_URL = os.getenv("CERTS_URL", "https://example.com/certs")
FAQ_URL = os.getenv("FAQ_URL", "https://example.com/faq")

ADMIN_CHAT_IDS_RAW = os.getenv("ADMIN_CHAT_IDS", "")
ADMIN_CHAT_IDS = [
    int(item.strip())
    for item in ADMIN_CHAT_IDS_RAW.replace(";", ",").split(",")
    if item.strip().isdigit()
]

DEFAULT_KB = {
    "care": CARE_TEXT,
    "trust": TRUST_TEXT,
    "useful": "📘 Полезные ссылки и материалы от UkaTaka",
    "faq": "❓ Часто задаваемые вопросы",
    "links": {
        "useful": [
            {"label": "Наш каталог", "url": CATALOG_URL},
            {"label": "Наш канал", "url": TG_CHANNEL_URL},
            {"label": "Сертификаты", "url": CERTS_URL}
        ],
        "care": [
            {"label": "Лайфхаки в Telegram", "url": TG_CHANNEL_URL}
        ],
        "trust": [
            {"label": "Исследования и сертификаты", "url": CERTS_URL},
            {"label": "Перейти в Telegram", "url": TG_CHANNEL_URL}
        ],
        "faq": [
            {"label": "Подробнее", "url": FAQ_URL}
        ]
    }
}

def load_kb() -> dict:
    if os.path.exists(KB_JSON_PATH):
        try:
            with open(KB_JSON_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return DEFAULT_KB

def save_kb(data: dict):
    with open(KB_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def get_ours_tokens() -> list[str]:
    ours_raw = os.getenv("OUR_CODES", "")
    return [item.strip() for item in ours_raw.replace(";", ",").split(",") if item.strip()]

def format_decoded_codes(codes: list[str]) -> str:
    return "\n".join(codes)

async def decode_image(bytes_data: bytes) -> tuple[list[str], bool]:
    codes = await asyncio.to_thread(extract_datamatrix, bytes_data)
    tokens = get_ours_tokens()
    is_ours = False
    if tokens:
        for code in codes:
            if any(token in code for token in tokens):
                is_ours = True
                break
    return codes, is_ours

async def upsert_from_user(db, user) -> None:
    await db.upsert_user(user.id, user.username, None)

async def send_cached_photo(bot: Bot, db, chat_id: int, photo_path: str, caption: str, reply_markup=None, parse_mode=None) -> None:
    cache_key = f"file_id:{photo_path}"
    file_id = await db.get_setting(cache_key)
    
    if file_id:
        try:
            await bot.send_photo(chat_id, file_id, caption=caption, reply_markup=reply_markup, parse_mode=parse_mode)
            return
        except Exception as e:
            logging.warning(f"Failed to send cached photo {photo_path}: {e}")
            # If failed, try re-uploading
    
    if not os.path.exists(photo_path):
        logging.error(f"Photo file not found: {photo_path}")
        await bot.send_message(chat_id, caption, reply_markup=reply_markup, parse_mode=parse_mode)
        return

    try:
        photo = FSInputFile(photo_path)
        msg = await bot.send_photo(chat_id, photo, caption=caption, reply_markup=reply_markup, parse_mode=parse_mode)
        if msg.photo:
            new_file_id = msg.photo[-1].file_id
            await db.set_setting(cache_key, new_file_id)
    except Exception as e:
        logging.error(f"Failed to send/upload photo {photo_path}: {e}")
        await bot.send_message(chat_id, caption, reply_markup=reply_markup, parse_mode=parse_mode)

async def get_or_create_user_thread(bot: Bot, db, user_id: int) -> int | None:
    user = await db.get_user(user_id)
    if not user:
        return None
    
    group_id_str = await db.get_setting("admin_group_id")
    if not group_id_str:
        return None
    
    group_id = int(group_id_str)
    
    if user.get("thread_id"):
        return user["thread_id"]
    
    try:
        topic_name = f"{user.get('name') or user.get('username') or user_id} ({user_id})"
        forum_topic = await bot.create_forum_topic(group_id, topic_name)
        thread_id = forum_topic.message_thread_id
        await db.update_user_thread(user_id, thread_id)
        return thread_id
    except Exception as e:
        logging.error(f"Failed to create forum topic: {e}")
        return None

async def send_admin_claim(
    bot: Bot, db, claim: dict, files: list[dict], username: str | None, name: str | None, phone: str | None, email: str | None = None, receipt_items: str | None = None, receipt_date: str | None = None
) -> None:
    from app.keyboards import claim_status_kb

    # Используем переданные данные чека, если есть, иначе пытаемся найти в гарантии
    products_info = ""
    if receipt_items:
        products_info = f"\n<b>Товары в чеке:</b>\n{escape(receipt_items)}"
    else:
        warranties = await db.get_warranties(claim['tg_id'])
        w = next((w for w in warranties if w['cz_code'] == claim['purchase_value']), None)
        if w and w.get('receipt_items'):
            products_info = f"\n<b>Товары в чеке:</b>\n{escape(w['receipt_items'])}"
    
    receipt_date_info = ""
    if receipt_date:
        receipt_date_info = f"\n<b>Дата чека:</b> {escape(receipt_date)}"

    group_id_str = await db.get_setting("admin_group_id")
    if not group_id_str:
        if not ADMIN_CHAT_IDS:
            return

        text = (
            "🛠 <b>Новая заявка</b>\n"
            f"claim_id: <code>{escape(claim['id'])}</code>\n"
            f"дата: {escape(claim['created_at'])}\n"
            f"tg: {claim['tg_id']} @{escape(username or '-')}\n"
            f"имя: {escape(name or '-')}\n"
            f"телефон: {escape(phone or '-')}\n"
            f"email: {escape(email or '-')}\n"
            f"идентификатор: {escape(claim['purchase_type'])} / {escape(claim['purchase_value'])}\n"
            f"{receipt_date_info}{products_info}\n"
            f"текст: {escape(claim['description'])}\n"
        )
        for admin_id in ADMIN_CHAT_IDS:
            try:
                await bot.send_message(admin_id, text, reply_markup=claim_status_kb(claim["id"]), parse_mode="HTML")
            except Exception as e:
                logging.error(f"Failed to send message to admin {admin_id}: {e}")

        if files:
            for index, item in enumerate(files):
                caption = f"Файлы по заявке {claim['id']}" if index == 0 else None
                for admin_id in ADMIN_CHAT_IDS:
                    try:
                        if item["file_type"] == "photo":
                            await bot.send_photo(admin_id, item["file_id"], caption=caption)
                        elif item["file_type"] == "video":
                            await bot.send_video(admin_id, item["file_id"], caption=caption)
                        else:
                            await bot.send_document(admin_id, item["file_id"], caption=caption)
                    except Exception as e:
                        logging.error(f"Failed to send file to admin {admin_id}: {e}")
        return

    group_id = int(group_id_str)
    logging.info(f"Sending claim {claim['id']} to group {group_id} for user {claim['tg_id']}")
    
    thread_id = await get_or_create_user_thread(bot, db, claim["tg_id"])
    if not thread_id:
        logging.error(f"Failed to create/get thread for user {claim['tg_id']}. User exists: {await db.get_user(claim['tg_id']) is not None}")
        # Пытаемся отправить без thread_id (в основную группу)
        thread_id = None
    
    text = (
        "🛠 <b>Новая заявка</b>\n"
        f"ID: <code>{escape(claim['id'])}</code>\n"
        f"Дата: {escape(claim['created_at'])}\n"
        f"Пользователь: @{escape(username or '-')}\n"
        f"Имя: {escape(name or '-')}\n"
        f"Телефон: {escape(phone or '-')}\n"
        f"Email: {escape(email or '-')}\n"
        f"Идентификатор: {escape(claim['purchase_type'])} / {escape(claim['purchase_value'])}\n"
        f"{receipt_date_info}{products_info}\n"
        f"<b>Текст проблемы:</b>\n{escape(claim['description'])}"
    )
    
    try:
        send_kwargs = {
            "chat_id": group_id,
            "text": text,
            "reply_markup": claim_status_kb(claim["id"], is_group=True),
            "parse_mode": "HTML"
        }
        if thread_id:
            send_kwargs["message_thread_id"] = thread_id
        
        group_msg = await bot.send_message(**send_kwargs)
        logging.info(f"Successfully sent claim {claim['id']} to group {group_id}, message_id: {group_msg.message_id}")
        
        try:
            await bot.pin_chat_message(group_id, group_msg.message_id)
        except Exception as e:
            logging.warning(f"Failed to pin message in group: {e}")
        
        await db.update_claim_group_message(claim["id"], group_msg.message_id)
        
        clean_group_id = group_id_str.replace("-100", "")
        msg_link = f"https://t.me/c/{clean_group_id}/{group_msg.message_id}"
        
        private_text = (
            f"🛠 <b>Новая заявка {escape(claim['id'])}</b>\n"
            f"Пользователь: @{escape(username or '-')}\n"
            f"Ссылка: {msg_link}"
        )
        
        for admin_id in ADMIN_CHAT_IDS:
            try:
                await bot.send_message(admin_id, private_text, reply_markup=claim_status_kb(claim["id"], is_group=False, group_link=msg_link), parse_mode="HTML")
            except Exception as e:
                logging.error(f"Failed to send notification to admin {admin_id}: {e}")

        if files:
            for item in files:
                try:
                    if item["file_type"] == "photo":
                        await bot.send_photo(group_id, item["file_id"], message_thread_id=thread_id)
                    elif item["file_type"] == "video":
                        await bot.send_video(group_id, item["file_id"], message_thread_id=thread_id)
                    else:
                        await bot.send_document(group_id, item["file_id"], message_thread_id=thread_id)
                except Exception as e:
                    logging.error(f"Failed to send file to group thread: {e}")
                    
    except Exception as e:
        logging.error(f"Failed to send claim {claim['id']} to group {group_id}: {e}")
        # Пытаемся отправить админам напрямую
        for admin_id in ADMIN_CHAT_IDS:
            try:
                await bot.send_message(admin_id, text, reply_markup=claim_status_kb(claim["id"], is_group=False), parse_mode="HTML")
            except Exception as admin_err:
                logging.error(f"Failed to send claim to admin {admin_id}: {admin_err}")

