from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from app.constants import MAIN_MENU

def main_menu_kb() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text=MAIN_MENU[0], callback_data="menu:warranty"),
        ],
        [
            InlineKeyboardButton(text=MAIN_MENU[1], callback_data="menu:claim"),
        ],
        [
            InlineKeyboardButton(text=MAIN_MENU[2], callback_data="menu:my_items"),
            InlineKeyboardButton(text=MAIN_MENU[3], callback_data="menu:claims"),
        ],
        [
            InlineKeyboardButton(text=MAIN_MENU[4], callback_data="menu:shop"),
            InlineKeyboardButton(text=MAIN_MENU[5], callback_data="menu:care"),
        ],
        [
            InlineKeyboardButton(text=MAIN_MENU[6], callback_data="menu:useful"),
            InlineKeyboardButton(text=MAIN_MENU[7], callback_data="menu:trust"),
        ],
        [
            InlineKeyboardButton(text=MAIN_MENU[8], callback_data="menu:faq"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)

def purchase_type_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Чек WB", callback_data="purchase:wb"),
                InlineKeyboardButton(text="Честный знак", callback_data="purchase:cz"),
            ],
            [InlineKeyboardButton(text="Отмена", callback_data="cancel")],
        ]
    )

def files_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Готово", callback_data="files:done"),
                InlineKeyboardButton(text="Отмена", callback_data="cancel"),
            ]
        ]
    )

def skip_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Пропустить", callback_data="skip:phone"),
                InlineKeyboardButton(text="Отмена", callback_data="cancel"),
            ]
        ]
    )

def claim_status_kb(claim_id: str, status: str = "Новая", is_group: bool = True, group_link: str | None = None) -> InlineKeyboardMarkup:
    rows = []
    
    if is_group:
        if status == "Новая":
            rows.append([InlineKeyboardButton(text="🛠 В работу", callback_data=f"status:{claim_id}:В работе")])
        
        btn_clarify = InlineKeyboardButton(
            text="❓ Нужны уточнения" if status != "Нужны уточнения" else "✅ Нужны уточнения (активно)",
            callback_data=f"status:{claim_id}:Нужны уточнения"
        )
        btn_resolved = InlineKeyboardButton(
            text="🟢 Решено" if status != "Решено" else "✅ Решено (активно)",
            callback_data=f"status:{claim_id}:Решено"
        )
        
        rows.append([btn_clarify])
        rows.append([btn_resolved])
    
    if group_link:
        rows.append([InlineKeyboardButton(text="➡️ Перейти к заявке", url=group_link)])
    
    return InlineKeyboardMarkup(inline_keyboard=rows)

def claims_list_kb(claims: list[dict], group_id: str | None = None, filter_type: str = "all", page: int = 0, total_count: int = 0, limit: int = 20) -> InlineKeyboardMarkup:
    rows = []
    for item in claims:
        status_icon = "🆕" if item['status'] == "Новая" else "🛠" if item['status'] == "В работе" else "🟢" if item['status'] == "Решено" else "❓"
        
        topic_link = ""
        if group_id:
            clean_group_id = group_id.replace("-100", "")
            if item.get("group_message_id"):
                topic_link = f"https://t.me/c/{clean_group_id}/{item['group_message_id']}"
            elif item.get("thread_id"):
                topic_link = f"https://t.me/c/{clean_group_id}/{item['thread_id']}"
        
        btn_text = f"{status_icon} {item['id']} — {item['status']}"
        
        row = [InlineKeyboardButton(text=btn_text, callback_data=f"claim:{item['id']}")]
        if topic_link:
            row.append(InlineKeyboardButton(text="➡️ Перейти", url=topic_link))
        
        rows.append(row)
    
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"admin:list_claims:{filter_type}:{page-1}"))
    
    if (page + 1) * limit < total_count:
        nav_row.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=f"admin:list_claims:{filter_type}:{page+1}"))
    
    if nav_row:
        rows.append(nav_row)
    
    rows.append([InlineKeyboardButton(text="❌ Закрыть", callback_data="cancel")])
        
    return InlineKeyboardMarkup(inline_keyboard=rows)

def link_kb(label: str, url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=label, url=url)]]
    )

def cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data="cancel")]]
    )

def warranties_selection_kb(warranties: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for w in warranties:
        sku = w.get("sku") or "Без артикула"
        cz = w.get("cz_code") or ""
        display_cz = (cz[:10] + "...") if len(cz) > 10 else cz
        rows.append([
            InlineKeyboardButton(
                text=f"📦 {sku} ({display_cz})",
                callback_data=f"select_w:{w['id']}"
            )
        ])
    rows.append([InlineKeyboardButton(text="Другой (через Чек/ЧЗ)", callback_data="select_w:other")])
    rows.append([InlineKeyboardButton(text="Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def admin_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📋 Все заявки", callback_data="admin:list_claims:all")],
            [InlineKeyboardButton(text="📨 Новые заявки", callback_data="admin:list_claims:new")],
            [InlineKeyboardButton(text="📚 База знаний", callback_data="admin:kb_menu")],
            [InlineKeyboardButton(text="❌ Закрыть", callback_data="cancel")]
        ]
    )

