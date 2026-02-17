# Admin FAQ: list, add, edit (title, answer, keywords), delete
import json
from html import escape
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from app.database import db
from app.keyboards import admin_menu_kb, faq_questions_list_kb, faq_question_edit_kb, QUESTIONS_PER_PAGE
from app.utils import ADMIN_CHAT_IDS
from app.states import AdminFaqStates

router = Router()


def _keywords_list(question: dict) -> list[str]:
    try:
        return json.loads(question.get("keywords") or "[]")
    except Exception:
        return []


@router.callback_query(F.data == "admin:faq")
@router.callback_query(F.data.startswith("admin:faq_page:"))
async def admin_faq_list_handler(callback: CallbackQuery, state: FSMContext) -> None:
    if not ADMIN_CHAT_IDS or callback.from_user.id not in ADMIN_CHAT_IDS:
        await callback.answer("Недостаточно прав")
        return
    await state.clear()
    page = 0
    if callback.data.startswith("admin:faq_page:"):
        try:
            page = int(callback.data.split(":")[2])
        except (IndexError, ValueError):
            pass
    offset = page * QUESTIONS_PER_PAGE
    questions = await db.list_faq_questions(limit=QUESTIONS_PER_PAGE, offset=offset)
    total = await db.count_faq_questions()
    if not questions and page == 0:
        text = "Вопросов пока нет. Добавьте первый вопрос."
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="➕ Добавить вопрос", callback_data="admin:faq_add")],
                [InlineKeyboardButton(text="🔙 В меню админа", callback_data="admin:menu")],
            ]
        )
    else:
        text = f"❓ Вопросы (по алфавиту, страница {page + 1}). Всего: {total}"
        kb = faq_questions_list_kb(questions, page, total)
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("admin:faq_edit:"))
async def admin_faq_edit_handler(callback: CallbackQuery, state: FSMContext) -> None:
    if not ADMIN_CHAT_IDS or callback.from_user.id not in ADMIN_CHAT_IDS:
        await callback.answer("Недостаточно прав")
        return
    await state.clear()
    try:
        qid = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("Ошибка")
        return
    q = await db.get_faq_question(qid)
    if not q:
        await callback.answer("Вопрос не найден")
        return
    keywords = ", ".join(_keywords_list(q))
    text = (
        f"<b>{escape(q['title'])}</b>\n\n"
        f"Ответ: {escape(q['answer'][:200])}{'…' if len(q['answer']) > 200 else ''}\n\n"
        f"Ключевые слова: {escape(keywords) or '—'}"
    )
    await callback.message.edit_text(text, reply_markup=faq_question_edit_kb(qid), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("admin:faq_set_title:"))
async def admin_faq_set_title_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not ADMIN_CHAT_IDS or callback.from_user.id not in ADMIN_CHAT_IDS:
        await callback.answer("Недостаточно прав")
        return
    try:
        qid = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("Ошибка")
        return
    await state.set_state(AdminFaqStates.edit_title)
    await state.update_data(faq_edit_id=qid)
    await callback.message.edit_text(
        "Введите новое название вопроса:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data=f"admin:faq_edit:{qid}")]]
        )
    )
    await callback.answer()


@router.message(AdminFaqStates.edit_title, F.text)
async def admin_faq_set_title_done(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    qid = data.get("faq_edit_id")
    if not qid:
        await state.clear()
        return
    q = await db.get_faq_question(qid)
    if not q:
        await message.answer("Вопрос не найден.")
        await state.clear()
        return
    title = message.text.strip() or q["title"]
    keywords = _keywords_list(q)
    await db.update_faq_question(qid, title, q["answer"], keywords)
    await state.clear()
    q = await db.get_faq_question(qid)
    keywords_str = ", ".join(_keywords_list(q))
    await message.answer(
        "✅ Название обновлено.\n\n"
        f"<b>{escape(q['title'])}</b>\n\nОтвет: {escape(q['answer'][:200])}{'…' if len(q['answer']) > 200 else ''}\n\nКлючевые слова: {escape(keywords_str) or '—'}",
        reply_markup=faq_question_edit_kb(qid),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("admin:faq_set_answer:"))
async def admin_faq_set_answer_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not ADMIN_CHAT_IDS or callback.from_user.id not in ADMIN_CHAT_IDS:
        await callback.answer("Недостаточно прав")
        return
    try:
        qid = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("Ошибка")
        return
    await state.set_state(AdminFaqStates.edit_answer)
    await state.update_data(faq_edit_id=qid)
    await callback.message.edit_text(
        "Введите новый текст ответа:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data=f"admin:faq_edit:{qid}")]]
        )
    )
    await callback.answer()


@router.message(AdminFaqStates.edit_answer, F.text)
async def admin_faq_set_answer_done(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    qid = data.get("faq_edit_id")
    if not qid:
        await state.clear()
        return
    q = await db.get_faq_question(qid)
    if not q:
        await message.answer("Вопрос не найден.")
        await state.clear()
        return
    answer = message.text.strip() or q["answer"]
    keywords = _keywords_list(q)
    await db.update_faq_question(qid, q["title"], answer, keywords)
    await state.clear()
    q = await db.get_faq_question(qid)
    keywords_str = ", ".join(_keywords_list(q))
    await message.answer(
        "✅ Ответ обновлён.\n\n"
        f"<b>{escape(q['title'])}</b>\n\nОтвет: {escape(q['answer'][:200])}{'…' if len(q['answer']) > 200 else ''}\n\nКлючевые слова: {escape(keywords_str) or '—'}",
        reply_markup=faq_question_edit_kb(qid),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("admin:faq_set_keywords:"))
async def admin_faq_set_keywords_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not ADMIN_CHAT_IDS or callback.from_user.id not in ADMIN_CHAT_IDS:
        await callback.answer("Недостаточно прав")
        return
    try:
        qid = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("Ошибка")
        return
    await state.set_state(AdminFaqStates.edit_keywords)
    await state.update_data(faq_edit_id=qid)
    q = await db.get_faq_question(qid)
    current = ", ".join(_keywords_list(q)) if q else ""
    await callback.message.edit_text(
        f"Введите ключевые слова через запятую (по ним будет находиться вопрос):\n\nСейчас: {escape(current) or '—'}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data=f"admin:faq_edit:{qid}")]]
        ),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminFaqStates.edit_keywords, F.text)
async def admin_faq_set_keywords_done(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    qid = data.get("faq_edit_id")
    if not qid:
        await state.clear()
        return
    q = await db.get_faq_question(qid)
    if not q:
        await message.answer("Вопрос не найден.")
        await state.clear()
        return
    keywords = [k.strip() for k in message.text.strip().split(",") if k.strip()]
    await db.update_faq_question(qid, q["title"], q["answer"], keywords)
    await state.clear()
    q = await db.get_faq_question(qid)
    keywords_str = ", ".join(_keywords_list(q))
    await message.answer(
        "✅ Ключевые слова обновлены.\n\n"
        f"<b>{escape(q['title'])}</b>\n\nОтвет: {escape(q['answer'][:200])}{'…' if len(q['answer']) > 200 else ''}\n\nКлючевые слова: {escape(keywords_str) or '—'}",
        reply_markup=faq_question_edit_kb(qid),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("admin:faq_del:"))
async def admin_faq_del_handler(callback: CallbackQuery, state: FSMContext) -> None:
    if not ADMIN_CHAT_IDS or callback.from_user.id not in ADMIN_CHAT_IDS:
        await callback.answer("Недостаточно прав")
        return
    await state.clear()
    try:
        qid = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("Ошибка")
        return
    await db.delete_faq_question(qid)
    await callback.answer("Вопрос удалён.")
    questions = await db.list_faq_questions(limit=QUESTIONS_PER_PAGE, offset=0)
    total = await db.count_faq_questions()
    text = f"❓ Вопросы (по алфавиту). Всего: {total}" if total else "Вопросов пока нет."
    await callback.message.edit_text(text, reply_markup=faq_questions_list_kb(questions, 0, total))


# --- Add new question ---
@router.callback_query(F.data == "admin:faq_add")
async def admin_faq_add_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not ADMIN_CHAT_IDS or callback.from_user.id not in ADMIN_CHAT_IDS:
        await callback.answer("Недостаточно прав")
        return
    await state.set_state(AdminFaqStates.add_title)
    await callback.message.edit_text(
        "Введите название вопроса (как оно будет отображаться в подсказках):",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data="admin:faq")]]
        )
    )
    await callback.answer()


@router.message(AdminFaqStates.add_title, F.text)
async def admin_faq_add_title_done(message: Message, state: FSMContext) -> None:
    await state.update_data(faq_add_title=message.text.strip())
    await state.set_state(AdminFaqStates.add_answer)
    await message.answer(
        "Введите текст ответа на вопрос:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data="admin:faq")]]
        ),
    )


@router.message(AdminFaqStates.add_answer, F.text)
async def admin_faq_add_answer_done(message: Message, state: FSMContext) -> None:
    await state.update_data(faq_add_answer=message.text.strip())
    await state.set_state(AdminFaqStates.add_keywords)
    await message.answer(
        "Введите ключевые слова через запятую (по ним будет находиться этот вопрос):",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data="admin:faq")]]
        ),
    )


@router.message(AdminFaqStates.add_keywords, F.text)
async def admin_faq_add_keywords_done(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    title = data.get("faq_add_title") or "Вопрос"
    answer = data.get("faq_add_answer") or "Ответ"
    keywords = [k.strip() for k in message.text.strip().split(",") if k.strip()]
    await db.create_faq_question(title, answer, keywords)
    await state.clear()
    await message.answer("✅ Вопрос добавлен.", reply_markup=admin_menu_kb())
