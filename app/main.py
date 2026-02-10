import asyncio
import datetime as dt
import io
import json
import logging
import os
import uuid
from typing import Any

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from app.constants import CARE_TEXT, FAQ_ITEMS, MAIN_MENU, TRUST_TEXT, WARRANTY_LEGAL_TEXT
from app.db import Database
from app.scanner import extract_datamatrix
from app.receipt_parser import ReceiptParser


DB_PATH = os.getenv("DB_PATH", "data/data.db")
ADMIN_CHAT_IDS_RAW = os.getenv("ADMIN_CHAT_IDS", "")
ADMIN_CHAT_IDS = [
    int(item.strip())
    for item in ADMIN_CHAT_IDS_RAW.replace(";", ",").split(",")
    if item.strip().isdigit()
]
CATALOG_URL = os.getenv("CATALOG_URL", "https://example.com/catalog")
WB_URL = os.getenv("WB_URL", "https://www.wildberries.ru/")
TG_CHANNEL_URL = os.getenv("TG_CHANNEL_URL", "https://t.me/your_channel")
CERTS_URL = os.getenv("CERTS_URL", "https://example.com/certs")
FAQ_URL = os.getenv("FAQ_URL", "https://example.com/faq")

db = Database(DB_PATH)


class ClaimStates(StatesGroup):
    description = State()
    purchase_type = State()
    purchase_wb = State()
    purchase_cz_photo = State()
    files = State()
    contact_name = State()
    contact_phone = State()


class AdminStates(StatesGroup):
    reply_text = State()
    kb_edit_text = State()
    kb_edit_links = State()
    kb_add_link_label = State()
    kb_add_link_url = State()
    kb_edit_link_label = State()
    kb_edit_link_url = State()


KB_JSON_PATH = "kb.json"

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


class WarrantyStates(StatesGroup):
    cz_photo = State()
    receipt_pdf = State()
    sku = State()
    name = State()


def main_menu_kb() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text=MAIN_MENU[0], callback_data="menu:warranty"),
        ],
        [
            InlineKeyboardButton(text=MAIN_MENU[1], callback_data="menu:claim"),
        ],
        [
            InlineKeyboardButton(text=MAIN_MENU[2], callback_data="menu:claims"),
            InlineKeyboardButton(text=MAIN_MENU[3], callback_data="menu:shop"),
        ],
        [
            InlineKeyboardButton(text=MAIN_MENU[4], callback_data="menu:care"),
            InlineKeyboardButton(text=MAIN_MENU[5], callback_data="menu:useful"),
        ],
        [
            InlineKeyboardButton(text=MAIN_MENU[6], callback_data="menu:trust"),
            InlineKeyboardButton(text=MAIN_MENU[7], callback_data="menu:faq"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def purchase_type_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Чек WB", callback_data="purchase:wb"),
                InlineKeyboardButton(text="Честный знак", callback_data="purchase:cz"),
            ],
            [InlineKeyboardButton(text="Отмена", callback_data="cancel")],
        ]
    )


def files_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Готово", callback_data="files:done"),
                InlineKeyboardButton(text="Отмена", callback_data="cancel"),
            ]
        ]
    )


def skip_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Пропустить", callback_data="skip:phone"),
                InlineKeyboardButton(text="Отмена", callback_data="cancel"),
            ]
        ]
    )


def claim_status_kb(claim_id: str, status: str = "Новая", group_link: str | None = None) -> InlineKeyboardMarkup:
    # Toggle logic for "Нужны уточнения" or "Решено"
    # Removed "В работу" as requested
    rows = []
    
    btn_clarify = InlineKeyboardButton(
        text="❓ Нужны уточнения" if status != "Нужны уточнения" else "✅ Нужны уточнения (активно)",
        callback_data=f"status:{claim_id}:Нужны уточнения"
    )
    btn_resolved = InlineKeyboardButton(
        text="🟢 Решено" if status != "Решено" else "✅ Решено (активно)",
        callback_data=f"status:{claim_id}:Решено"
    )
    
    rows.append([btn_clarify])
    rows.append([btn_resolved])
    
    if group_link:
        rows.append([InlineKeyboardButton(text="➡️ Перейти к заявке", url=group_link)])
    
    return InlineKeyboardMarkup(inline_keyboard=rows)


def claims_list_kb(claims: list[dict], group_id: str | None = None, filter_type: str = "all", page: int = 0, total_count: int = 0, limit: int = 20) -> InlineKeyboardMarkup:
    rows = []
    for item in claims:
        # Status icon
        status_icon = "🆕" if item['status'] == "Новая" else "🛠" if item['status'] == "В работе" else "🟢" if item['status'] == "Решено" else "❓"
        
        # Link to topic or specific message if possible
        topic_link = ""
        if group_id:
            clean_group_id = group_id.replace("-100", "")
            if item.get("group_message_id"):
                topic_link = f"https://t.me/c/{clean_group_id}/{item['group_message_id']}"
            elif item.get("thread_id"):
                topic_link = f"https://t.me/c/{clean_group_id}/{item['thread_id']}"
        
        btn_text = f"{status_icon} {item['id']} — {item['status']}"
        
        row = [InlineKeyboardButton(text=btn_text, callback_data=f"claim:{item['id']}")]
        if topic_link:
            row.append(InlineKeyboardButton(text="➡️ Перейти", url=topic_link))
        
        rows.append(row)
    
    # Pagination buttons
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"admin:list_claims:{filter_type}:{page-1}"))
    
    if (page + 1) * limit < total_count:
        nav_row.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=f"admin:list_claims:{filter_type}:{page+1}"))
    
    if nav_row:
        rows.append(nav_row)
    
    rows.append([InlineKeyboardButton(text="❌ Закрыть", callback_data="cancel")])
        
    return InlineKeyboardMarkup(inline_keyboard=rows)


def link_kb(label: str, url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=label, url=url)]]
    )


def cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data="cancel")]]
    )


def warranties_selection_kb(warranties: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for w in warranties:
        # Show SKU and a bit of CZ code
        sku = w.get("sku") or "Без артикула"
        cz = w.get("cz_code") or ""
        display_cz = (cz[:10] + "...") if len(cz) > 10 else cz
        rows.append([
            InlineKeyboardButton(
                text=f"📦 {sku} ({display_cz})",
                callback_data=f"select_w:{w['id']}"
            )
        ])
    rows.append([InlineKeyboardButton(text="Другой (через Чек/ЧЗ)", callback_data="select_w:other")])
    rows.append([InlineKeyboardButton(text="Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_ours_tokens() -> list[str]:
    ours_raw = os.getenv("OUR_CODES", "")
    return [item.strip() for item in ours_raw.replace(";", ",").split(",") if item.strip()]

async def upsert_from_user(user) -> None:
    await db.upsert_user(user.id, user.username, None)

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


async def get_or_create_user_thread(bot: Bot, user_id: int) -> int | None:
    user = await db.get_user(user_id)
    if not user:
        return None
    
    group_id_str = await db.get_setting("admin_group_id")
    if not group_id_str:
        return None
    
    group_id = int(group_id_str)
    
    if user.get("thread_id"):
        return user["thread_id"]
    
    # Create new thread
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
    bot: Bot, claim: dict, files: list[dict], username: str | None, name: str | None, phone: str | None
) -> None:
    # Try to find products info from warranty if applicable
    products_info = ""
    if "из гарантии" in claim['purchase_type']:
        warranties = await db.get_warranties(claim['tg_id'])
        # Find specific warranty by CZ code
        w = next((w for w in warranties if w['cz_code'] == claim['purchase_value']), None)
        if w and w.get('receipt_items'):
            products_info = f"\n**Товары в чеке:**\n{w['receipt_items']}"

    group_id_str = await db.get_setting("admin_group_id")
    if not group_id_str:
        # Fallback to private messages if group not set
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
        # ... rest of files ...

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

    # Supergroup logic
    group_id = int(group_id_str)
    thread_id = await get_or_create_user_thread(bot, claim["tg_id"])
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
        reply_markup=claim_status_kb(claim["id"]),
        parse_mode="Markdown"
    )
    
    # Store message_id for linking
    await db.update_claim_group_message(claim["id"], group_msg.message_id)
    
    # Send link to admins in private if they are set
    clean_group_id = group_id_str.replace("-100", "")
    msg_link = f"https://t.me/c/{clean_group_id}/{group_msg.message_id}"
    
    private_text = (
        f"🛠 **Новая заявка {claim['id']}**\n"
        f"Пользователь: @{username or '-'}\n"
        f"Ссылка: {msg_link}"
    )
    
    for admin_id in ADMIN_CHAT_IDS:
        try:
            # We don't send full text to private if group is available, just a link/notification
            await bot.send_message(admin_id, private_text, reply_markup=claim_status_kb(claim["id"], group_link=msg_link), parse_mode="Markdown")
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


async def attach_clarification(message: Message, bot: Bot, state: FSMContext) -> bool:
    current_state = await state.get_state()
    if current_state:
        return False
    
    if message.text and message.text.startswith("/"):
        return False
        
    if message.text and (message.text in MAIN_MENU or message.text in {
        "Отмена",
        "Готово",
        "Пропустить",
        "Чек WB",
        "Честный знак",
    }):
        return False

    await upsert_from_user(message.from_user)

    # Check for any open claim (not "Решено")
    claim = await db.get_last_claim_by_status(message.from_user.id, "Нужны уточнения") or \
            await db.get_last_claim_by_status(message.from_user.id, "В работе") or \
            await db.get_last_claim_by_status(message.from_user.id, "Новая")
    
    if not claim:
        return False

    logging.info(f"Processing clarification from user {message.from_user.id} for claim {claim['id']}")

    # Check if user has a thread
    group_id_str = await db.get_setting("admin_group_id")
    if not group_id_str:
        # Old logic fallback: private messages to admins
        text = message.text or ""
        if text:
            await db.add_claim_note(claim["id"], "user", text)

        if message.photo:
            await db.add_claim_file(claim["id"], message.photo[-1].file_id, "photo")
        elif message.video:
            await db.add_claim_file(claim["id"], message.video.file_id, "video")
        elif message.document:
            await db.add_claim_file(claim["id"], message.document.file_id, "document")

        if ADMIN_CHAT_IDS:
            for admin_id in ADMIN_CHAT_IDS:
                try:
                    await bot.send_message(
                        admin_id,
                        f"Получены уточнения по заявке {claim['id']} от пользователя {message.from_user.id}",
                    )
                    if text:
                        await bot.send_message(admin_id, f"Текст уточнения: {text}")
                    if message.photo:
                        await bot.send_photo(admin_id, message.photo[-1].file_id)
                    elif message.video:
                        await bot.send_video(admin_id, message.video.file_id)
                    elif message.document:
                        await bot.send_document(admin_id, message.document.file_id)
                except Exception as e:
                    logging.error(f"Failed to send clarification to admin {admin_id}: {e}")

        await message.answer("Спасибо! Уточнения добавлены к заявке.", reply_markup=main_menu_kb())
        return True

    # Supergroup logic: forward to the thread
    group_id = int(group_id_str)
    thread_id = await get_or_create_user_thread(bot, message.from_user.id)
    if not thread_id:
        logging.warning(f"Could not get or create thread for user {message.from_user.id}")
        return False

    try:
        await message.copy_to(group_id, message_thread_id=thread_id)
        logging.info(f"Forwarded message from {message.from_user.id} to thread {thread_id}")
        
        # Store in DB as note/file
        if message.text:
            await db.add_claim_note(claim["id"], "user", message.text)
        if message.photo:
            await db.add_claim_file(claim["id"], message.photo[-1].file_id, "photo")
        elif message.video:
            await db.add_claim_file(claim["id"], message.video.file_id, "video")
        elif message.document:
            await db.add_claim_file(claim["id"], message.document.file_id, "document")
    except Exception as e:
        logging.error(f"Failed to forward message to thread: {e}")

    return True

async def admin_group_reply_handler(message: Message, bot: Bot) -> None:
    # Handler for messages in the supergroup
    group_id_str = await db.get_setting("admin_group_id")
    if not group_id_str or str(message.chat.id) != group_id_str:
        return

    if not message.message_thread_id:
        return

    logging.info(f"Received message in admin group thread {message.message_thread_id}")

    # Find user by thread_id
    user = await db.get_user_by_thread(message.message_thread_id)
    if not user:
        logging.warning(f"No user found for thread {message.message_thread_id}")
        return

    # Don't forward commands
    if message.text and message.text.startswith("/"):
        return

    try:
        await message.copy_to(user["tg_id"])
        
        # Also log as manager note if there's an active claim
        claim = await db.get_last_claim_by_status(user["tg_id"], "Нужны уточнения") or \
                await db.get_last_claim_by_status(user["tg_id"], "В работе") or \
                await db.get_last_claim_by_status(user["tg_id"], "Новая")
        if claim and message.text:
            await db.add_claim_note(claim["id"], "manager", message.text)
    except Exception as e:
        logging.error(f"Failed to forward message from thread to user: {e}")


def admin_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📋 Все заявки", callback_data="admin:list_claims:all")],
            [InlineKeyboardButton(text="📨 Новые заявки", callback_data="admin:list_claims:new")],
            [InlineKeyboardButton(text="📚 База знаний", callback_data="admin:kb_menu")],
            [InlineKeyboardButton(text="❌ Закрыть", callback_data="cancel")]
        ]
    )


async def admin_handler(message: Message) -> None:
    if not ADMIN_CHAT_IDS or message.from_user.id not in ADMIN_CHAT_IDS:
        return
    
    group_id = await db.get_setting("admin_group_id")
    status = f"✅ Группа привязана: `{group_id}`" if group_id else "❌ Группа не привязана. Напишите /add в супергруппе."
    
    await message.answer(
        f"Панель администратора:\n\n{status}", 
        reply_markup=admin_menu_kb(),
        parse_mode="Markdown"
    )


async def admin_add_group_handler(message: Message, bot: Bot) -> None:
    if not ADMIN_CHAT_IDS or message.from_user.id not in ADMIN_CHAT_IDS:
        return
    
    if message.chat.type not in ["supergroup", "group"]:
        await message.answer("Эту команду нужно вызвать в супергруппе (с включенными темами).")
        return

    # Check if we can create topics
    try:
        test_topic = await bot.create_forum_topic(message.chat.id, "🔍 Тест прав бота")
        await bot.close_forum_topic(message.chat.id, test_topic.message_thread_id)
        await bot.delete_forum_topic(message.chat.id, test_topic.message_thread_id)
    except Exception as e:
        await message.answer(
            f"❌ Ошибка прав: Бот не может управлять темами в этой группе.\n"
            f"Убедитесь, что темы включены в настройках группы, и бот назначен администратором с правом «Управление темами».\n\n"
            f"Техническая ошибка: `{e}`"
        )
        return

    await db.set_setting("admin_group_id", str(message.chat.id))
    await message.answer(f"✅ Эта группа ({message.chat.title}) теперь успешно привязана для обработки заявок.")


async def admin_list_claims_handler(callback: CallbackQuery) -> None:
    if not ADMIN_CHAT_IDS or callback.from_user.id not in ADMIN_CHAT_IDS:
        await callback.answer("Недостаточно прав")
        return
    
    parts = callback.data.split(":")
    filter_type = parts[2] if len(parts) > 2 else "all"
    page = int(parts[3]) if len(parts) > 3 else 0
    limit = 20
    offset = page * limit
    
    group_id = await db.get_setting("admin_group_id")
    
    status_filter = "Новая" if filter_type == "new" else None
    claims = await db.list_claims_with_threads(status=status_filter, limit=limit, offset=offset)
    total_count = await db.count_claims(status=status_filter)
    
    title = "Вот ваши заявки"
    
    if not claims:
        if page == 0:
            await callback.message.edit_text("Заявок не найдено.", reply_markup=admin_menu_kb())
        else:
            await callback.answer("Больше заявок нет.")
        return

    await callback.message.edit_text(
        title, 
        reply_markup=claims_list_kb(claims, group_id, filter_type, page, total_count, limit)
    )
    await callback.answer()


async def admin_kb_menu_handler(callback: CallbackQuery) -> None:
    if not ADMIN_CHAT_IDS or callback.from_user.id not in ADMIN_CHAT_IDS:
        await callback.answer("Недостаточно прав")
        return
    
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🧼 Уход за изделием", callback_data="admin:kb_edit:care")],
            [InlineKeyboardButton(text="🛡 Доверие", callback_data="admin:kb_edit:trust")],
            [InlineKeyboardButton(text="📘 Полезное", callback_data="admin:kb_edit:useful")],
            [InlineKeyboardButton(text="❓ FAQ", callback_data="admin:kb_edit:faq")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin:menu")],
        ]
    )
    await callback.message.edit_text("Выберите раздел базы знаний для редактирования:", reply_markup=kb)
    await callback.answer()


async def admin_kb_edit_handler(callback: CallbackQuery, state: FSMContext) -> None:
    if not ADMIN_CHAT_IDS or callback.from_user.id not in ADMIN_CHAT_IDS:
        await callback.answer("Недостаточно прав")
        return
    
    section = callback.data.split(":")[2]
    await state.update_data(kb_section=section)
    
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📝 Изменить текст", callback_data=f"admin:kb_edit_text:{section}")],
            [InlineKeyboardButton(text="🔗 Управление ссылками", callback_data=f"admin:kb_links:{section}")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin:kb_menu")],
        ]
    )
    
    kb_data = load_kb()
    current_text = kb_data.get(section, "Текст не задан")
    
    await callback.message.edit_text(
        f"Раздел: {section}\n\n"
        f"Текущий текст:\n---\n{current_text}\n---\n\n"
        "Что вы хотите изменить?",
        reply_markup=kb
    )
    await callback.answer()


async def admin_kb_text_edit_start(callback: CallbackQuery, state: FSMContext) -> None:
    section = callback.data.split(":")[2]
    await state.set_state(AdminStates.kb_edit_text)
    await callback.message.edit_text(
        f"Отправьте новый текст для раздела '{section}' (поддерживается Markdown).",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data=f"admin:kb_edit:{section}")]])
    )
    await callback.answer()


async def admin_kb_links_menu_handler(callback: CallbackQuery, state: FSMContext) -> None:
    section = callback.data.split(":")[2]
    await state.update_data(kb_section=section)
    
    kb_data = load_kb()
    links = kb_data.get("links", {}).get(section, DEFAULT_KB["links"].get(section, []))
    
    rows = []
    for i, l in enumerate(links):
        rows.append([
            InlineKeyboardButton(text=f"✏️ {l['label']}", callback_data=f"admin:kb_link_edit:{section}:{i}"),
            InlineKeyboardButton(text="❌", callback_data=f"admin:kb_link_del:{section}:{i}")
        ])
    
    rows.append([InlineKeyboardButton(text="➕ Добавить ссылку", callback_data=f"admin:kb_link_add:{section}")])
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data=f"admin:kb_edit:{section}")])
    
    await callback.message.edit_text(
        f"Управление ссылками раздела: {section}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )
    await callback.answer()


async def admin_kb_link_add_start(callback: CallbackQuery, state: FSMContext) -> None:
    section = callback.data.split(":")[2]
    await state.set_state(AdminStates.kb_add_link_label)
    await callback.message.edit_text(
        "Введите название для новой ссылки:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data=f"admin:kb_links:{section}")]])
    )
    await callback.answer()


async def admin_kb_link_add_label(message: Message, state: FSMContext) -> None:
    await state.update_data(new_link_label=message.text)
    await state.set_state(AdminStates.kb_add_link_url)
    data = await state.get_data()
    await message.answer(
        f"Теперь введите URL для ссылки '{message.text}':",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data=f"admin:kb_links:{data['kb_section']}")]])
    )


async def admin_kb_link_add_url(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    section = data["kb_section"]
    label = data["new_link_label"]
    url = message.text
    
    if not (url.startswith("http://") or url.startswith("https://") or url.startswith("t.me/")):
        await message.answer("❌ Ошибка: URL должен начинаться с http://, https:// или t.me/")
        return

    kb_data = load_kb()
    if "links" not in kb_data: kb_data["links"] = {}
    if section not in kb_data["links"]: kb_data["links"][section] = list(DEFAULT_KB["links"].get(section, []))
    
    kb_data["links"][section].append({"label": label, "url": url})
    save_kb(kb_data)
    
    await state.clear()
    await message.answer(f"✅ Ссылка '{label}' добавлена!", reply_markup=admin_menu_kb())


async def admin_kb_link_del_handler(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    section = parts[2]
    idx = int(parts[3])
    
    kb_data = load_kb()
    if "links" in kb_data and section in kb_data["links"]:
        if 0 <= idx < len(kb_data["links"][section]):
            del kb_data["links"][section][idx]
            save_kb(kb_data)
            await callback.answer("✅ Ссылка удалена")
        else:
            await callback.answer("❌ Ошибка индекса")
    else:
        # If it was in default, we need to copy default first
        kb_data["links"][section] = list(DEFAULT_KB["links"].get(section, []))
        if 0 <= idx < len(kb_data["links"][section]):
            del kb_data["links"][section][idx]
            save_kb(kb_data)
            await callback.answer("✅ Ссылка удалена")
            
    await admin_kb_links_menu_handler(callback, state)


async def admin_kb_link_edit_start(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    section = parts[2]
    idx = int(parts[3])
    
    kb_data = load_kb()
    links = kb_data.get("links", {}).get(section, DEFAULT_KB["links"].get(section, []))
    link = links[idx]
    
    await state.update_data(kb_section=section, edit_link_idx=idx)
    await state.set_state(AdminStates.kb_edit_link_label)
    
    await callback.message.edit_text(
        f"Редактирование ссылки: {link['label']}\nURL: {link['url']}\n\nВведите новое название (или отправьте то же самое):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data=f"admin:kb_links:{section}")]])
    )
    await callback.answer()


async def admin_kb_link_edit_label(message: Message, state: FSMContext) -> None:
    await state.update_data(edit_link_label=message.text)
    await state.set_state(AdminStates.kb_edit_link_url)
    data = await state.get_data()
    await message.answer(
        f"Введите новый URL для ссылки '{message.text}':",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data=f"admin:kb_links:{data['kb_section']}")]])
    )


async def admin_kb_link_edit_url(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    section = data["kb_section"]
    idx = data["edit_link_idx"]
    label = data["edit_link_label"]
    url = message.text
    
    if not (url.startswith("http://") or url.startswith("https://") or url.startswith("t.me/")):
        await message.answer("❌ Ошибка: URL должен начинаться с http://, https:// или t.me/")
        return

    kb_data = load_kb()
    if "links" not in kb_data: kb_data["links"] = {}
    if section not in kb_data["links"]: kb_data["links"][section] = list(DEFAULT_KB["links"].get(section, []))
    
    kb_data["links"][section][idx] = {"label": label, "url": url}
    save_kb(kb_data)
    
    await state.clear()
    await message.answer(f"✅ Ссылка обновлена!", reply_markup=admin_menu_kb())


async def admin_kb_save_handler(message: Message, state: FSMContext) -> None:
    if not ADMIN_CHAT_IDS or message.from_user.id not in ADMIN_CHAT_IDS:
        return
    
    data = await state.get_data()
    section = data.get("kb_section")
    if not section:
        await state.clear()
        return
    
    kb_data = load_kb()
    kb_data[section] = message.text
    save_kb(kb_data)
    
    await state.clear()
    await message.answer(f"✅ Текст для раздела '{section}' успешно обновлен!", reply_markup=admin_menu_kb())


async def admin_menu_callback_handler(callback: CallbackQuery) -> None:
    if not ADMIN_CHAT_IDS or callback.from_user.id not in ADMIN_CHAT_IDS:
        await callback.answer("Недостаточно прав")
        return
    
    group_id = await db.get_setting("admin_group_id")
    status = f"✅ Группа привязана: `{group_id}`" if group_id else "❌ Группа не привязана. Напишите /add в супергруппе."
    
    await callback.message.edit_text(
        f"Панель администратора:\n\n{status}", 
        reply_markup=admin_menu_kb(),
        parse_mode="Markdown"
    )
    await callback.answer()


async def start_handler(message: Message) -> None:
    await upsert_from_user(message.from_user)
    has_warranty = await db.has_warranty(message.from_user.id)
    text = "Добро пожаловать! Выберите действие из меню."
    if not has_warranty:
        text = (
            "Добро пожаловать! 👋\n\n"
            "Зарегистрируйтесь сейчас и получите **гарантию на 12 месяцев** на ваше изделие!\n\n"
            "Выберите действие из меню."
        )
    await message.answer(
        text,
        reply_markup=main_menu_kb(),
        parse_mode="Markdown"
    )


async def cancel_handler(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=main_menu_kb())


async def forget_me_handler(message: Message, state: FSMContext) -> None:
    await state.clear()
    await db.delete_user_data(message.from_user.id)
    await message.answer("Все ваши данные были удалены из базы данных.", reply_markup=main_menu_kb())


async def cancel_callback_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()
    await callback.message.answer("Действие отменено.", reply_markup=main_menu_kb())


async def claims_menu_handler(message: Message) -> None:
    claims = await db.list_claims_by_user(message.from_user.id, limit=5)
    if not claims:
        await message.answer("У вас пока нет заявок.", reply_markup=main_menu_kb())
        return
    await message.answer("Ваши последние заявки:", reply_markup=claims_list_kb(claims))


async def claims_menu_callback_handler(callback: CallbackQuery) -> None:
    await upsert_from_user(callback.from_user)
    claims = await db.list_claims_by_user(callback.from_user.id, limit=5)
    await callback.answer()
    if not claims:
        await callback.message.answer("У вас пока нет заявок.", reply_markup=main_menu_kb())
        return
    await callback.message.answer("Ваши последние заявки:", reply_markup=claims_list_kb(claims))


async def shopping_callback_handler(callback: CallbackQuery) -> None:
    await callback.answer()
    await shopping_handler(callback.message)


async def care_callback_handler(callback: CallbackQuery) -> None:
    await callback.answer()
    await care_handler(callback.message)


async def useful_callback_handler(callback: CallbackQuery) -> None:
    await callback.answer()
    await useful_handler(callback.message)


async def trust_callback_handler(callback: CallbackQuery) -> None:
    await callback.answer()
    await trust_handler(callback.message)


async def faq_callback_handler(callback: CallbackQuery) -> None:
    await callback.answer()
    await faq_handler(callback.message)


async def claim_details_handler(callback: CallbackQuery) -> None:
    claim_id = callback.data.split(":", 1)[1]
    claim = await db.get_claim(claim_id)
    if not claim:
        await callback.answer("Заявка не найдена")
        return
    
    # Try to find products info
    products_info = ""
    if "из гарантии" in claim['purchase_type']:
        warranties = await db.get_warranties(claim['tg_id'])
        w = next((w for w in warranties if w['cz_code'] == claim['purchase_value']), None)
        if w and w.get('receipt_items'):
            products_info = f"\n**Товары в чеке:**\n{w['receipt_items']}"

    text = (
        f"🛠 **Заявка {claim['id']}**\n"
        f"Статус: {claim['status']}\n"
        f"Идентификатор: {claim['purchase_type']} / {claim['purchase_value']}\n"
        f"{products_info}\n"
        f"**Текст проблемы:**\n{claim['description']}"
    )
    
    # Check if admin
    is_admin = ADMIN_CHAT_IDS and callback.from_user.id in ADMIN_CHAT_IDS
    kb = claim_status_kb(claim['id'], claim['status']) if is_admin else main_menu_kb()
    
    await callback.message.answer(text, reply_markup=kb, parse_mode="Markdown")
    await callback.answer()


async def comment_handler(message: Message) -> None:
    if not ADMIN_CHAT_IDS or message.from_user.id not in ADMIN_CHAT_IDS:
        return
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("Формат: /comment <claim_id> <текст>")
        return
    claim_id = parts[1]
    comment = parts[2]
    claim = await db.get_claim(claim_id)
    if not claim:
        await message.answer("Заявка не найдена")
        return
    await db.update_claim_comment(claim_id, comment)
    await message.answer("Комментарий сохранен.")
    await message.bot.send_message(
        claim["tg_id"],
        f"Комментарий менеджера по заявке {claim_id}:\n{comment}",
    )


async def admin_reply_callback_handler(callback: CallbackQuery, state: FSMContext) -> None:
    if not ADMIN_CHAT_IDS or callback.from_user.id not in ADMIN_CHAT_IDS:
        await callback.answer("Недостаточно прав")
        return
    
    claim_id = callback.data.split(":")[1]
    await state.update_data(reply_claim_id=claim_id)
    await state.set_state(AdminStates.reply_text)
    await callback.message.answer(f"Введите ответ на заявку {claim_id}:")
    await callback.answer()


async def admin_reply_text_handler(message: Message, state: FSMContext) -> None:
    if not ADMIN_CHAT_IDS or message.from_user.id not in ADMIN_CHAT_IDS:
        return
    
    data = await state.get_data()
    claim_id = data.get("reply_claim_id")
    if not claim_id:
        await state.clear()
        return

    claim = await db.get_claim(claim_id)
    if not claim:
        await message.answer("Заявка не найдена")
        await state.clear()
        return

    text = message.text
    await db.add_claim_note(claim_id, "manager", text)
    
    await message.bot.send_message(
        claim["tg_id"],
        f"📩 Ответ менеджера по заявке {claim_id}:\n\n{text}"
    )
    await message.answer(f"Ответ отправлен пользователю по заявке {claim_id}.")
    await state.clear()


async def status_callback_handler(callback: CallbackQuery) -> None:
    if not ADMIN_CHAT_IDS or callback.from_user.id not in ADMIN_CHAT_IDS:
        await callback.answer("Недостаточно прав")
        return
    _, claim_id, status = callback.data.split(":", 2)
    claim = await db.get_claim(claim_id)
    if not claim:
        await callback.answer("Заявка не найдена")
        return

    await db.update_claim_status(claim_id, status)
    
    # Update markup to reflect new state
    await callback.message.edit_reply_markup(reply_markup=claim_status_kb(claim_id, status))
    await callback.answer(f"Статус обновлен: {status}")

    if status == "Нужны уточнения":
        await callback.bot.send_message(
            claim["tg_id"],
            "По вашей заявке нужны уточнения. Пожалуйста, отправьте доп. текст/фото.",
        )
    elif status == "Решено":
        await callback.bot.send_message(
            claim["tg_id"], f"Заявка {claim_id} отмечена как решенная."
        )
    elif status == "В работе":
        await callback.bot.send_message(
            claim["tg_id"], f"Заявка {claim_id} принята в работу."
        )


async def claim_start_handler(message: Message, state: FSMContext) -> None:
    await upsert_from_user(message.from_user)
    has_warranty = await db.has_warranty(message.from_user.id)
    if not has_warranty:
        await message.answer(
            "Чтобы оформить заявку по гарантии, вам необходимо зарегистрироваться.\n"
            "Это обеспечит вам 12 месяцев гарантийного обслуживания.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="Зарегистрироваться", callback_data="menu:warranty")],
                    [InlineKeyboardButton(text="Отмена", callback_data="cancel")]
                ]
            )
        )
        return

    await state.set_state(ClaimStates.description)
    await message.answer("Опишите ситуацию текстом.", reply_markup=cancel_kb())


async def claim_start_callback_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await upsert_from_user(callback.from_user)
    
    has_warranty = await db.has_warranty(callback.from_user.id)
    if not has_warranty:
        await callback.message.answer(
            "Чтобы оформить заявку по гарантии, вам необходимо зарегистрироваться.\n"
            "Это обеспечит вам 12 месяцев гарантийного обслуживания.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="Зарегистрироваться", callback_data="menu:warranty")],
                    [InlineKeyboardButton(text="Отмена", callback_data="cancel")]
                ]
            )
        )
        return

    await state.set_state(ClaimStates.description)
    await callback.message.answer(
        "Опишите ситуацию текстом.",
        reply_markup=cancel_kb(),
    )


async def claim_description_handler(message: Message, state: FSMContext) -> None:
    await state.update_data(description=message.text)
    
    warranties = await db.get_warranties(message.from_user.id)
    if not warranties:
        # Fallback if somehow they got here without warranties
        await state.set_state(ClaimStates.purchase_type)
        await message.answer("Выберите идентификатор покупки:", reply_markup=purchase_type_kb())
        return

    if len(warranties) == 1:
        # Automatically use the only warranty
        w = warranties[0]
        await state.update_data(purchase_type="ЧЗ (из гарантии)", purchase_value=w["cz_code"])
        await state.set_state(ClaimStates.files)
        await state.update_data(files=[])
        await message.answer(
            f"Выбрано изделие: {w.get('sku') or 'Без артикула'}\n"
            "Пришлите фото/видео неисправности (если есть, до 5 файлов). Нажмите “Готово”, когда закончите.",
            reply_markup=files_kb(),
        )
    else:
        # Ask to select which one
        await state.set_state(ClaimStates.purchase_type) # reusing this state for selection
        await message.answer(
            "Выберите изделие, по которому подаете обращение:",
            reply_markup=warranties_selection_kb(warranties)
        )


async def claim_warranty_selection_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    data = callback.data
    if data == "select_w:other":
        await callback.message.answer("Выберите способ идентификации:", reply_markup=purchase_type_kb())
        return

    warranty_id = data.replace("select_w:", "")
    warranties = await db.get_warranties(callback.from_user.id)
    selected = next((w for w in warranties if w["id"] == warranty_id), None)
    
    if not selected:
        await callback.message.answer("Ошибка: изделие не найдено. Выберите другой способ.", reply_markup=purchase_type_kb())
        return

    await state.update_data(purchase_type="ЧЗ (из гарантии)", purchase_value=selected["cz_code"])
    await state.set_state(ClaimStates.files)
    await state.update_data(files=[])
    await callback.message.answer(
        f"Выбрано изделие: {selected.get('sku') or 'Без артикула'}\n"
        "Пришлите фото/видео неисправности (если есть, до 5 файлов). Нажмите “Готово”, когда закончите.",
        reply_markup=files_kb(),
    )


async def claim_purchase_type_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.data == "purchase:wb":
        await state.update_data(purchase_type="WB")
        await state.set_state(ClaimStates.purchase_wb)
        await callback.message.answer(
            "Отправьте фото чека WB.",
            reply_markup=cancel_kb(),
        )
        return
    if callback.data == "purchase:cz":
        await state.update_data(purchase_type="ЧЗ")
        await state.set_state(ClaimStates.purchase_cz_photo)
        await callback.message.answer(
            "Отправьте фото кода Честный знак.",
            reply_markup=cancel_kb(),
        )
        return


async def claim_purchase_wb_handler(message: Message, state: FSMContext) -> None:
    photo = message.photo[-1] if message.photo else None
    document = message.document if message.document else None
    if not photo and not document:
        await message.answer("Нужна фотография чека WB.")
        return

    file_id = photo.file_id if photo else document.file_id
    await state.update_data(purchase_value="WB чек (фото)")
    await state.set_state(ClaimStates.files)
    await state.update_data(files=[{"file_id": file_id, "file_type": "wb_receipt"}])
    await message.answer(
        "Чек получен. Пришлите фото/видео (если есть, до 5 файлов). Нажмите “Готово”, когда закончите.",
        reply_markup=files_kb(),
    )


async def claim_purchase_cz_handler(message: Message, state: FSMContext) -> None:
    photo = message.photo[-1] if message.photo else None
    document = message.document if message.document else None
    if not photo and not document:
        await message.answer("Нужна фотография Честного знака.", reply_markup=cancel_kb())
        return

    file_id = photo.file_id if photo else document.file_id
    
    status_msg = await message.answer("🔍 Распознаю код... Это может занять несколько минут.")
    
    file = await message.bot.get_file(file_id)
    buffer = io.BytesIO()
    await message.bot.download_file(file.file_path, destination=buffer)
    codes, is_ours = await decode_image(buffer.getvalue())
    
    try:
        await status_msg.delete()
    except Exception:
        pass

    if not codes:
        await message.answer(
            "Не удалось прочитать код. Попробуйте более четкое фото.",
            reply_markup=cancel_kb(),
        )
        return
    if not is_ours:
        await message.answer(
            f"Код не относится к нашей продукции: {codes[0]}\n"
            "Пожалуйста, отправьте корректный код ЧЗ.",
            reply_markup=cancel_kb(),
        )
        return
    cz_code = codes[0]
    await db.add_cz_code(message.from_user.id, cz_code)
    await state.update_data(purchase_value=cz_code)
    await state.set_state(ClaimStates.files)
    await state.update_data(files=[])
    await message.answer(
        "Расшифровка:\n"
        f"{format_decoded_codes(codes)}\n"
        "Пришлите фото/видео (если есть, до 5 файлов). Нажмите “Готово”, когда закончите.",
        reply_markup=files_kb(),
    )


async def claim_files_handler(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    files = data.get("files", [])

    file_id = None
    file_type = None
    if message.photo:
        file_id = message.photo[-1].file_id
        file_type = "photo"
    elif message.video:
        file_id = message.video.file_id
        file_type = "video"
    elif message.document:
        file_id = message.document.file_id
        file_type = "document"

    if not file_id:
        await message.answer("Отправьте фото/видео или нажмите “Готово”.")
        return

    if len(files) >= 5:
        await message.answer("Достигнут лимит 5 файлов. Нажмите “Готово”.")
        return

    files.append({"file_id": file_id, "file_type": file_type})
    await state.update_data(files=files)
    if len(files) == 5:
        await message.answer("Получено 5 файлов. Нажмите “Готово”.", reply_markup=files_kb())


async def claim_files_done_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    user = await db.get_user(callback.from_user.id)
    if user and user.get("name"):
        await state.set_state(ClaimStates.contact_phone)
        await callback.message.answer("Введите телефон (или нажмите “Пропустить”).", reply_markup=skip_kb())
    else:
        await state.set_state(ClaimStates.contact_name)
        await callback.message.answer(
            "Как к вам обращаться?",
            reply_markup=cancel_kb(),
        )


async def claim_contact_name_handler(message: Message, state: FSMContext) -> None:
    await db.upsert_user(message.from_user.id, message.from_user.username, message.text)
    await state.set_state(ClaimStates.contact_phone)
    await message.answer("Введите телефон (или нажмите “Пропустить”).", reply_markup=skip_kb())


async def claim_contact_phone_handler(message: Message, state: FSMContext) -> None:
    await finalize_claim(message, state, message.from_user, phone=message.text)


async def claim_skip_phone_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await finalize_claim(callback.message, state, callback.from_user, phone=None)


async def finalize_claim(message: Message, state: FSMContext, user: Any, phone: str | None) -> None:
    if phone:
        await db.update_user_phone(user.id, phone)

    data = await state.get_data()
    claim_id = uuid.uuid4().hex[:8]
    await db.create_claim(
        claim_id=claim_id,
        tg_id=user.id,
        description=data["description"],
        purchase_type=data["purchase_type"],
        purchase_value=data["purchase_value"],
    )

    for item in data.get("files", []):
        await db.add_claim_file(claim_id, item["file_id"], item["file_type"])

    user_db = await db.get_user(user.id)
    claim = await db.get_claim(claim_id)
    files = await db.get_claim_files(claim_id)
    await send_admin_claim(
        message.bot,
        claim,
        files,
        user.username,
        user_db.get("name") if user_db else None,
        user_db.get("phone") if user_db else None,
    )

    await message.answer(
        f"Заявка принята! Номер: {claim_id}",
        reply_markup=main_menu_kb(),
    )
    await state.clear()


async def start_warranty_activation(message: Message, state: FSMContext) -> None:
    await state.set_state(WarrantyStates.cz_photo)
    await message.answer(
        "🔐 Активируйте расширенную гарантию 12 месяцев.\n"
        "Отправьте фото кода Честный знак.",
        reply_markup=cancel_kb(),
    )


async def warranty_start_handler(message: Message, state: FSMContext) -> None:
    await upsert_from_user(message.from_user)
    warranties = await db.get_warranties(message.from_user.id)
    if warranties:
        text = "Ваши активные гарантии:\n\n"
        for w in warranties:
            end_date = w['end_date']
            try:
                end_date = dt.date.fromisoformat(end_date).strftime("%d.%m.%Y")
            except: pass
            text += f"📦 **{w.get('sku', 'Изделие')}**\nДо: {end_date}\nКод: `{w['cz_code'][:15]}...`\n\n"
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Активировать еще", callback_data="warranty:new")],
            [InlineKeyboardButton(text="Отмена", callback_data="cancel")]
        ])
        await message.answer(text, reply_markup=kb, parse_mode="Markdown")
    else:
        await start_warranty_activation(message, state)


async def warranty_start_callback_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await upsert_from_user(callback.from_user)
    warranties = await db.get_warranties(callback.from_user.id)
    
    if warranties:
        text = "Ваши активные гарантии:\n\n"
        for w in warranties:
            end_date = w['end_date']
            try:
                end_date = dt.date.fromisoformat(end_date).strftime("%d.%m.%Y")
            except: pass
            text += f"📦 **{w.get('sku', 'Изделие')}**\nДо: {end_date}\nКод: `{w['cz_code'][:15]}...`\n\n"
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Активировать еще", callback_data="warranty:new")],
            [InlineKeyboardButton(text="Отмена", callback_data="cancel")]
        ])
        await callback.message.answer(text, reply_markup=kb, parse_mode="Markdown")
    else:
        await start_warranty_activation(callback.message, state)


async def warranty_new_callback_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await start_warranty_activation(callback.message, state)


async def warranty_cz_handler(message: Message, state: FSMContext) -> None:
    photo = message.photo[-1] if message.photo else None
    document = message.document if message.document else None
    if not photo and not document:
        await message.answer("Нужна фотография Честного знака.", reply_markup=cancel_kb())
        return

    file_id = photo.file_id if photo else document.file_id
    
    status_msg = await message.answer("🔍 Распознаю код... Это может занять несколько секунд.")
    
    try:
        file = await message.bot.get_file(file_id)
        buffer = io.BytesIO()
        try:
            await asyncio.wait_for(message.bot.download_file(file.file_path, destination=buffer), timeout=30)
        except asyncio.TimeoutError:
            await message.answer("⚠️ Ошибка: Время ожидания истекло. Пожалуйста, попробуйте отправить фото еще раз.", reply_markup=cancel_kb())
            return
        except Exception as e:
            logging.error(f"Download error: {e}")
            await message.answer("⚠️ Произошла ошибка при загрузке фото. Попробуйте еще раз.", reply_markup=cancel_kb())
            return
            
        codes, is_ours = await decode_image(buffer.getvalue())
    finally:
        try:
            await status_msg.delete()
        except Exception:
            pass

    if not codes:
        await message.answer(
            "Не удалось прочитать код. Попробуйте более четкое фото.",
            reply_markup=cancel_kb(),
        )
        return
    if not is_ours:
        await message.answer(
            f"Код не относится к нашей продукции: {codes[0]}\n"
            "Пожалуйста, отправьте корректный код ЧЗ.",
            reply_markup=cancel_kb(),
        )
        return
    cz_code = codes[0]
    await state.update_data(cz_code=cz_code, cz_file_id=file_id)
    await state.set_state(WarrantyStates.receipt_pdf)
    await message.answer(
        "Код принят! ✅\n"
        "Теперь, пожалуйста, отправьте чек с WB в формате PDF.",
        reply_markup=cancel_kb(),
    )


async def warranty_receipt_handler(message: Message, state: FSMContext) -> None:
    if not message.document or not message.document.file_name.lower().endswith(".pdf"):
        await message.answer("Пожалуйста, отправьте чек в формате PDF.", reply_markup=cancel_kb())
        return

    file_id = message.document.file_id
    
    status_msg = await message.answer("📄 Обрабатываю чек... Это займет мгновение.")
    
    try:
        file = await message.bot.get_file(file_id)
        
        # Create data directory if it doesn't exist
        os.makedirs("data", exist_ok=True)
        temp_path = f"data/temp_{file_id}.pdf"
        
        try:
            await asyncio.wait_for(message.bot.download_file(file.file_path, destination=temp_path), timeout=60)
        except asyncio.TimeoutError:
            await message.answer("⚠️ Ошибка: Файл слишком долго загружается. Попробуйте еще раз.", reply_markup=cancel_kb())
            return
        
        receipt_date = None
        receipt_text = None
        receipt_items = None
        try:
            parser = ReceiptParser()
            receipt_data = parser.parse_pdf(temp_path)
            receipt_date = receipt_data.date
            receipt_text = receipt_data.raw_text
            
            # Render items list
            from app.receipt_parser import render_items
            receipt_items = render_items(receipt_data.items)
        except Exception as e:
            logging.error(f"Error parsing PDF: {e}")
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
    finally:
        try:
            await status_msg.delete()
        except Exception:
            pass

    await state.update_data(
        receipt_file_id=file_id, 
        receipt_date=receipt_date,
        receipt_text=receipt_text,
        receipt_items=receipt_items
    )
    await state.set_state(WarrantyStates.sku)
    await message.answer(
        "Чек получен! ✅\n"
        "Теперь введите артикул товара.",
        reply_markup=cancel_kb(),
    )


async def warranty_sku_handler(message: Message, state: FSMContext) -> None:
    if not message.text:
        await message.answer("Пожалуйста, введите артикул товара текстом.", reply_markup=cancel_kb())
        return
    
    await state.update_data(sku=message.text)
    
    user = await db.get_user(message.from_user.id)
    if user and user.get("name"):
        await finalize_warranty(message, state, user["name"])
        return

    await state.set_state(WarrantyStates.name)
    await message.answer(
        "Как к вам обращаться?",
        reply_markup=cancel_kb(),
    )


async def warranty_name_handler(message: Message, state: FSMContext) -> None:
    await db.upsert_user(message.from_user.id, message.from_user.username, message.text)
    await finalize_warranty(message, state, message.text)


async def finalize_warranty(message: Message, state: FSMContext, name: str) -> None:
    data = await state.get_data()
    warranty_id = uuid.uuid4().hex[:8]
    
    start_date, end_date = await db.create_warranty(
        warranty_id=warranty_id,
        tg_id=message.from_user.id,
        cz_code=data["cz_code"],
        cz_file_id=data["cz_file_id"],
        receipt_file_id=data["receipt_file_id"],
        sku=data["sku"],
        receipt_date=data["receipt_date"],
        receipt_text=data.get("receipt_text"),
        receipt_items=data.get("receipt_items")
    )
    
    # Format dates for user
    try:
        display_end_date = dt.date.fromisoformat(end_date).strftime("%d.%m.%Y")
    except:
        display_end_date = end_date

    await message.answer(
        f"✅ Регистрация завершена! Гарантия активирована.\n\n"
        f"📅 Гарантия действует до: **{display_end_date}**\n\n"
        f"{WARRANTY_LEGAL_TEXT}",
        reply_markup=main_menu_kb(),
        parse_mode="Markdown"
    )
    await state.clear()


async def shopping_handler(message: Message) -> None:
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Каталог", url=CATALOG_URL)],
            [InlineKeyboardButton(text="Wildberries", url=WB_URL)],
        ]
    )
    await message.answer("Выберите, куда перейти:", reply_markup=kb)


async def care_handler(message: Message) -> None:
    kb_data = load_kb()
    text = kb_data.get("care", CARE_TEXT)
    links = kb_data.get("links", {}).get("care", DEFAULT_KB["links"]["care"])
    
    rows = []
    for l in links:
        rows.append([InlineKeyboardButton(text=l["label"], url=l["url"])])
    
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


async def useful_handler(message: Message) -> None:
    kb_data = load_kb()
    text = kb_data.get("useful", "В Telegram мы делимся советами по защите от солнца, уходу за изделиями и новинками.")
    links = kb_data.get("links", {}).get("useful", DEFAULT_KB["links"]["useful"])
    
    rows = []
    for l in links:
        rows.append([InlineKeyboardButton(text=l["label"], url=l["url"])])
    rows.append([InlineKeyboardButton(text="На главную", callback_data="cancel")])
    
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


async def trust_handler(message: Message) -> None:
    kb_data = load_kb()
    text = kb_data.get("trust", TRUST_TEXT)
    links = kb_data.get("links", {}).get("trust", DEFAULT_KB["links"]["trust"])
    
    rows = []
    for l in links:
        rows.append([InlineKeyboardButton(text=l["label"], url=l["url"])])
    
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


async def faq_handler(message: Message) -> None:
    kb_data = load_kb()
    default_faq_text = "❓ FAQ\n\n" + "\n".join([f"• {q}\n  {a}" for q, a in FAQ_ITEMS])
    text = kb_data.get("faq", default_faq_text)
    links = kb_data.get("links", {}).get("faq", DEFAULT_KB["links"]["faq"])
    
    rows = []
    for l in links:
        rows.append([InlineKeyboardButton(text=l["label"], url=l["url"])])
    rows.append([InlineKeyboardButton(text="Задать вопрос", callback_data="faq:ask")])
    
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


async def faq_ask_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await upsert_from_user(callback.from_user)
    await state.set_state(ClaimStates.description)
    await callback.message.answer(
        "Опишите ситуацию текстом.",
        reply_markup=cancel_kb(),
    )


async def generic_photo_handler(message: Message, state: FSMContext) -> None:
    if await state.get_state():
        return
    claim = await db.get_last_claim_by_status(message.from_user.id, "Нужны уточнения")
    if claim:
        return
    file_id = message.photo[-1].file_id
    file = await message.bot.get_file(file_id)
    buffer = io.BytesIO()
    await message.bot.download_file(file.file_path, destination=buffer)
    codes, is_ours = await decode_image(buffer.getvalue())
    if not codes:
        await message.answer(
            "DataMatrix не найден. Попробуйте более четкое фото.",
            reply_markup=main_menu_kb(),
        )
        return
    suffix = "\nКод наш" if is_ours else "\nКод не наш"
    decoded = format_decoded_codes(codes)
    await message.answer(f"Расшифровка:\n{decoded}{suffix}", reply_markup=main_menu_kb())


async def unexpected_callback_handler(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state():
        await state.clear()
        await callback.answer()
        await callback.message.answer("Выберите действие из меню.", reply_markup=main_menu_kb())
        return
    await callback.answer()
    await callback.message.answer("Выберите действие из меню.", reply_markup=main_menu_kb())


async def unexpected_message_handler(message: Message, state: FSMContext) -> None:
    if await state.get_state():
        return
    await message.answer("Выберите действие из меню.", reply_markup=main_menu_kb())


async def unexpected_state_message_handler(message: Message, state: FSMContext) -> None:
    if not await state.get_state():
        return
    await state.clear()
    await message.answer("Выберите действие из меню.", reply_markup=main_menu_kb())


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN is required")

    await db.init()

    bot = Bot(token=token)
    dp = Dispatcher(storage=MemoryStorage())

    dp.message.register(start_handler, CommandStart())
    dp.message.register(admin_handler, Command("admin"))
    dp.message.register(admin_add_group_handler, Command("add"))
    dp.message.register(forget_me_handler, Command("forget_me"))
    dp.message.register(comment_handler, Command("comment"))

    dp.callback_query.register(status_callback_handler, F.data.startswith("status:"))
    dp.callback_query.register(admin_list_claims_handler, F.data.startswith("admin:list_claims:"))
    dp.callback_query.register(admin_kb_menu_handler, F.data == "admin:kb_menu")
    dp.callback_query.register(admin_kb_edit_handler, F.data.startswith("admin:kb_edit:"))
    dp.callback_query.register(admin_kb_text_edit_start, F.data.startswith("admin:kb_edit_text:"))
    dp.callback_query.register(admin_kb_links_menu_handler, F.data.startswith("admin:kb_links:"))
    dp.callback_query.register(admin_kb_link_add_start, F.data.startswith("admin:kb_link_add:"))
    dp.callback_query.register(admin_kb_link_del_handler, F.data.startswith("admin:kb_link_del:"))
    dp.callback_query.register(admin_kb_link_edit_start, F.data.startswith("admin:kb_link_edit:"))

    dp.message.register(admin_kb_save_handler, AdminStates.kb_edit_text)
    dp.message.register(admin_kb_link_add_label, AdminStates.kb_add_link_label)
    dp.message.register(admin_kb_link_add_url, AdminStates.kb_add_link_url)
    dp.message.register(admin_kb_link_edit_label, AdminStates.kb_edit_link_label)
    dp.message.register(admin_kb_link_edit_url, AdminStates.kb_edit_link_url)

    dp.callback_query.register(admin_menu_callback_handler, F.data == "admin:menu")
    dp.callback_query.register(admin_reply_callback_handler, F.data.startswith("reply:"))
    dp.message.register(admin_reply_text_handler, AdminStates.reply_text)
    dp.callback_query.register(claim_details_handler, F.data.startswith("claim:"))
    dp.callback_query.register(faq_ask_handler, F.data == "faq:ask")
    dp.callback_query.register(cancel_callback_handler, F.data == "cancel")
    dp.callback_query.register(claim_start_callback_handler, F.data == "menu:claim")
    dp.callback_query.register(claims_menu_callback_handler, F.data == "menu:claims")
    dp.callback_query.register(warranty_start_callback_handler, F.data == "menu:warranty")
    dp.callback_query.register(warranty_new_callback_handler, F.data == "warranty:new")
    dp.callback_query.register(shopping_callback_handler, F.data == "menu:shop")
    dp.callback_query.register(care_callback_handler, F.data == "menu:care")
    dp.callback_query.register(useful_callback_handler, F.data == "menu:useful")
    dp.callback_query.register(trust_callback_handler, F.data == "menu:trust")
    dp.callback_query.register(faq_callback_handler, F.data == "menu:faq")

    dp.message.register(claims_menu_handler, Command("claims"))
    dp.message.register(claim_start_handler, Command("claim"))
    dp.message.register(warranty_start_handler, Command("warranty"))

    dp.message.register(claim_description_handler, ClaimStates.description)
    dp.callback_query.register(claim_warranty_selection_handler, F.data.startswith("select_w:"), ClaimStates.purchase_type)
    dp.callback_query.register(claim_purchase_type_handler, ClaimStates.purchase_type)
    dp.message.register(claim_purchase_wb_handler, ClaimStates.purchase_wb)
    dp.message.register(claim_purchase_cz_handler, ClaimStates.purchase_cz_photo)
    dp.message.register(claim_files_handler, ClaimStates.files)
    dp.callback_query.register(claim_files_done_handler, F.data == "files:done", ClaimStates.files)
    dp.message.register(claim_contact_name_handler, ClaimStates.contact_name)
    dp.message.register(claim_contact_phone_handler, ClaimStates.contact_phone)
    dp.callback_query.register(claim_skip_phone_handler, F.data == "skip:phone", ClaimStates.contact_phone)

    dp.message.register(warranty_cz_handler, WarrantyStates.cz_photo)
    dp.message.register(warranty_receipt_handler, WarrantyStates.receipt_pdf, F.document)
    dp.message.register(warranty_sku_handler, WarrantyStates.sku)
    dp.message.register(warranty_name_handler, WarrantyStates.name)

    dp.message.register(unexpected_state_message_handler)

    # Forwarding handlers (only if no state is active)
    dp.message.register(admin_group_reply_handler, F.chat.type.in_({"supergroup", "group"}))
    dp.message.register(attach_clarification, F.chat.type == "private", StateFilter(None))

    # Move generic handlers down
    dp.message.register(generic_photo_handler, F.photo)
    dp.message.register(unexpected_message_handler)
    dp.callback_query.register(unexpected_callback_handler)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

