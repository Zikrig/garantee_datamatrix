import asyncio
import datetime as dt
import io
import logging
import os
import uuid

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from app.database import db
from app.states import WarrantyStates
from app.keyboards import main_menu_kb, cancel_kb
from app.utils import upsert_from_user, decode_image
from app.constants import WARRANTY_LEGAL_TEXT
from app.receipt_parser import ReceiptParser, render_items

router = Router()

async def start_warranty_activation(message: Message, state: FSMContext) -> None:
    await state.set_state(WarrantyStates.cz_photo)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⌨️ Отправить текстом", callback_data="warranty:cz_text_start")],
        [InlineKeyboardButton(text="Отмена", callback_data="cancel")]
    ])
    
    await message.answer(
        "🔐 Активируйте расширенную гарантию 12 месяцев.\n"
        "Отправьте фото кода Честный знак.",
        reply_markup=kb,
    )

@router.message(F.text == "🔐 Получить гарантию")
@router.message(Command("warranty"))
async def warranty_start_handler(message: Message, state: FSMContext) -> None:
    await upsert_from_user(db, message.from_user)
    warranties = await db.get_warranties(message.from_user.id)
    if warranties:
        # Import here to avoid circular
        from app.handlers.common import show_user_warranties
        await show_user_warranties(message, message.from_user.id)
    else:
        await start_warranty_activation(message, state)

@router.callback_query(F.data == "menu:warranty")
async def warranty_start_callback_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await upsert_from_user(db, callback.from_user)
    warranties = await db.get_warranties(callback.from_user.id)
    
    if warranties:
        from app.handlers.common import show_user_warranties
        await show_user_warranties(callback.message, callback.from_user.id)
    else:
        await start_warranty_activation(callback.message, state)

@router.callback_query(F.data == "warranty:new")
async def warranty_new_callback_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await start_warranty_activation(callback.message, state)

@router.callback_query(F.data == "warranty:cz_text_start")
async def warranty_cz_text_start_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(WarrantyStates.cz_text)
    await callback.message.answer(
        "Введите код Честный знак вручную.\n\n"
        "Рядом с вашим ЧЗ есть буквенно цифровой код. Он начинается примерно так: 01046. "
        "Введите ЦИФРОВУЮ часть этого кода - первые символы, обычно их от 12 до 20.",
        reply_markup=cancel_kb()
    )

@router.message(WarrantyStates.cz_photo)
async def warranty_cz_handler(message: Message, state: FSMContext) -> None:
    photo = message.photo[-1] if message.photo else None
    document = message.document if message.document else None
    
    data = await state.get_data()
    failures = data.get("cz_failures", 0)

    if not photo and not document:
        await message.answer("Нужна фотография Честного знака или нажмите кнопку 'Отправить текстом'.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⌨️ Отправить текстом", callback_data="warranty:cz_text_start")],
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
        await state.update_data(cz_failures=failures)
        
        if failures >= 2:
            await state.set_state(WarrantyStates.cz_text)
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
                [InlineKeyboardButton(text="⌨️ Отправить текстом", callback_data="warranty:cz_text_start")],
                [InlineKeyboardButton(text="Отмена", callback_data="cancel")]
            ])
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

@router.message(WarrantyStates.cz_text)
async def warranty_cz_text_handler(message: Message, state: FSMContext) -> None:
    if not message.text:
        await message.answer("Пожалуйста, введите код текстом.", reply_markup=cancel_kb())
        return
    
    cz_code = message.text.strip()
    if len(cz_code) < 10:
        await message.answer("Код слишком короткий. Пожалуйста, проверьте и введите еще раз.", reply_markup=cancel_kb())
        return

    await state.update_data(cz_code=cz_code, cz_file_id=None)
    await state.set_state(WarrantyStates.receipt_pdf)
    await message.answer(
        "Код принят! ✅\n"
        "Теперь, пожалуйста, отправьте чек с WB в формате PDF.",
        reply_markup=cancel_kb(),
    )

@router.message(WarrantyStates.receipt_pdf, F.document)
async def warranty_receipt_handler(message: Message, state: FSMContext) -> None:
    if not message.document or not message.document.file_name.lower().endswith(".pdf"):
        await message.answer("Пожалуйста, отправьте чек в формате PDF.", reply_markup=cancel_kb())
        return

    file_id = message.document.file_id
    status_msg = await message.answer("📄 Обрабатываю чек... Это займет мгновение.")
    
    try:
        file = await message.bot.get_file(file_id)
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

@router.message(WarrantyStates.sku)
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

@router.message(WarrantyStates.name)
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

