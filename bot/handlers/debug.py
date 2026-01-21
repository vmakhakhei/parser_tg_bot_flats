"""
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

# Глобальная переменная для DEBUG_FORCE_RUN (можно изменять во время выполнения)
_debug_force_run = False


@router.message(Command("debug"))
async def cmd_debug(message: Message):
    """Обработчик команды /debug run"""
    from bot.utils.admin import is_admin
    
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
    
    _debug_force_run = True
    
    await message.answer("🧪 DEBUG RUN запущен. Принудительный прогон поиска…")
    
    try:
        # Создаем агрегатор и получаем объявления
        aggregator = ListingsAggregator()
        listings = await aggregator.fetch_all_listings(
            city="барановичи",
            min_rooms=1,
            max_rooms=4,
            min_price=0,
            max_price=100000,
        )
        
        # Для debug режима используем все объявления как "новые"
        # В реальном режиме это будут только новые из БД
        await notify_users_about_new_apartments_summary(
            listings,
            force=True
        )
        
        await message.answer(
            f"✅ DEBUG RUN завершён\n"
            f"Найдено объявлений: {len(listings)}\n"
            f"Передано в notify: {len(listings)}"
        )
        
    except Exception as e:
        logger.exception("DEBUG RUN failed")
        await message.answer(f"❌ DEBUG RUN ошибка: {e}")
    
    finally:
        _debug_force_run = False


def get_debug_force_run() -> bool:
    """Возвращает текущее значение DEBUG_FORCE_RUN"""
    global _debug_force_run
    return _debug_force_run or DEBUG_FORCE_RUN
