import asyncio
import io
import logging
import os

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message

from app.scanner import extract_datamatrix


async def handle_datamatrix_from_file(message: Message, bot: Bot, file_id: str) -> None:
    file = await bot.get_file(file_id)
    buffer = io.BytesIO()
    await bot.download_file(file.file_path, destination=buffer)
    codes = extract_datamatrix(buffer.getvalue())
    ours_raw = os.getenv("OUR_CODES", "")
    ours = [item.strip() for item in ours_raw.replace(";", ",").split(",") if item.strip()]
    is_ours = bool(ours) and any(
        any(token in code for token in ours) for code in codes
    )

    if not codes:
        await message.answer("DataMatrix не найден. Попробуйте более четкое фото.")
        return

    suffix = "\nКод наш" if is_ours else "\nКод не наш"
    if len(codes) == 1:
        await message.answer(f"DataMatrix: {codes[0]}{suffix}")
        return

    response = "Найдено несколько DataMatrix:\n" + "\n".join(codes) + suffix
    await message.answer(response)


async def start_handler(message: Message) -> None:
    await message.answer(
        "Отправьте фото с DataMatrix, и я распознаю его и верну значение."
    )


async def photo_handler(message: Message, bot: Bot) -> None:
    photo = message.photo[-1]
    await handle_datamatrix_from_file(message, bot, photo.file_id)


async def document_handler(message: Message, bot: Bot) -> None:
    document = message.document
    if not document or not document.mime_type or not document.mime_type.startswith("image/"):
        await message.answer("Пожалуйста, отправьте изображение с DataMatrix.")
        return
    await handle_datamatrix_from_file(message, bot, document.file_id)


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN is required")

    bot = Bot(token=token)
    dp = Dispatcher()
    dp.message.register(start_handler, CommandStart())
    dp.message.register(photo_handler, F.photo)
    dp.message.register(document_handler, F.document)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

