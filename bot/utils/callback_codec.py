"""
Утилита для кодирования и декодирования длинных callback_data.
Позволяет обходить ограничение Telegram в 64 байта.
"""
import hashlib
import logging
from typing import Optional

from database_turso import save_short_link, get_payload_from_code

logger = logging.getLogger(__name__)

def generate_short_code(payload: str) -> str:
    """
    Генерирует детерминированный короткий код для произвольной строки.
    Использует MD5 хеш, усеченный до 12 символов.
    """
    return hashlib.md5(payload.encode()).hexdigest()[:12]

async def encode_callback_payload(payload: str) -> str:
    """
    Сохраняет полный payload в базе данных и возвращает короткий код.
    """

    if not payload:
        return ""

    # Если payload уже короткий, можно было бы его не кодировать,
    # но для единообразия кодируем всё, что проходит через этот механизм.
    code = generate_short_code(payload)
    await save_short_link(code, payload)
    return code

async def decode_callback_payload(code: str) -> Optional[str]:
    """
    Получает полный payload по короткому коду из базы данных.
    """

    if not code:
        return None

    return await get_payload_from_code(code)
