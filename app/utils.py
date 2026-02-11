import asyncio
import json
import logging
import os
from aiogram import Bot
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
    bot: Bot, db, claim: dict, files: list[dict], username: str | None, name: str | None, phone: str | None
) -> None:
    from app.keyboards import claim_status_kb

    products_info = ""
    if "из гарантии" in claim['purchase_type']:
        warranties = await db.get_warranties(claim['tg_id'])
        w = next((w for w in warranties if w['cz_code'] == claim['purchase_value']), None)
        if w and w.get('receipt_items'):
            products_info = f"\n**Товары в чеке:**\n{w['receipt_items']}"

    group_id_str = await db.get_setting("admin_group_id")
    if not group_id_str:
        if not ADMIN_CHAT_IDS:
            return

        text = (
            "🛠 Новая заявка\n"
            f"claim_id: {claim['id']}\n"
            f"дата: {claim['created_at']}\n"
            f"tg: {claim['tg_id']} @{username or '-'}\n"
            f"имя: {name or '-'}\n"
            f"телефон: {phone or '-'}\n"
            f"идентификатор: {claim['purchase_type']} / {claim['purchase_value']}\n"
            f"{products_info}\n"
            f"текст: {claim['description']}\n"
        )
        for admin_id in ADMIN_CHAT_IDS:
            try:
                await bot.send_message(admin_id, text, reply_markup=claim_status_kb(claim["id"]), parse_mode="Markdown")
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
    thread_id = await get_or_create_user_thread(bot, db, claim["tg_id"])
    if not thread_id:
        return

    text = (
        "🛠 **Новая заявка**\n"
        f"ID: `{claim['id']}`\n"
        f"Дата: {claim['created_at']}\n"
        f"Идентификатор: {claim['purchase_type']} / {claim['purchase_value']}\n"
        f"{products_info}\n"
        f"**Текст проблемы:**\n{claim['description']}"
    )
    
    group_msg = await bot.send_message(
        group_id, 
        text, 
        message_thread_id=thread_id, 
        reply_markup=claim_status_kb(claim["id"], is_group=True),
        parse_mode="Markdown"
    )
    
    try:
        await bot.pin_chat_message(group_id, group_msg.message_id)
    except Exception as e:
        logging.warning(f"Failed to pin message in group: {e}")
    
    await db.update_claim_group_message(claim["id"], group_msg.message_id)
    
    clean_group_id = group_id_str.replace("-100", "")
    msg_link = f"https://t.me/c/{clean_group_id}/{group_msg.message_id}"
    
    private_text = (
        f"🛠 **Новая заявка {claim['id']}**\n"
        f"Пользователь: @{username or '-'}\n"
        f"Ссылка: {msg_link}"
    )
    
    for admin_id in ADMIN_CHAT_IDS:
        try:
            await bot.send_message(admin_id, private_text, reply_markup=claim_status_kb(claim["id"], is_group=False, group_link=msg_link), parse_mode="Markdown")
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

