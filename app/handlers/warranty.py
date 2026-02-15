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
from app.utils import upsert_from_user, decode_image, send_cached_photo
from app.constants import WARRANTY_LEGAL_TEXT

router = Router()

async def start_warranty_activation(message: Message, state: FSMContext) -> None:
    await state.set_state(WarrantyStates.cz_photo)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⌨️ Отправить текстом", callback_data="warranty:cz_text_start")],
        [InlineKeyboardButton(text="Отмена", callback_data="cancel")]
    ])
    
    await send_cached_photo(
        message.bot, 
        db, 
        message.chat.id, 
        "data/images/chz.png",
        "🔐 Активируйте расширенную гарантию 12 месяцев.\n"
        "Отправьте фото бирки изделия с надписью «ЧЕСТНЫЙ ЗНАК».",
        reply_markup=kb,
    )

@router.message(F.text == "🔐 Активировать гарантию 12 месяцев")
@router.message(Command("warranty"))
async def warranty_start_handler(message: Message, state: FSMContext) -> None:
    await upsert_from_user(db, message.from_user)
    warranties = await db.get_warranties(message.from_user.id)
    if warranties:
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

async def start_next_registration_step(message: Message, state: FSMContext, user_data: dict) -> None:
    current_state = await state.get_state()
    data = await state.get_data()

    # Determine which contact info is missing
    missing_name = not user_data.get("name") and not data.get("name")
    missing_phone = not user_data.get("phone") and not data.get("phone")
    missing_email = not user_data.get("email") and not data.get("email")

    if missing_name:
        await state.set_state(WarrantyStates.name)
        await message.answer("Как к вам обращаться?", reply_markup=cancel_kb())
        return

    if missing_phone:
        await state.set_state(WarrantyStates.phone)
        await message.answer("Введите ваш номер телефона.", reply_markup=cancel_kb())
        return

    if missing_email:
        await state.set_state(WarrantyStates.email)
        await message.answer("Введите вашу электронную почту.", reply_markup=cancel_kb())
        return

    # If all contact info is present, move to SKU
    if current_state in [WarrantyStates.cz_photo, WarrantyStates.cz_text, WarrantyStates.name, WarrantyStates.phone, WarrantyStates.email]:
        if not data.get("sku"):
            await state.set_state(WarrantyStates.sku)
            await message.answer(
                "введите артикул товара – это цифры с этикетки за словом «Артикул»",
                reply_markup=cancel_kb(),
            )
            return

    # If everything is done, finalize (без требования чека)
    await finalize_warranty(message, state, data.get("name") or user_data.get("name"))

@router.message(WarrantyStates.cz_photo)
async def warranty_cz_handler(message: Message, state: FSMContext) -> None:
    photo = message.photo[-1] if message.photo else None
    document = message.document if message.document else None
    
    data = await state.get_data()
    failures = data.get("cz_failures", 0)

    if not photo and not document:
        await message.answer("Нужна фотография бирки изделия или нажмите кнопку 'Отправить текстом'.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
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
                [InlineKeyboardButton(text="⌨️ Отправить текстом", callback_data="warranty:cz_text_start")],
                [InlineKeyboardButton(text="Отмена", callback_data="cancel")]
            ])
        )
        return

    cz_code = codes[0]
    if await db.is_cz_registered(cz_code):
        await message.answer(
            "⚠️ Этот код Честный знак уже зарегистрирован в системе.\n"
            "Повторная регистрация одного и того же изделия невозможна.",
            reply_markup=cancel_kb()
        )
        return

    await state.update_data(cz_code=cz_code, cz_file_id=file_id)
    user_data = await db.get_user(message.from_user.id)
    await start_next_registration_step(message, state, user_data)

@router.message(WarrantyStates.cz_text)
async def warranty_cz_text_handler(message: Message, state: FSMContext) -> None:
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
            "Повторная регистрация одного и того же изделия невозможна.",
            reply_markup=cancel_kb()
        )
        return

    if len(cz_code) < 10:
        await message.answer("Код слишком короткий. Пожалуйста, проверьте и введите еще раз.", reply_markup=cancel_kb())
        return

    await state.update_data(cz_code=cz_code, cz_file_id=None)
    user_data = await db.get_user(message.from_user.id)
    await start_next_registration_step(message, state, user_data)

@router.message(WarrantyStates.name)
async def warranty_name_handler(message: Message, state: FSMContext) -> None:
    if not message.text:
        await message.answer("Пожалуйста, введите ваше имя текстом.", reply_markup=cancel_kb())
        return
    await state.update_data(name=message.text)
    user_data = await db.get_user(message.from_user.id)
    await start_next_registration_step(message, state, user_data)

@router.message(WarrantyStates.phone)
async def warranty_phone_handler(message: Message, state: FSMContext) -> None:
    if not message.text:
        await message.answer("Пожалуйста, введите ваш номер телефона текстом.", reply_markup=cancel_kb())
        return
    await state.update_data(phone=message.text)
    user_data = await db.get_user(message.from_user.id)
    await start_next_registration_step(message, state, user_data)

@router.message(WarrantyStates.email)
async def warranty_email_handler(message: Message, state: FSMContext) -> None:
    if not message.text or "@" not in message.text or "." not in message.text:
        await message.answer("Пожалуйста, введите корректный адрес электронной почты.", reply_markup=cancel_kb())
        return
    
    email = message.text.strip().lower()
    await state.update_data(email=email)
    user_data = await db.get_user(message.from_user.id)
    await start_next_registration_step(message, state, user_data)

@router.message(WarrantyStates.sku)
async def warranty_sku_handler(message: Message, state: FSMContext) -> None:
    if not message.text:
        await message.answer("Пожалуйста, введите артикул товара текстом.", reply_markup=cancel_kb())
        return
    
    await state.update_data(sku=message.text)
    user_data = await db.get_user(message.from_user.id)
    await start_next_registration_step(message, state, user_data)

# Обработчики чека удалены - чек не требуется при получении гарантии

async def finalize_warranty(message: Message, state: FSMContext, name: str) -> None:
    data = await state.get_data()
    warranty_id = uuid.uuid4().hex[:8]
    
    # Update user contact info in DB if it was just collected
    if data.get("name") or data.get("phone") or data.get("email"):
        await db.upsert_user(message.from_user.id, message.from_user.username, data.get("name"))
        if data.get("phone"):
            await db.update_user_phone(message.from_user.id, data["phone"])
        if data.get("email"):
            await db.update_user_email(message.from_user.id, data["email"])

    start_date, end_date = await db.create_warranty(
        warranty_id=warranty_id,
        tg_id=message.from_user.id,
        cz_code=data["cz_code"],
        cz_file_id=data.get("cz_file_id"),
        receipt_file_id=None,
        sku=data["sku"],
        receipt_date=None,
        receipt_text=None,
        receipt_items=None
    )
    
    try:
        display_end_date = dt.date.fromisoformat(end_date).strftime("%d.%m.%Y")
    except:
        display_end_date = end_date

    await message.answer(
        f"✅ Регистрация завершена! Гарантия активирована.\n\n"
        f"📅 Гарантия действует до: <b>{display_end_date}</b>\n\n"
        f"{WARRANTY_LEGAL_TEXT}",
        reply_markup=main_menu_kb(),
        parse_mode="HTML"
    )
    await state.clear()
