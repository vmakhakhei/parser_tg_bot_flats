"""
Админ-команды для управления ботом
"""

import logging
from aiogram import Router
from aiogram.types import Message
from aiogram.filters import Command

from bot.utils.admin import is_admin
from database_turso import delete_sent_ads_for_user

logger = logging.getLogger(__name__)
router = Router()


@router.message(Command("admin_clear_sent"))
async def cmd_admin_clear_sent(message: Message):
    """Админ-команда для очистки sent_ads для пользователя"""
    # Проверка прав администратора
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Команда доступна только администратору.")
        return
    
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer(
            "Использование: /admin_clear_sent <telegram_id>\n\n"
            "Удаляет все записи sent_ads для указанного пользователя."
        )
        return
    
    try:
        telegram_id = int(parts[1])
    except ValueError:
        await message.answer(f"❌ Неверный формат telegram_id: {parts[1]}")
        return
    
    # Получаем количество записей до удаления
    from database_turso import get_turso_connection
    import asyncio
    
    conn = get_turso_connection()
    if not conn:
        await message.answer("❌ Не удалось подключиться к базе данных")
        return
    
    try:
        def _get_count():
            cursor = conn.execute(
                "SELECT COUNT(*) as cnt FROM sent_ads WHERE telegram_id = ?",
                (telegram_id,)
            )
            result = cursor.fetchone()
            return result[0] if result else 0
        
        count_before = await asyncio.to_thread(_get_count)
        
        # Удаляем записи
        remaining = await delete_sent_ads_for_user(telegram_id)
        
        if remaining == -1:
            await message.answer(f"❌ Ошибка при удалении записей для пользователя {telegram_id}")
        else:
            deleted = count_before - remaining
            await message.answer(
                f"✅ Удалено записей sent_ads для пользователя {telegram_id}:\n"
                f"• До удаления: {count_before}\n"
                f"• Удалено: {deleted}\n"
                f"• Осталось: {remaining}"
            )
            logger.info(f"[admin] Удалено {deleted} записей sent_ads для пользователя {telegram_id}")
    except Exception as e:
        logger.exception(f"[admin] Ошибка при очистке sent_ads для пользователя {telegram_id}")
        await message.answer(f"❌ Ошибка: {e}")
    finally:
        if conn:
            conn.close()
