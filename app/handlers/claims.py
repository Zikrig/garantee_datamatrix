import asyncio
import io
import logging
import os
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
from app.utils import upsert_from_user, decode_image, format_decoded_codes, send_admin_claim, send_cached_photo
from app.receipt_parser import parse_receipt_pdf

router = Router()

@router.message(F.text == "🛠 Обращение по изделию")
@router.message(Command("claim"))
async def claim_start_handler(message: Message, state: FSMContext) -> None:
    await upsert_from_user(db, message.from_user)
    
    warranties = await db.get_warranties(message.from_user.id)
    if not warranties:
        await message.answer(
            "Чтобы оформить заявку по гарантии, вам необходимо зарегистрироваться.\n"
            "Это обеспечит вам 12 месяцев гарантийного обслуживания.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🔐 Активировать гарантию 12 месяцев", callback_data="menu:warranty")],
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
            "Это обеспечит вам 12 месяцев гарантийного обслуживания.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🔐 Активировать гарантию 12 месяцев", callback_data="menu:warranty")],
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
    await db.create_warranty(
        warranty_id=warranty_id,
        tg_id=message.from_user.id,
        cz_code=data["cz_code"],
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
        purchase_type="ЧЗ (новая гарантия)", 
        purchase_value=data["cz_code"]
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
        await state.set_state(ClaimStates.purchase_cz_photo)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⌨️ Отправить текстом", callback_data="claim:cz_text_start")],
            [InlineKeyboardButton(text="Отмена", callback_data="cancel")]
        ])
        await send_cached_photo(
            callback.message.bot, 
            db, 
            callback.message.chat.id, 
            "data/images/chz2.png",
            "Чтобы получить расширенную гарантию, \n"
            "отправьте фото бирки изделия с надписью «ЧЕСТНЫЙ ЗНАК»",
            reply_markup=kb
        )
        return

    warranty_id = data.replace("select_w:", "")
    warranties = await db.get_warranties(callback.from_user.id)
    selected = next((w for w in warranties if w["id"] == warranty_id), None)
    
    if not selected:
        await callback.message.answer("Ошибка: изделие не найдено. Пожалуйста, попробуйте еще раз.", reply_markup=main_menu_kb())
        return

    await state.update_data(
        purchase_type="ЧЗ (из гарантии)", 
        purchase_value=selected["cz_code"], 
        sku=selected.get("sku")
    )
    
    # Check if contact info is complete before moving to description
    user_db = await db.get_user(callback.from_user.id)
    if not user_db.get("name") or not user_db.get("phone") or not user_db.get("email"):
        await start_next_claim_reg_step(callback.message, state, user_db)
        return

    await state.set_state(ClaimStates.description)
    await callback.message.answer("Опишите ситуацию текстом.", reply_markup=cancel_kb())

@router.callback_query(F.data == "claim:cz_text_start")
async def claim_cz_text_start_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(ClaimStates.purchase_cz_text)
    await send_cached_photo(
        callback.message.bot,
        db,
        callback.message.chat.id,
        "data/images/chz_code.png",
        "Введите код Честный знак вручную.\n\n"
        "Рядом с вашим ЧЗ есть буквенно цифровой код. Он начинается примерно так: 01046. "
        "Введите ЦИФРОВУЮ часть этого кода - первые символы, обычно их от 12 до 20.",
        reply_markup=cancel_kb()
    )

@router.message(ClaimStates.purchase_cz_photo)
async def claim_purchase_cz_photo_handler(message: Message, state: FSMContext) -> None:
    photo = message.photo[-1] if message.photo else None
    document = message.document if message.document else None
    
    data = await state.get_data()
    failures = data.get("cz_failures_claim", 0)

    if not photo and not document:
        await message.answer("Нужна фотография бирки изделия или нажмите кнопку 'Отправить текстом'.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⌨️ Отправить текстом", callback_data="claim:cz_text_start")],
            [InlineKeyboardButton(text="Отмена", callback_data="cancel")]
        ]))
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

    if not codes or not is_ours:
        failures += 1
        await state.update_data(cz_failures_claim=failures)
        
        if failures >= 2:
            await state.set_state(ClaimStates.purchase_cz_text)
            await send_cached_photo(
                message.bot,
                db,
                message.chat.id,
                "data/images/chz_code.png",
                "⚠️ Не удалось распознать фото.\n\n"
                "Введите ЦИФРОВУЮ часть кода ЧЗ вручную - первые символы, обычно их от 12 до 20.",
                reply_markup=cancel_kb()
            )
            return

        error_text = "Не удалось прочитать код. Попробуйте более четкое фото."
        if codes and not is_ours:
            error_text = f"Код не относится к нашей продукции: {codes[0]}\nПожалуйста, отправьте корректный код ЧЗ."
        
        await message.answer(
            error_text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⌨️ Отправить текстом", callback_data="claim:cz_text_start")],
                [InlineKeyboardButton(text="Отмена", callback_data="cancel")]
            ])
        )
        return

    cz_code = codes[0]
    if await db.is_cz_registered(cz_code):
        await message.answer(
            "⚠️ Этот код Честный знак уже зарегистрирован в системе.\n"
            "Пожалуйста, выберите это изделие из списка в начале или используйте другой код.",
            reply_markup=main_menu_kb()
        )
        await state.clear()
        return

    await state.update_data(cz_code=cz_code, cz_file_id=file_id)
    user_data = await db.get_user(message.from_user.id)
    await start_next_claim_reg_step(message, state, user_data)

@router.message(ClaimStates.purchase_cz_text)
async def claim_purchase_cz_text_handler(message: Message, state: FSMContext) -> None:
    if not message.text:
        await message.answer("Пожалуйста, введите код текстом.", reply_markup=cancel_kb())
        return
    
    cz_code = message.text.strip()
    
    # Проверяем соответствие OUR_CODES
    from app.utils import get_ours_tokens
    tokens = get_ours_tokens()
    
    if tokens:
        code_valid = any(token in cz_code for token in tokens)
        if not code_valid:
            await message.answer(
                "❌ Код не относится к нашей продукции.\n"
                "Пожалуйста, проверьте код и введите еще раз. Код должен содержать один из наших идентификаторов.",
                reply_markup=cancel_kb()
            )
            return
    
    if await db.is_cz_registered(cz_code):
        await message.answer(
            "⚠️ Этот код Честный знак уже зарегистрирован в системе.\n"
            "Пожалуйста, выберите это изделие из списка в начале или используйте другой код.",
            reply_markup=main_menu_kb()
        )
        await state.clear()
        return

    if len(cz_code) < 10:
        await message.answer("Код слишком короткий. Пожалуйста, проверьте и введите еще раз.", reply_markup=cancel_kb())
        return

    await state.update_data(cz_code=cz_code, cz_file_id=None)
    user_data = await db.get_user(message.from_user.id)
    await start_next_claim_reg_step(message, state, user_data)

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
        await message.answer("❌ Фото не принимается. Пожалуйста, отправьте файл PDF чека с Wildberries.", reply_markup=cancel_kb())
        return
    
    if not message.document:
        await message.answer("❌ Пожалуйста, отправьте файл PDF чека с Wildberries.", reply_markup=cancel_kb())
        return
    
    if message.document.mime_type != "application/pdf":
        await message.answer("❌ Принимаются только PDF файлы. Пожалуйста, отправьте файл PDF чека.", reply_markup=cancel_kb())
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
            "Отправьте файл (PDF) чека с Wildberries.",
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
