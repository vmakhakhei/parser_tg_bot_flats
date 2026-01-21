"""
Утилиты для работы с администраторами бота
"""

from config import ADMIN_TELEGRAM_IDS


def is_admin(telegram_id: int) -> bool:
    """
    Проверяет, является ли пользователь администратором.
    
    Args:
        telegram_id: ID пользователя в Telegram
    
    Returns:
        True если пользователь является администратором, False иначе
    """
    return telegram_id in ADMIN_TELEGRAM_IDS
