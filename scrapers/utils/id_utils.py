"""
Утилиты для нормализации идентификаторов
"""


def normalize_ad_id(ad_id) -> str:
    """Нормализует внешний идентификатор объявления в единый строковый вид."""
    if ad_id is None:
        return ""
    return str(ad_id).strip()


def normalize_telegram_id(tid) -> int:
    """Приводит telegram id к int. При некорректном значении — пробросить ValueError."""
    if tid is None:
        raise ValueError("telegram_id is None")
    try:
        return int(tid)
    except Exception as e:
        raise ValueError(f"Invalid telegram id: {tid}") from e
