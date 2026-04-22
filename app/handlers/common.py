import datetime as dt
import os
from html import escape
from aiogram import F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from app.db import Database
from app.utils import upsert_from_user, load_kb, DEFAULT_KB, get_ours_tokens, send_admin_claim
from app.keyboards import main_menu_kb, cancel_kb, claims_list_kb, faq_suggestions_kb
from app.states import CheckZnackStates, FaqAskStates
from app.constants import CARE_TEXT, TRUST_TEXT, FAQ_ITEMS

router = Router()

# Need db instance here - will be passed from main or imported
# For now, let's assume we can import it or it will be injected
from app.database import db

@router.message(CommandStart())
async def start_handler(message: Message) -> None:
    await upsert_from_user(db, message.from_user)
    has_warranty = await db.has_warranty(message.from_user.id)
    text = "Добро пожаловать! Выберите действие из меню."
    if not has_warranty:
        text = (
            "Добро пожаловать! 👋\n\n"
            "Зарегистрируйтесь сейчас и получите <b>гарантию на 12 месяцев</b> на ваше изделие!\n\n"
            "Выберите действие из меню."
        )
    await message.answer(
        text,
        reply_markup=main_menu_kb(),
        parse_mode="HTML"
    )

@router.message(Command("cancel"))
@router.message(F.text == "Отмена")
async def cancel_handler(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=main_menu_kb())

@router.callback_query(F.data == "cancel")
async def cancel_callback_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()
    await callback.message.answer("Действие отменено.", reply_markup=main_menu_kb())

@router.message(Command("forget_me"))
async def forget_me_handler(message: Message, state: FSMContext) -> None:
    await state.clear()
    await db.delete_user_data(message.from_user.id)
    await message.answer("Все ваши данные были удалены из базы данных.", reply_markup=main_menu_kb())

async def show_user_warranties(message: Message, user_id: int) -> None:
    warranties = await db.get_warranties(user_id)
    if not warranties:
        await message.answer(
            "У вас пока нет зарегистрированных изделий.",
            reply_markup=main_menu_kb()
        )
        return

    text = "📦 <b>Ваши изделия с активной гарантией:</b>\n\n"
    for w in warranties:
        end_date = w['end_date']
        try:
            end_date = dt.date.fromisoformat(end_date).strftime("%d.%m.%Y")
        except:
            pass
        sku = w.get('sku') or 'Изделие'
        cz_code = w.get('cz_code') or ''
        text += f"🔹 <b>{escape(sku)}</b>\n🗓 Гарантия до: {escape(end_date)}\n🔢 Код: <code>{escape(cz_code)}</code>\n\n"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Активировать еще", callback_data="warranty:new")],
        [InlineKeyboardButton(text="🔙 В меню", callback_data="cancel")]
    ])
    
    await message.answer(text, reply_markup=kb, parse_mode="HTML")

@router.message(F.text == "📦 Мои изделия")
@router.callback_query(F.data == "menu:my_items")
async def my_items_handler(event, state: FSMContext = None) -> None:
    message = event.message if isinstance(event, CallbackQuery) else event
    if isinstance(event, CallbackQuery): await event.answer()
    await upsert_from_user(db, event.from_user)
    await show_user_warranties(message, event.from_user.id)

@router.message(F.text == "📌 Мои заявки")
@router.callback_query(F.data == "menu:claims")
async def claims_menu_handler(event) -> None:
    message = event.message if isinstance(event, CallbackQuery) else event
    if isinstance(event, CallbackQuery): await event.answer()
    await upsert_from_user(db, event.from_user)
    claims = await db.list_claims_by_user(event.from_user.id, limit=5)
    if not claims:
        await message.answer("У вас пока нет заявок.", reply_markup=main_menu_kb())
        return
    await message.answer("Ваши последние заявки:", reply_markup=claims_list_kb(claims))

@router.callback_query(F.data == "menu:shop")
async def shopping_callback_handler(callback: CallbackQuery) -> None:
    await callback.answer()
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Каталог", url=os.getenv("CATALOG_URL", "https://ukataka.ru/#katalog"))],
            [InlineKeyboardButton(text="Wildberries", url=os.getenv("WB_URL", "https://www.wildberries.ru/brands/1021574-ukataka"))],
        ]
    )
    await callback.message.answer("Выберите, куда перейти:", reply_markup=kb)

@router.callback_query(F.data == "menu:care")
async def care_callback_handler(callback: CallbackQuery) -> None:
    await callback.answer()
    kb_data = load_kb()
    text = kb_data.get("care", CARE_TEXT)
    links = kb_data.get("links", {}).get("care", DEFAULT_KB["links"]["care"])
    rows = [[InlineKeyboardButton(text=l["label"], url=l["url"])] for l in links]
    await callback.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

@router.callback_query(F.data == "menu:useful")
async def useful_callback_handler(callback: CallbackQuery) -> None:
    await callback.answer()
    kb_data = load_kb()
    text = kb_data.get("useful", "В Telegram мы делимся советами...")
    links = kb_data.get("links", {}).get("useful", DEFAULT_KB["links"]["useful"])
    rows = [[InlineKeyboardButton(text=l["label"], url=l["url"])] for l in links]
    rows.append([InlineKeyboardButton(text="На главную", callback_data="cancel")])
    await callback.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

@router.callback_query(F.data == "menu:trust")
async def trust_callback_handler(callback: CallbackQuery) -> None:
    await callback.answer()
    kb_data = load_kb()
    text = kb_data.get("trust", TRUST_TEXT)
    links = kb_data.get("links", {}).get("trust", DEFAULT_KB["links"]["trust"])
    rows = [[InlineKeyboardButton(text=l["label"], url=l["url"])] for l in links]
    await callback.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

@router.callback_query(F.data == "menu:faq")
async def faq_callback_handler(callback: CallbackQuery) -> None:
    await callback.answer()
    kb_data = load_kb()
    default_faq_text = "❓ FAQ\n\n" + "\n".join([f"• {q}\n  {a}" for q, a in FAQ_ITEMS])
    text = kb_data.get("faq", default_faq_text)
    links = kb_data.get("links", {}).get("faq", DEFAULT_KB["links"]["faq"])
    rows = [[InlineKeyboardButton(text=l["label"], url=l["url"])] for l in links]
    rows.append([InlineKeyboardButton(text="Задать вопрос", callback_data="faq:ask")])
    await callback.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


# --- Задать вопрос: ввод текста -> подсказки по ключевым словам или заявка ---
@router.callback_query(F.data == "faq:ask")
async def faq_ask_start_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(FaqAskStates.waiting_question)
    await callback.message.answer("Введите ваш вопрос текстом:", reply_markup=cancel_kb())


@router.message(FaqAskStates.waiting_question, F.text)
async def faq_ask_text_handler(message: Message, state: FSMContext) -> None:
    question_text = (message.text or "").strip()
    if not question_text:
        await message.answer("Пожалуйста, введите вопрос текстом.", reply_markup=cancel_kb())
        return
    await upsert_from_user(db, message.from_user)
    matched = await db.search_faq_by_keywords(question_text)
    await state.update_data(faq_user_question=question_text)
    if matched:
        await message.answer(
            "Возможно, вы имели в виду:",
            reply_markup=faq_suggestions_kb(matched),
        )
        return
    # Нет совпадений — сразу создаём заявку
    await _create_question_claim(message, state, question_text)


async def _create_question_claim(message: Message, state: FSMContext, question_text: str) -> None:
    claim_number = await db.get_next_claim_number()
    claim_id = str(claim_number)
    await db.create_claim(
        claim_id=claim_id,
        tg_id=message.from_user.id,
        description=question_text,
        purchase_type="Вопрос",
        purchase_value="",
    )
    claim = await db.get_claim(claim_id)
    user_db = await db.get_user(message.from_user.id)
    await send_admin_claim(
        message.bot,
        db,
        claim,
        [],
        message.from_user.username,
        user_db.get("name") if user_db else None,
        user_db.get("phone") if user_db else None,
        user_db.get("email") if user_db else None,
    )
    await state.clear()
    await message.answer(
        "Ваш вопрос отправлен. Мы ответим в ближайшее время.",
        reply_markup=main_menu_kb(),
    )


@router.callback_query(F.data.startswith("faq:answer:"))
async def faq_answer_show_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    # Сохраняем текст вопроса в данных, чтобы после просмотра ответа кнопка «Нет, отправить вопрос менеджеру» всё ещё работала
    data = await state.get_data()
    saved_question = data.get("faq_user_question")
    try:
        qid = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.message.answer("Ответ не найден.", reply_markup=main_menu_kb())
        await state.clear()
        return
    q = await db.get_faq_question(qid)
    if not q:
        await callback.message.answer("Ответ не найден.", reply_markup=main_menu_kb())
        await state.clear()
        return
    await callback.message.answer(
        f"<b>{escape(q['title'])}</b>\n\n{escape(q['answer'])}",
        reply_markup=main_menu_kb(),
        parse_mode="HTML",
    )
    await state.clear()
    if saved_question:
        await state.update_data(faq_user_question=saved_question)


@router.callback_query(F.data == "faq:create_claim")
async def faq_create_claim_handler(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    data = await state.get_data()
    question_text = data.get("faq_user_question") or ""
    if not question_text:
        await callback.message.answer("Вопрос не найден. Напишите вопрос ещё раз.", reply_markup=main_menu_kb())
        await state.clear()
        return
    message = callback.message
    await _create_question_claim(message, state, question_text)

@router.message(Command("check_znack"))
async def check_znack_start_handler(message: Message, state: FSMContext) -> None:
    await upsert_from_user(db, message.from_user)
    await state.set_state(CheckZnackStates.waiting_for_code)
    await message.answer(
        "🔍 Проверка кода Честный знак\n\n"
        "Введите код для проверки:",
        reply_markup=cancel_kb()
    )

@router.message(CheckZnackStates.waiting_for_code)
async def check_znack_code_handler(message: Message, state: FSMContext) -> None:
    if not message.text:
        await message.answer("Пожалуйста, введите код текстом.", reply_markup=cancel_kb())
        return
    
    code = message.text.strip()
    tokens = get_ours_tokens()
    
    if not tokens:
        await message.answer(
            "⚠️ Список кодов OUR_CODES не настроен. Обратитесь к администратору.",
            reply_markup=main_menu_kb()
        )
        await state.clear()
        return
    
    # Проверяем, содержит ли код хотя бы один фрагмент из OUR_CODES
    code_valid = any(token in code for token in tokens)
    
    if code_valid:
        matched_tokens = [token for token in tokens if token in code]
        matched_text = ", ".join(matched_tokens)
        await message.answer(
            f"✅ <b>Код соответствует нашей продукции!</b>\n\n"
            f"Найденные фрагменты: <code>{escape(matched_text)}</code>\n"
            f"Проверенный код: <code>{escape(code)}</code>",
            reply_markup=main_menu_kb(),
            parse_mode="HTML"
        )
    else:
        await message.answer(
            f"❌ <b>Код не относится к нашей продукции.</b>\n\n"
            f"Проверенный код: <code>{escape(code)}</code>\n\n"
            f"Убедитесь, что вы ввели правильный код Честный знак.",
            reply_markup=main_menu_kb(),
            parse_mode="HTML"
        )
    
    await state.clear()

