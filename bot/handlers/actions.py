"""
from bot.utils.callback_codec import decode_callback_payload

Обработчики действий пользователя с объявлениями
"""
import logging
from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.enums import ParseMode

logger = logging.getLogger(__name__)
router = Router()


@router.callback_query(F.data.startswith("open_ad:"))
async def open_ad(callback: CallbackQuery):
    """
    Обработчик кнопки "Открыть объявление"
    
    Извлекает URL из callback_data и отправляет его пользователю.
    Telegram не открывает ссылки напрямую по callback, поэтому отправляем сообщением.
    
    Поддерживает как прямые URL, так и закодированные через callback_codec.
    """
    # Извлекаем URL или код из callback_data
    url_or_code = callback.data.replace("open_ad:", "", 1)
    
    # UX: мгновенный ответ (убирает "часики")
    await callback.answer("Открываю объявление…")
    
    # Проверяем, это URL или закодированный код
    if url_or_code.startswith("http"):
        # Это прямой URL
        url = url_or_code
    else:
        # Это закодированный код - декодируем
        url = await decode_callback_payload(url_or_code)
        
        if not url:
            logger.warning(f"[ACTIONS] open_ad не удалось декодировать код: {url_or_code}")
            await callback.message.answer(
                "⚠️ Ошибка: не удалось получить ссылку на объявление.",
                parse_mode=ParseMode.HTML
            )
            return
    
    # Проверяем, что это валидный URL
    if not url or not url.startswith("http"):
        logger.warning(f"[ACTIONS] open_ad получил невалидный URL: {url}")
        await callback.message.answer(
            "⚠️ Ошибка: неверная ссылка на объявление.",
            parse_mode=ParseMode.HTML
        )
        return
    
    # Отправляем ссылку пользователю
    await callback.message.answer(
        f"🔗 <b>Ссылка на объявление:</b>\n{url}",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=False
    )


@router.callback_query(F.data.startswith("save_ad:"))
async def save_ad(callback: CallbackQuery):
    """
    Обработчик кнопки "Сохранить объявление"
    
    Пока без БД — только UX подтверждение.
    Позже можно добавить сохранение в БД/Redis.
    """
    ad_id = callback.data.replace("save_ad:", "", 1)
    
    # Логируем действие для будущей аналитики
    logger.info(f"[ACTIONS] save_ad user={callback.from_user.id} ad_id={ad_id}")
    
    # TODO: Проверить, есть ли в БД
    # TODO: Если есть → "Уже сохранено"
    # TODO: Если нет → сохранить → "Сохранено ⭐"
    
    # Пока без БД — только UX подтверждение
    await callback.answer("Сохранено ⭐")


@router.callback_query(F.data.startswith("mute_ad:"))
async def mute_ad(callback: CallbackQuery):
    """
    Обработчик кнопки "Не показывать"
    
    Пока без БД — только UX подтверждение.
    Позже можно добавить фильтрацию похожих объявлений.
    """
    ad_id = callback.data.replace("mute_ad:", "", 1)
    
    # Логируем действие для будущей аналитики
    logger.info(f"[ACTIONS] mute_ad user={callback.from_user.id} ad_id={ad_id}")
    
    # TODO: Сохранить в БД/Redis паттерн для фильтрации
    # TODO: Фильтровать похожие объявления при следующей проверке
    
    # Пока без БД — только UX подтверждение
    await callback.answer("Похожие объявления больше не будут показываться")
