import logging
from html import escape
from aiogram import F, Router, Bot
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from app.database import db
from app.keyboards import admin_menu_kb, claims_list_kb, claim_status_kb
from app.utils import ADMIN_CHAT_IDS, get_or_create_user_thread
from app.states import AdminStates
from app.sheets import sync_to_sheets

router = Router()

@router.message(Command("admin"))
async def admin_handler(message: Message) -> None:
    if not ADMIN_CHAT_IDS or message.from_user.id not in ADMIN_CHAT_IDS:
        return
    
    group_id = await db.get_setting("admin_group_id")
    status = f"✅ Группа привязана: <code>{escape(str(group_id))}</code>" if group_id else "❌ Группа не привязана. Напишите /add в супергруппе."
    
    await message.answer(
        f"Панель администратора:\n\n{status}", 
        reply_markup=admin_menu_kb(),
        parse_mode="HTML"
    )

@router.message(Command("add"))
async def admin_add_group_handler(message: Message, bot: Bot) -> None:
    if not ADMIN_CHAT_IDS or message.from_user.id not in ADMIN_CHAT_IDS:
        return
    
    if message.chat.type not in ["supergroup", "group"]:
        await message.answer("Эту команду нужно вызвать в супергруппе (с включенными темами).")
        return

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

    group_id_str = str(message.chat.id)
    await db.set_setting("admin_group_id", group_id_str)
    logging.info(f"Admin group ID saved: {group_id_str} for group: {message.chat.title}")
    
    # Проверяем, что настройка сохранилась
    saved_group_id = await db.get_setting("admin_group_id")
    if saved_group_id != group_id_str:
        logging.error(f"Failed to save admin_group_id! Expected: {group_id_str}, Got: {saved_group_id}")
        await message.answer(f"⚠️ Предупреждение: группа сохранена, но проверка не прошла. Сохранено: {saved_group_id}")
    else:
        await message.answer(f"✅ Эта группа ({message.chat.title}) теперь успешно привязана для обработки заявок.")

@router.callback_query(F.data.startswith("admin:list_claims:"))
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
    
    if not claims:
        if page == 0:
            await callback.message.edit_text("Заявок не найдено.", reply_markup=admin_menu_kb())
        else:
            await callback.answer("Больше заявок нет.")
        return

    await callback.message.edit_text(
        "Вот ваши заявки", 
        reply_markup=claims_list_kb(claims, group_id, filter_type, page, total_count, limit)
    )
    await callback.answer()

@router.callback_query(F.data == "admin:menu")
async def admin_menu_callback_handler(callback: CallbackQuery) -> None:
    if not ADMIN_CHAT_IDS or callback.from_user.id not in ADMIN_CHAT_IDS:
        await callback.answer("Недостаточно прав")
        return
    
    group_id = await db.get_setting("admin_group_id")
    status = f"✅ Группа привязана: <code>{escape(str(group_id))}</code>" if group_id else "❌ Группа не привязана. Напишите /add в супергруппе."
    
    await callback.message.edit_text(
        f"Панель администратора:\n\n{status}", 
        reply_markup=admin_menu_kb(),
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(F.data.startswith("status:"))
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
    
    group_id_str = await db.get_setting("admin_group_id")
    is_group_msg = str(callback.message.chat.id) == group_id_str
    
    group_link = None
    if not is_group_msg and group_id_str:
        clean_group_id = group_id_str.replace("-100", "")
        if claim.get("group_message_id"):
            group_link = f"https://t.me/c/{clean_group_id}/{claim['group_message_id']}"

    await callback.message.edit_reply_markup(
        reply_markup=claim_status_kb(claim_id, status, is_group=is_group_msg, group_link=group_link)
    )
    await callback.answer(f"Статус обновлен: {status}")

    if status == "Нужны уточнения":
        await callback.bot.send_message(claim["tg_id"], "По вашей заявке нужны уточнения. Пожалуйста, отправьте доп. текст/фото.")
    elif status == "Решено":
        await callback.bot.send_message(claim["tg_id"], f"Заявка {claim_id} отмечена как решенная.")
    elif status == "В работе":
        await callback.bot.send_message(claim["tg_id"], f"Заявка {claim_id} принята в работу.")

@router.callback_query(F.data.startswith("claim:"))
async def claim_details_handler(callback: CallbackQuery) -> None:
    claim_id = callback.data.split(":", 1)[1]
    claim = await db.get_claim(claim_id)
    if not claim:
        await callback.answer("Заявка не найдена")
        return
    
    products_info = ""
    warranties = await db.get_warranties(claim['tg_id'])
    w = next((w for w in warranties if w['cz_code'] == claim['purchase_value']), None)
    if w and w.get('receipt_items'):
        products_info = f"\n<b>Товары в чеке:</b>\n{escape(w['receipt_items'])}"

    text = (
        f"🛠 <b>Заявка {escape(claim['id'])}</b>\n"
        f"Статус: {escape(claim['status'])}\n"
        f"Идентификатор: {escape(claim['purchase_type'])} / {escape(claim['purchase_value'])}\n"
        f"{products_info}\n"
        f"<b>Текст проблемы:</b>\n{escape(claim['description'])}"
    )
    
    is_admin = ADMIN_CHAT_IDS and callback.from_user.id in ADMIN_CHAT_IDS
    from app.keyboards import main_menu_kb
    kb = claim_status_kb(claim['id'], claim['status']) if is_admin else main_menu_kb()
    
    await callback.message.answer(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data.startswith("reply:"))
async def admin_reply_callback_handler(callback: CallbackQuery, state: FSMContext) -> None:
    if not ADMIN_CHAT_IDS or callback.from_user.id not in ADMIN_CHAT_IDS:
        await callback.answer("Недостаточно прав")
        return
    
    claim_id = callback.data.split(":")[1]
    await state.update_data(reply_claim_id=claim_id)
    await state.set_state(AdminStates.reply_text)
    await callback.message.answer(f"Введите ответ на заявку {claim_id}:")
    await callback.answer()

@router.message(AdminStates.reply_text)
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

@router.message(Command("comment"))
async def comment_handler(message: Message) -> None:
    if not ADMIN_CHAT_IDS or message.from_user.id not in ADMIN_CHAT_IDS:
        return
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("Формат: /comment <claim_id> <текст>")
        return
    claim_id, comment = parts[1], parts[2]
    claim = await db.get_claim(claim_id)
    if not claim:
        await message.answer("Заявка не найдена")
        return
    await db.update_claim_comment(claim_id, comment)
    await message.answer("Комментарий сохранен.")
    await message.bot.send_message(claim["tg_id"], f"Комментарий менеджера по заявке {claim_id}:\n{comment}")


@router.message(Command("table"))
async def table_sync_handler(message: Message) -> None:
    if not ADMIN_CHAT_IDS or message.from_user.id not in ADMIN_CHAT_IDS:
        return

    await message.answer("⏳ Запускаю синхронизацию таблицы...")
    try:
        await sync_to_sheets()
        await message.answer("✅ Синхронизация с Google Sheets завершена.")
    except Exception as e:
        logging.exception("Manual sheets sync failed")
        await message.answer(f"❌ Ошибка синхронизации: {e}")

