from aiogram.fsm.state import State, StatesGroup

class ClaimStates(StatesGroup):
    purchase_type = State()
    purchase_cz_photo = State()
    purchase_cz_text = State()
    purchase_email = State()
    purchase_sku = State()
    purchase_receipt_file = State()
    purchase_receipt_text = State()
    purchase_site_receipt_number = State()
    description = State()
    files = State()
    contact_name = State()
    contact_phone = State()

class AdminStates(StatesGroup):
    reply_text = State()
    kb_edit_text = State()
    kb_edit_links = State()
    kb_add_link_label = State()
    kb_add_link_url = State()
    kb_edit_link_label = State()
    kb_edit_link_url = State()

class WarrantyStates(StatesGroup):
    cz_photo = State()
    cz_text = State()
    name = State()
    phone = State()
    email = State()
    sku = State()
    receipt_date_wb = State()   # дата чека из WB
    receipt_number_wb = State()  # номер чека из WB
    receipt_file = State()
    receipt_text = State()

class CheckZnackStates(StatesGroup):
    waiting_for_code = State()


class FaqAskStates(StatesGroup):
    waiting_question = State()


class AdminFaqStates(StatesGroup):
    edit_title = State()
    edit_answer = State()
    edit_keywords = State()
    add_title = State()
    add_answer = State()
    add_keywords = State()

