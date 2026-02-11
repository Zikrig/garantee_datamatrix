from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from app.database import db
from app.keyboards import admin_menu_kb
from app.utils import ADMIN_CHAT_IDS, load_kb, save_kb, DEFAULT_KB
from app.states import AdminStates

router = Router()

@router.callback_query(F.data == "admin:kb_menu")
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

@router.callback_query(F.data.startswith("admin:kb_edit:"))
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

@router.callback_query(F.data.startswith("admin:kb_edit_text:"))
async def admin_kb_text_edit_start(callback: CallbackQuery, state: FSMContext) -> None:
    section = callback.data.split(":")[2]
    await state.set_state(AdminStates.kb_edit_text)
    await callback.message.edit_text(
        f"Отправьте новый текст для раздела '{section}' (поддерживается Markdown).",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data=f"admin:kb_edit:{section}")]])
    )
    await callback.answer()

@router.message(AdminStates.kb_edit_text)
async def admin_kb_save_handler(message: Message, state: FSMContext) -> None:
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

@router.callback_query(F.data.startswith("admin:kb_links:"))
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
    
    await callback.message.edit_text(f"Управление ссылками раздела: {section}", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()

@router.callback_query(F.data.startswith("admin:kb_link_add:"))
async def admin_kb_link_add_start(callback: CallbackQuery, state: FSMContext) -> None:
    section = callback.data.split(":")[3]
    await state.set_state(AdminStates.kb_add_link_label)
    await callback.message.edit_text("Введите название для новой ссылки:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data=f"admin:kb_links:{section}")]]))
    await callback.answer()

@router.message(AdminStates.kb_add_link_label)
async def admin_kb_link_add_label(message: Message, state: FSMContext) -> None:
    await state.update_data(new_link_label=message.text)
    await state.set_state(AdminStates.kb_add_link_url)
    data = await state.get_data()
    await message.answer(f"Теперь введите URL для ссылки '{message.text}':", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data=f"admin:kb_links:{data['kb_section']}")]]))

@router.message(AdminStates.kb_add_link_url)
async def admin_kb_link_add_url(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    section, label, url = data["kb_section"], data["new_link_label"], message.text
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

@router.callback_query(F.data.startswith("admin:kb_link_del:"))
async def admin_kb_link_del_handler(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    section, idx = parts[3], int(parts[4])
    kb_data = load_kb()
    if "links" in kb_data and section in kb_data["links"]:
        if 0 <= idx < len(kb_data["links"][section]):
            del kb_data["links"][section][idx]
            save_kb(kb_data)
            await callback.answer("✅ Ссылка удалена")
    await admin_kb_links_menu_handler(callback, state)

@router.callback_query(F.data.startswith("admin:kb_link_edit:"))
async def admin_kb_link_edit_start(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    section, idx = parts[3], int(parts[4])
    await state.update_data(kb_section=section, edit_link_idx=idx)
    await state.set_state(AdminStates.kb_edit_link_label)
    await callback.message.edit_text("Введите НОВОЕ название для ссылки:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data=f"admin:kb_links:{section}")]]))
    await callback.answer()

@router.message(AdminStates.kb_edit_link_label)
async def admin_kb_link_edit_label(message: Message, state: FSMContext) -> None:
    await state.update_data(edit_link_label=message.text)
    await state.set_state(AdminStates.kb_edit_link_url)
    data = await state.get_data()
    await message.answer(f"Введите НОВЫЙ URL для ссылки '{message.text}':", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data=f"admin:kb_links:{data['kb_section']}")]]))

@router.message(AdminStates.kb_edit_link_url)
async def admin_kb_link_edit_url(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    section, idx, label, url = data["kb_section"], data["edit_link_idx"], data["edit_link_label"], message.text
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

