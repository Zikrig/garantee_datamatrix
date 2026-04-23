import datetime as dt
import uuid

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery

from app.database import db
from app.states import WarrantyStates
from app.keyboards import main_menu_kb, cancel_kb
from app.utils import upsert_from_user
from app.constants import WARRANTY_LEGAL_TEXT

router = Router()


def _parse_sku_list(raw: str) -> list[str]:
    text = (raw or "").replace("\n", ",")
    items = [item.strip() for item in text.split(",")]
    # Preserve order and skip empty values
    return [item for item in items if item]


async def start_warranty_activation(message: Message, state: FSMContext) -> None:
    await state.clear()
    user_data = await db.get_user(message.from_user.id)
    await start_next_registration_step(message, state, user_data or {})

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

async def start_next_registration_step(message: Message, state: FSMContext, user_data: dict) -> None:
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

    if not data.get("sku_list"):
        await state.set_state(WarrantyStates.sku)
        await message.answer(
            "Введите артикул товара.\n"
            "(цифры на этикетке, которые идут после слова \"Артикул\")\n"
            "Если изделий несколько, отправьте артикулы через запятую.",
            reply_markup=cancel_kb(),
        )
        return

    if not data.get("receipt_date_wb"):
        await state.set_state(WarrantyStates.receipt_date_wb)
        await message.answer(
            "Введите дату чека из Wildberries (например: 01.02.2025).\n\n"
            "📌 Смотреть: зайти в свой профиль на ВБ → оплата → чеки — открыть чек покупки туники и посмотреть.",
            reply_markup=cancel_kb(),
        )
        return

    if not data.get("receipt_number_wb"):
        await state.set_state(WarrantyStates.receipt_number_wb)
        await message.answer(
            "Введите номер чека из Wildberries.\n\n"
            "📌 Смотреть: зайти в свой профиль на ВБ → оплата → чеки — открыть чек покупки туники и посмотреть.",
            reply_markup=cancel_kb(),
        )
        return

    # If everything is done, finalize
    await finalize_warranty(message, state, data.get("name") or user_data.get("name"))

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

    sku_list = _parse_sku_list(message.text)
    if not sku_list:
        await message.answer(
            "Не удалось распознать артикулы. Введите один артикул или список через запятую.",
            reply_markup=cancel_kb(),
        )
        return

    await state.update_data(sku_list=sku_list)
    user_data = await db.get_user(message.from_user.id)
    await start_next_registration_step(message, state, user_data)

@router.message(WarrantyStates.receipt_date_wb)
async def warranty_receipt_date_wb_handler(message: Message, state: FSMContext) -> None:
    if not message.text:
        await message.answer("Пожалуйста, введите дату чека текстом.", reply_markup=cancel_kb())
        return
    raw = message.text.strip()
    # Принимаем DD.MM.YYYY или YYYY-MM-DD
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            dt.datetime.strptime(raw, fmt)
            break
        except ValueError:
            continue
    else:
        await message.answer(
            "Неверный формат даты. Введите дату в формате ДД.ММ.ГГГГ (например: 01.02.2025).",
            reply_markup=cancel_kb(),
        )
        return
    await state.update_data(receipt_date_wb=raw)
    user_data = await db.get_user(message.from_user.id)
    await start_next_registration_step(message, state, user_data)

@router.message(WarrantyStates.receipt_number_wb)
async def warranty_receipt_number_wb_handler(message: Message, state: FSMContext) -> None:
    if not message.text:
        await message.answer("Пожалуйста, введите номер чека из WB текстом.", reply_markup=cancel_kb())
        return
    await state.update_data(receipt_number_wb=message.text.strip())
    user_data = await db.get_user(message.from_user.id)
    await start_next_registration_step(message, state, user_data)

async def finalize_warranty(message: Message, state: FSMContext, name: str) -> None:
    data = await state.get_data()
    sku_list = data.get("sku_list") or []
    
    # Update user contact info in DB if it was just collected
    if data.get("name") or data.get("phone") or data.get("email"):
        await db.upsert_user(message.from_user.id, message.from_user.username, data.get("name"))
        if data.get("phone"):
            await db.update_user_phone(message.from_user.id, data["phone"])
        if data.get("email"):
            await db.update_user_email(message.from_user.id, data["email"])

    end_date = ""
    for sku in sku_list:
        warranty_id = uuid.uuid4().hex[:8]
        # Keep cz_code populated for backward compatibility with existing schema/flows.
        fallback_cz_code = f"no-cz-{warranty_id}"
        _, end_date = await db.create_warranty(
            warranty_id=warranty_id,
            tg_id=message.from_user.id,
            cz_code=fallback_cz_code,
            cz_file_id=None,
            receipt_file_id=None,
            sku=sku,
            receipt_date=data.get("receipt_date_wb"),
            receipt_text=data.get("receipt_number_wb"),
            receipt_items=None,
        )
    
    try:
        display_end_date = dt.date.fromisoformat(end_date).strftime("%d.%m.%Y")
    except:
        display_end_date = end_date

    await message.answer(
        f"✅ Регистрация завершена! Гарантия активирована для {len(sku_list)} изделий.\n\n"
        f"📅 Гарантия действует до: <b>{display_end_date}</b>\n\n"
        f"{WARRANTY_LEGAL_TEXT}",
        reply_markup=main_menu_kb(),
        parse_mode="HTML"
    )
    await state.clear()
