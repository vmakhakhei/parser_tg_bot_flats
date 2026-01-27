"""
from bot.utils.admin import is_admin
from bot.services.search_service import check_new_listings

Обработчики debug-команд для тестирования системы
"""

import logging
from aiogram import Router
from aiogram.types import Message
from aiogram.filters import Command

from constants.constants import DEBUG_FORCE_RUN
from scrapers.aggregator import ListingsAggregator
from bot.services.notification_service import notify_users_about_new_apartments_summary

logger = logging.getLogger(__name__)
router = Router()

# Глобальные переменные для DEBUG режима (можно изменять во время выполнения)
_debug_force_run = False
_debug_bypass_summary = False
_debug_ignore_sent_ads = False
_debug_skip_filter_validation = False


@router.message(Command("debug"))
async def cmd_debug(message: Message):
    """Обработчик команды /debug run"""
    
    # Проверка прав администратора
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Команда доступна только администратору.")
        return
    
    global _debug_force_run
    
    parts = message.text.split()
    if len(parts) < 2 or parts[1] != "run":
        await message.answer(
            "Использование: /debug run\n\n"
            "Запускает принудительный прогон поиска и отправку уведомлений."
        )
        return
    
    global _debug_force_run, _debug_bypass_summary, _debug_ignore_sent_ads, _debug_skip_filter_validation
    
    _debug_force_run = True
    _debug_bypass_summary = True
    _debug_ignore_sent_ads = True
    _debug_skip_filter_validation = True
    
    await message.answer("🧪 DEBUG RUN запущен. Принудительный прогон поиска…")
    
    try:
        # Используем check_new_listings с флагами для DEBUG режима
        
        await check_new_listings(
            bot=message.bot,
            force_send=True,
            ignore_sent_ads=True,
            bypass_summary=True
        )
        
        await message.answer(
            f"✅ DEBUG RUN завершён\n"
            f"Запущен принудительный поиск с игнорированием sent_ads и summary"
        )
        
    except Exception as e:
        logger.exception("DEBUG RUN failed")
        await message.answer(f"❌ DEBUG RUN ошибка: {e}")
    
    finally:
        _debug_force_run = False
        _debug_bypass_summary = False
        _debug_ignore_sent_ads = False
        _debug_skip_filter_validation = False


def get_debug_force_run() -> bool:
    """Возвращает текущее значение DEBUG_FORCE_RUN"""
    global _debug_force_run
    return _debug_force_run or DEBUG_FORCE_RUN


def get_debug_bypass_summary() -> bool:
    """Возвращает текущее значение DEBUG_BYPASS_SUMMARY"""
    global _debug_bypass_summary
    return _debug_bypass_summary


def get_debug_ignore_sent_ads() -> bool:
    """Возвращает текущее значение DEBUG_IGNORE_SENT_ADS"""
    global _debug_ignore_sent_ads
    return _debug_ignore_sent_ads


def get_debug_skip_filter_validation() -> bool:
    """Возвращает текущее значение DEBUG_SKIP_FILTER_VALIDATION"""
    global _debug_skip_filter_validation
    return _debug_skip_filter_validation
