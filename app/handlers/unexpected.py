from aiogram import Router, F
from aiogram.filters import StateFilter
from aiogram.types import Message, CallbackQuery
from app.keyboards import main_menu_kb

router = Router()

@router.message(StateFilter(None))
async def unexpected_message_handler(message: Message) -> None:
    await message.answer("Выберите действие из меню.", reply_markup=main_menu_kb())

@router.callback_query()
async def unexpected_callback_handler(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer("Выберите действие из меню.", reply_markup=main_menu_kb())

