import asyncio
import io
import logging
import os
import uuid
import datetime as dt
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
from app.utils import upsert_from_user, decode_image, format_decoded_codes, send_admin_claim
from app.receipt_parser import ReceiptParser, render_items

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
                    [InlineKeyboardButton(text="Зарегистрироваться", callback_data="menu:warranty")],
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
                    [InlineKeyboardButton(text="Зарегистрироваться", callback_data="menu:warranty")],
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
        await callback.message.answer(
            "🔐 Активируйте расширенную гарантию 12 месяцев.\n"
            "Отправьте фото кода Честный знак.",
            reply_markup=kb,
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
    await state.set_state(ClaimStates.description)
    await callback.message.answer("Опишите ситуацию текстом.", reply_markup=cancel_kb())

@router.callback_query(F.data == "claim:cz_text_start")
async def claim_cz_text_start_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(ClaimStates.purchase_cz_text)
    await callback.message.answer(
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
        await message.answer("Нужна фотография Честного знака или нажмите кнопку 'Отправить текстом'.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
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
            await message.answer(
                "⚠️ Не удалось распознать фото.\n\n"
                "Рядом с вашим ЧЗ есть буквенно цифровой код. Он начинается примерно так: 01046. "
                "Введите ЦИФРОВУЮ часть этого кода - первые символы, обычно их от 12 до 20.",
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
    await state.update_data(cz_code=cz_code, cz_file_id=file_id)
    await state.set_state(ClaimStates.purchase_receipt_pdf)
    await message.answer(
        "Код принят! ✅\n"
        "Теперь, пожалуйста, отправьте чек с WB в формате PDF.",
        reply_markup=cancel_kb(),
    )

@router.message(ClaimStates.purchase_cz_text)
async def claim_purchase_cz_text_handler(message: Message, state: FSMContext) -> None:
    if not message.text:
        await message.answer("Пожалуйста, введите код текстом.", reply_markup=cancel_kb())
        return
    
    cz_code = message.text.strip()
    if len(cz_code) < 10:
        await message.answer("Код слишком короткий. Пожалуйста, проверьте и введите еще раз.", reply_markup=cancel_kb())
        return

    await state.update_data(cz_code=cz_code, cz_file_id=None)
    await state.set_state(ClaimStates.purchase_receipt_pdf)
    await message.answer(
        "Код принят! ✅\n"
        "Теперь, пожалуйста, отправьте чек с WB в формате PDF.",
        reply_markup=cancel_kb(),
    )

@router.message(ClaimStates.purchase_receipt_pdf, F.document)
async def claim_purchase_receipt_handler(message: Message, state: FSMContext) -> None:
    if not message.document or not message.document.file_name.lower().endswith(".pdf"):
        await message.answer("Пожалуйста, отправьте чек в формате PDF.", reply_markup=cancel_kb())
        return

    file_id = message.document.file_id
    status_msg = await message.answer("📄 Обрабатываю чек... Это займет мгновение.")
    
    try:
        file = await message.bot.get_file(file_id)
        os.makedirs("data", exist_ok=True)
        temp_path = f"data/temp_claim_{file_id}.pdf"
        
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
    await state.set_state(ClaimStates.purchase_sku)
    await message.answer(
        "Чек получен! ✅\n"
        "Теперь введите артикул товара.",
        reply_markup=cancel_kb(),
    )

@router.message(ClaimStates.purchase_sku)
async def claim_purchase_sku_handler(message: Message, state: FSMContext) -> None:
    if not message.text:
        await message.answer("Пожалуйста, введите артикул товара текстом.", reply_markup=cancel_kb())
        return
    
    sku = message.text
    await state.update_data(sku=sku)
    
    # Save as warranty first
    data = await state.get_data()
    warranty_id = uuid.uuid4().hex[:8]
    await db.create_warranty(
        warranty_id=warranty_id,
        tg_id=message.from_user.id,
        cz_code=data["cz_code"],
        cz_file_id=data.get("cz_file_id"),
        receipt_file_id=data["receipt_file_id"],
        sku=sku,
        receipt_date=data["receipt_date"],
        receipt_text=data.get("receipt_text"),
        receipt_items=data.get("receipt_items")
    )
    
    await state.update_data(
        purchase_type="ЧЗ (новая гарантия)", 
        purchase_value=data["cz_code"]
    )
    
    await state.set_state(ClaimStates.description)
    await message.answer(
        f"Изделие '{sku}' успешно зарегистрировано! ✅\n\n"
        "Теперь опишите ситуацию по этому изделию текстом.",
        reply_markup=cancel_kb(),
    )

@router.message(ClaimStates.description)
async def claim_description_handler(message: Message, state: FSMContext) -> None:
    await state.update_data(description=message.text)
    await state.set_state(ClaimStates.files)
    await state.update_data(files=[])
    
    data = await state.get_data()
    sku = data.get("sku") or "Изделие"
    
    await message.answer(
        f"Выбрано изделие: {sku}\n"
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

@router.message(ClaimStates.contact_name)
async def claim_contact_name_handler(message: Message, state: FSMContext) -> None:
    await db.upsert_user(message.from_user.id, message.from_user.username, message.text)
    await state.set_state(ClaimStates.contact_phone)
    await message.answer("Введите телефон (или нажмите “Пропустить”).", reply_markup=skip_kb())

@router.message(ClaimStates.contact_phone)
async def claim_contact_phone_handler(message: Message, state: FSMContext) -> None:
    await finalize_claim(message, state, message.from_user, phone=message.text)

@router.callback_query(F.data == "skip:phone", ClaimStates.contact_phone)
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
        db,
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
