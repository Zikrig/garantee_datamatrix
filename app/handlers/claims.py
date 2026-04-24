import io
import logging
import uuid
import datetime as dt
from html import escape
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from app.database import db
from app.states import ClaimStates
from app.keyboards import (
    main_menu_kb, cancel_kb, purchase_type_kb, files_kb, 
    skip_kb, warranties_selection_kb, claim_status_kb
)
from app.utils import upsert_from_user, send_admin_claim
from app.receipt_parser import parse_receipt_pdf
from app.sheets import find_site_registration_by_receipt

router = Router()

RECEIPT_PDF_MANUAL = (
    "\n\n📌 в Личном Кабинете ВБ – Профиль – Данные и настройки – Оплата – чеки – "
    "находим свой чек – нажимаем «Распечатать», видим предварительный просмотр – "
    "нажимаем три точки в верхнем правом углу – «Сохранить как PDF»."
)

@router.message(F.text == "🛠 Обращение по изделию")
@router.message(Command("claim"))
async def claim_start_handler(message: Message, state: FSMContext) -> None:
    await upsert_from_user(db, message.from_user)
    
    warranties = await db.get_warranties(message.from_user.id)
    if not warranties:
        await message.answer(
            "Чтобы оформить заявку по гарантии, вам необходимо зарегистрироваться.\n"
            "Это обеспечит вам 12 месяцев гарантийного обслуживания.\n\n"
            "Если вы уже регистрировали гарантию на сайте, можно продолжить по номеру чека с ВБ.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🔐 Активировать гарантию 12 месяцев", callback_data="menu:warranty")],
                    [InlineKeyboardButton(text="🧾 Ввести номер чека с ВБ", callback_data="claimflow:site_start")],
                    [InlineKeyboardButton(text="Отмена", callback_data="cancel")]
                ]
            )
        )
        return

    await state.set_state(ClaimStates.purchase_type)
    await message.answer(
        "Выберите изделие, по которому подаете обращение:",
        reply_markup=warranties_selection_kb(warranties)
    )

@router.callback_query(F.data == "menu:claim")
async def claim_start_callback_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await upsert_from_user(db, callback.from_user)
    
    warranties = await db.get_warranties(callback.from_user.id)
    if not warranties:
        await callback.message.answer(
            "Чтобы оформить заявку по гарантии, вам необходимо зарегистрироваться.\n"
            "Это обеспечит вам 12 месяцев гарантийного обслуживания.\n\n"
            "Если вы уже регистрировали гарантию на сайте, можно продолжить по номеру чека с ВБ.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🔐 Активировать гарантию 12 месяцев", callback_data="menu:warranty")],
                    [InlineKeyboardButton(text="🧾 Ввести номер чека с ВБ", callback_data="claimflow:site_start")],
                    [InlineKeyboardButton(text="Отмена", callback_data="cancel")]
                ]
            )
        )
        return

    await state.set_state(ClaimStates.purchase_type)
    await callback.message.answer(
        "Выберите изделие, по которому подаете обращение:",
        reply_markup=warranties_selection_kb(warranties)
    )

async def start_next_claim_reg_step(message: Message, state: FSMContext, user_data: dict) -> None:
    data = await state.get_data()

    # Determine which contact info is missing
    missing_name = not user_data.get("name") and not data.get("name")
    missing_phone = not user_data.get("phone") and not data.get("phone")
    missing_email = not user_data.get("email") and not data.get("email")

    if missing_name:
        await state.set_state(ClaimStates.contact_name)
        await message.answer("Как к вам обращаться?", reply_markup=cancel_kb())
        return

    if missing_phone:
        await state.set_state(ClaimStates.contact_phone)
        await message.answer("Введите ваш номер телефона.", reply_markup=cancel_kb())
        return

    if missing_email:
        await state.set_state(ClaimStates.purchase_email)
        await message.answer("Введите вашу электронную почту.", reply_markup=cancel_kb())
        return

    # If all contact info is present, move to SKU
    if not data.get("sku"):
        await state.set_state(ClaimStates.purchase_sku)
        await message.answer(
            "введите артикул товара – это цифры с этикетки за словом «Артикул»",
            reply_markup=cancel_kb(),
        )
        return

    # Once item is registered, move to problem description (чек запрашивается позже)
    # Save as warranty first
    warranty_id = uuid.uuid4().hex[:8]
    cz_code_value = data.get("cz_code") or f"no-cz-{warranty_id}"
    await db.create_warranty(
        warranty_id=warranty_id,
        tg_id=message.from_user.id,
        cz_code=cz_code_value,
        cz_file_id=data.get("cz_file_id"),
        receipt_file_id=data.get("receipt_file_id"),
        sku=data["sku"],
        receipt_date=data.get("receipt_date"),
        receipt_text=data.get("receipt_text"),
        receipt_items=data.get("receipt_items")
    )
    
    # Update user data if we collected new info
    if data.get("name") or data.get("phone") or data.get("email"):
        await db.upsert_user(message.from_user.id, message.from_user.username, data.get("name"))
        if data.get("phone"):
            await db.update_user_phone(message.from_user.id, data["phone"])
        if data.get("email"):
            await db.update_user_email(message.from_user.id, data["email"])

    await state.update_data(
        purchase_type="Новая гарантия",
        purchase_value=data.get("receipt_text") or data.get("sku") or "-"
    )
    
    await state.set_state(ClaimStates.description)
    await message.answer(
        f"✅ Изделие <b>{escape(data['sku'])}</b> успешно зарегистрировано.\n\n"
        "Опишите ситуацию по этому изделию текстом.",
        reply_markup=cancel_kb(),
        parse_mode="HTML"
    )

@router.callback_query(F.data.startswith("select_w:"), ClaimStates.purchase_type)
async def claim_warranty_selection_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    data = callback.data
    if data == "select_w:other":
        await state.set_state(ClaimStates.purchase_site_receipt_number)
        await callback.message.answer(
            "🧾 Введите номер чека с ВБ.\n\n"
            "📌 Смотреть: зайти в свой профиль на ВБ → оплата → чеки — открыть чек покупки.",
            reply_markup=cancel_kb(),
        )
        return

    warranty_id = data.replace("select_w:", "")
    warranties = await db.get_warranties(callback.from_user.id)
    selected = next((w for w in warranties if w["id"] == warranty_id), None)
    
    if not selected:
        await callback.message.answer("Ошибка: изделие не найдено. Пожалуйста, попробуйте еще раз.", reply_markup=main_menu_kb())
        return

    await state.update_data(
        purchase_type="Зарегистрированная гарантия",
        purchase_value=selected.get("receipt_text") or selected.get("sku") or "-",
        sku=selected.get("sku")
    )
    
    # Check if contact info is complete before moving to description
    user_db = await db.get_user(callback.from_user.id)
    if not user_db.get("name") or not user_db.get("phone") or not user_db.get("email"):
        await start_next_claim_reg_step(callback.message, state, user_db)
        return

    await state.set_state(ClaimStates.description)
    await callback.message.answer("Опишите ситуацию текстом.", reply_markup=cancel_kb())

@router.callback_query(F.data == "claimflow:site_start")
async def claim_site_start_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(ClaimStates.purchase_site_receipt_number)
    await callback.message.answer(
        "🧾 Введите номер чека с ВБ.\n\n"
        "📌 Смотреть: зайти в свой профиль на ВБ → оплата → чеки — открыть чек покупки.",
        reply_markup=cancel_kb(),
    )

@router.message(ClaimStates.purchase_site_receipt_number)
async def claim_site_receipt_text_handler(message: Message, state: FSMContext) -> None:
    if not message.text or not message.text.strip():
        await message.answer("Пожалуйста, введите номер чека текстом.", reply_markup=cancel_kb())
        return

    receipt_number = message.text.strip()

    # 1) Ищем в таблице бота (warranties.receipt_text)
    bot_warranties = await db.find_warranties_by_receipt(receipt_number)
    if bot_warranties:
        w = bot_warranties[0]
        await state.update_data(
            purchase_type="ВБ (по номеру чека)",
            purchase_value=w.get("receipt_text") or receipt_number,
            receipt_text=w.get("receipt_text") or receipt_number,
            receipt_date=w.get("receipt_date"),
            receipt_items=w.get("receipt_items"),
            sku=w.get("sku"),
            no_file=True,
        )
        user_db = await db.get_user(message.from_user.id)
        if not user_db or not user_db.get("name") or not user_db.get("phone") or not user_db.get("email"):
            await start_next_claim_reg_step(message, state, user_db or {})
            return
        await state.set_state(ClaimStates.description)
        await message.answer(
            "✅ Чек найден в ваших зарегистрированных гарантиях.\n"
            "Опишите ситуацию по изделию текстом.",
            reply_markup=cancel_kb(),
        )
        return

    # 2) Ищем в таблице сайта
    site_data = await find_site_registration_by_receipt(receipt_number)
    if site_data:
        await state.update_data(
            purchase_type="Сайт (по номеру чека)",
            purchase_value=site_data.get("receipt_number") or receipt_number,
            receipt_text=site_data.get("receipt_number") or receipt_number,
            receipt_date=site_data.get("purchase_date"),
            receipt_items=site_data.get("products"),
            sku=site_data.get("sku"),
            no_file=True,
        )
        if site_data.get("name"):
            await state.update_data(name=str(site_data["name"]).strip())
        if site_data.get("phone"):
            await state.update_data(phone=str(site_data["phone"]).strip())
        if site_data.get("email"):
            await state.update_data(email=str(site_data["email"]).strip().lower())

        await state.set_state(ClaimStates.description)
        await message.answer(
            "✅ Чек найден в регистрации на сайте.\n"
            "Опишите ситуацию по изделию текстом.",
            reply_markup=cancel_kb(),
        )
        return

    # 3) Чек не найден нигде — предлагаем зарегистрировать гарантию
    await state.clear()
    await message.answer(
        "❌ Этот номер чека не найден ни в наших гарантиях, ни в регистрации на сайте.\n\n"
        "Чтобы пользоваться сервисом и оформить обращение, активируйте гарантию — "
        "это займёт меньше минуты.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔐 Активировать гарантию 12 месяцев", callback_data="menu:warranty")],
                [InlineKeyboardButton(text="Отмена", callback_data="cancel")],
            ]
        ),
    )

@router.message(ClaimStates.contact_name)
async def claim_contact_name_handler(message: Message, state: FSMContext) -> None:
    if not message.text:
        await message.answer("Пожалуйста, введите ваше имя текстом.", reply_markup=cancel_kb())
        return
    await state.update_data(name=message.text)
    user_data = await db.get_user(message.from_user.id)
    await start_next_claim_reg_step(message, state, user_data)

@router.message(ClaimStates.contact_phone)
async def claim_contact_phone_handler(message: Message, state: FSMContext) -> None:
    if not message.text:
        await message.answer("Пожалуйста, введите ваш номер телефона текстом.", reply_markup=cancel_kb())
        return
    await state.update_data(phone=message.text)
    user_data = await db.get_user(message.from_user.id)
    await start_next_claim_reg_step(message, state, user_data)

@router.message(ClaimStates.purchase_email)
async def claim_purchase_email_handler(message: Message, state: FSMContext) -> None:
    if not message.text or "@" not in message.text or "." not in message.text:
        await message.answer("Пожалуйста, введите корректный адрес электронной почты.", reply_markup=cancel_kb())
        return
    
    email = message.text.strip().lower()
    await state.update_data(email=email)
    user_data = await db.get_user(message.from_user.id)
    await start_next_claim_reg_step(message, state, user_data)

@router.message(ClaimStates.purchase_sku)
async def claim_purchase_sku_handler(message: Message, state: FSMContext) -> None:
    if not message.text:
        await message.answer("Пожалуйста, введите артикул товара текстом.", reply_markup=cancel_kb())
        return
    
    sku = message.text
    await state.update_data(sku=sku)
    user_data = await db.get_user(message.from_user.id)
    await start_next_claim_reg_step(message, state, user_data)

@router.message(ClaimStates.purchase_receipt_file)
async def claim_purchase_receipt_file_handler(message: Message, state: FSMContext) -> None:
    # Проверяем, что это PDF файл, а не фото
    if message.photo:
        await message.answer(
            "❌ Фото не принимается. Пожалуйста, отправьте файл PDF чека с Wildberries."
            f"{RECEIPT_PDF_MANUAL}",
            reply_markup=cancel_kb(),
        )
        return
    
    if not message.document:
        await message.answer(
            "❌ Пожалуйста, отправьте файл PDF чека с Wildberries."
            f"{RECEIPT_PDF_MANUAL}",
            reply_markup=cancel_kb(),
        )
        return
    
    if message.document.mime_type != "application/pdf":
        await message.answer(
            "❌ Принимаются только PDF файлы. Пожалуйста, отправьте файл PDF чека."
            f"{RECEIPT_PDF_MANUAL}",
            reply_markup=cancel_kb(),
        )
        return
    
    file_id = message.document.file_id
    status_msg = await message.answer("📄 Обрабатываю PDF-чек...")
    
    try:
        file = await message.bot.get_file(file_id)
        pdf_bytes = io.BytesIO()
        await message.bot.download_file(file.file_path, destination=pdf_bytes)
        pdf_bytes.seek(0)
        
        # Парсим чек для получения товаров и даты
        from app.receipt_parser import ReceiptParser
        parser = ReceiptParser()
        receipt_data = parser.parse_pdf(pdf_bytes)
        
        if not receipt_data.items:
            await message.answer(
                "❌ Не удалось распознать товары из чека. "
                "Убедитесь, что файл содержит корректный чек с Wildberries и попробуйте еще раз.",
                reply_markup=cancel_kb()
            )
            return
        
        receipt_items = "\n".join([f"- {i.name} ({i.amount:.2f} руб.)" for i in receipt_data.items])
        receipt_text = "Чек распознан из PDF"
        receipt_date = receipt_data.date
        
        await state.update_data(
            receipt_file_id=file_id,
            receipt_items=receipt_items,
            receipt_text=receipt_text,
            receipt_date=receipt_date
        )
        
        # После обработки чека переходим к файлам
        await state.set_state(ClaimStates.files)
        await state.update_data(files=[])
        
        await message.answer(
            "Пришлите фото/видео неисправности (если есть, до 5 файлов). Нажмите “Готово”, когда закончите.",
            reply_markup=files_kb(),
        )
    except Exception as e:
        logging.error(f"Receipt parse error: {e}")
        await message.answer(
            "❌ Произошла ошибка при обработке чека. Пожалуйста, попробуйте еще раз.",
            reply_markup=cancel_kb()
        )
    finally:
        try:
            await status_msg.delete()
        except:
            pass

# Убрана возможность пропустить загрузку чека
# Обработчик receipt_text удален - чек только PDF

@router.message(ClaimStates.description)
async def claim_description_handler(message: Message, state: FSMContext) -> None:
    await state.update_data(description=message.text)
    
    # После описания проблемы запрашиваем чек для заявки
    data = await state.get_data()
    if not data.get("receipt_file_id") and not data.get("no_file"):
        await state.set_state(ClaimStates.purchase_receipt_file)
        await message.answer(
            "Отправьте файл (PDF) чека с Wildberries."
            f"{RECEIPT_PDF_MANUAL}",
            reply_markup=cancel_kb(),
        )
        return
    
    # Если чек уже есть, переходим к файлам
    await state.set_state(ClaimStates.files)
    await state.update_data(files=[])
    
    await message.answer(
        "Пришлите фото/видео неисправности (если есть, до 5 файлов). Нажмите “Готово”, когда закончите.",
        reply_markup=files_kb(),
    )

@router.message(ClaimStates.files)
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

@router.callback_query(F.data == "files:done", ClaimStates.files)
async def claim_files_done_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await finalize_claim(callback.message, state, callback.from_user)

async def finalize_claim(message: Message, state: FSMContext, user: Any) -> None:
    data = await state.get_data()
    claim_number = await db.get_next_claim_number()
    claim_id = str(claim_number)
    
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
    
    # Send to admins/group
    await send_admin_claim(
        message.bot,
        db,
        claim,
        files,
        user.username,
        user_db.get("name") if user_db else None,
        user_db.get("phone") if user_db else None,
        user_db.get("email") if user_db else None,
        receipt_items=data.get("receipt_items"),
        receipt_date=data.get("receipt_date"),
    )

    await message.answer(
        f"Заявка принята! Номер: {claim_id}",
        reply_markup=main_menu_kb(),
    )
    await state.clear()
