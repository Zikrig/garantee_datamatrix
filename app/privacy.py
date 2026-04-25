from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.database import db
from app.keyboards import privacy_consent_kb
from app.utils import ADMIN_CHAT_IDS, get_privacy_policy_url


class PrivacyConsentMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        if not user:
            return await handler(event, data)

        if ADMIN_CHAT_IDS and user.id in ADMIN_CHAT_IDS:
            return await handler(event, data)

        has_consent = await db.has_privacy_consent(user.id)
        if has_consent:
            return await handler(event, data)

        if isinstance(event, Message):
            if event.text and event.text.startswith("/start"):
                return await handler(event, data)
            policy_url = await get_privacy_policy_url(db)
            await event.answer(
                "Для доступа к функциям бота нужно согласиться с обработкой персональных данных.",
                reply_markup=privacy_consent_kb(policy_url),
            )
            return None

        if isinstance(event, CallbackQuery):
            if event.data == "privacy:accept":
                return await handler(event, data)
            await event.answer("Сначала подтвердите согласие", show_alert=True)
            policy_url = await get_privacy_policy_url(db)
            if event.message:
                await event.message.answer(
                    "Подтвердите согласие, чтобы продолжить работу.",
                    reply_markup=privacy_consent_kb(policy_url),
                )
            return None

        return await handler(event, data)
