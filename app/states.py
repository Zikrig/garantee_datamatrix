from aiogram.fsm.state import State, StatesGroup

class ClaimStates(StatesGroup):
    description = State()
    purchase_type = State()
    purchase_wb = State()
    purchase_cz_photo = State()
    purchase_cz_text = State()
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
    receipt_pdf = State()
    sku = State()
    name = State()

