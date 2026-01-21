"""
Утилиты для нормализации идентификаторов
"""


def normalize_ad_id(ad_id) -> str:
    """
    Нормализует внешний идентификатор объявления.
    Гарантируем: str, без лишних пробелов.
    Примеры:
      'kufar_123' -> 'kufar_123'
      123 -> '123'
    """
    if ad_id is None:
        return ""
    return str(ad_id).strip()


def normalize_telegram_id(tid) -> int:
    """Вернуть int если возможно, иначе поднять ValueError."""
    try:
        return int(tid)
    except Exception:
        raise ValueError(f"Invalid telegram id: {tid}")
