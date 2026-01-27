"""
from database_turso import get_turso_connection
from constants.constants import LOG_KUFAR_LOOKUP
from scrapers.kufar import lookup_kufar_location
from database_turso import set_kufar_city_cache
from scrapers.kufar import lookup_kufar_location_async
from pathlib import Path

Админ-команды для управления ботом
"""

import logging
from aiogram import Router
from aiogram.types import Message
from aiogram.filters import Command

from bot.utils.admin import is_admin
from database_turso import (
    delete_sent_ads_for_user,
    find_stale_sent_ads,
    cleanup_stale_sent_ads,
    check_sent_ads_sync,
    list_stale_sent_ads,
    load_city_map_from_json,
    ensure_city_codes_table
)

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


@router.message(Command("admin_check_sync"))
async def cmd_admin_check_sync(message: Message):
    """Админ-команда для проверки синхронизации sent_ads ↔ apartments"""
    # Проверка прав администратора
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Команда доступна только администратору.")
        return
    
    try:
        sync_info = await check_sent_ads_sync()
        
        if "error" in sync_info:
            await message.answer(f"❌ Ошибка проверки синхронизации: {sync_info['error']}")
            return
        
        is_synced = sync_info.get("is_synced", False)
        status_emoji = "✅" if is_synced else "⚠️"
        
        await message.answer(
            f"{status_emoji} **Синхронизация sent_ads ↔ apartments**\n\n"
            f"• Всего записей в sent_ads: {sync_info['total_sent_ads']}\n"
            f"• Всего объявлений в apartments: {sync_info['total_apartments']}\n"
            f"• Стейл записей: {sync_info['stale_count']}\n"
            f"• Процент синхронизации: {sync_info['sync_percent']:.2f}%\n"
            f"• Статус: {'Синхронизировано' if is_synced else 'Есть рассинхронизация'}\n\n"
            f"Используйте /admin_cleanup_stale для очистки стейл записей."
        )
        logger.info(f"[admin] Проверка синхронизации: {sync_info}")
    except Exception as e:
        logger.exception(f"[admin] Ошибка при проверке синхронизации")
        await message.answer(f"❌ Ошибка: {e}")


@router.message(Command("admin_cleanup_stale"))
async def cmd_admin_cleanup_stale(message: Message):
    """Админ-команда для очистки стейл записей sent_ads"""
    # Проверка прав администратора
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Команда доступна только администратору.")
        return
    
    parts = message.text.split()
    dry_run = "--dry-run" not in parts and "dry-run" not in parts
    
    try:
        # Сначала проверяем количество стейл записей
        stale_records = await find_stale_sent_ads()
        stale_count = len(stale_records)
        
        if stale_count == 0:
            await message.answer("✅ Стейл записей не найдено. База данных синхронизирована.")
            return
        
        if not dry_run:
            # Подтверждение для реального удаления
            await message.answer(
                f"⚠️ Найдено {stale_count} стейл записей.\n\n"
                f"Для удаления отправьте команду:\n"
                f"`/admin_cleanup_stale confirm`"
            )
            return
        
        if len(parts) >= 2 and parts[1] == "confirm":
            # Выполняем реальное удаление
            result = await cleanup_stale_sent_ads(dry_run=False)
            
            await message.answer(
                f"✅ **Очистка стейл записей завершена**\n\n"
                f"• Найдено стейл записей: {result['total_stale']}\n"
                f"• Удалено: {result['deleted']}\n"
                f"• Ошибок: {result['errors']}\n\n"
                f"Используйте /admin_check_sync для проверки синхронизации."
            )
            logger.info(f"[admin] Очистка стейл записей: {result}")
        else:
            # Dry run - только показываем информацию
            result = await cleanup_stale_sent_ads(dry_run=True)
            
            await message.answer(
                f"🔍 **Dry-run: проверка стейл записей**\n\n"
                f"• Найдено стейл записей: {result['total_stale']}\n"
                f"• Удаление не выполнено (dry-run режим)\n\n"
                f"Для реального удаления отправьте:\n"
                f"`/admin_cleanup_stale confirm`"
            )
    except Exception as e:
        logger.exception(f"[admin] Ошибка при очистке стейл записей")
        await message.answer(f"❌ Ошибка: {e}")


@router.message(Command("admin_list_stale_sent"))
async def cmd_admin_list_stale_sent(message: Message):
    """Админ-команда для просмотра стейл записей sent_ads"""
    # Проверка прав администратора
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Команда доступна только администратору.")
        return
    
    try:
        stale_records = await list_stale_sent_ads(limit=100)
        
        if not stale_records:
            await message.answer("✅ Стейл записей не найдено. База данных синхронизирована.")
            return
        
        # Формируем сообщение с первыми 20 записями (из-за лимита Telegram)
        lines = [f"📋 **Стейл записи sent_ads (первые {min(20, len(stale_records))} из {len(stale_records)}):**\n"]
        
        for i, (ad_external_id, telegram_id, sent_at) in enumerate(stale_records[:20], 1):
            lines.append(f"{i}. `{ad_external_id}` → user={telegram_id} ({sent_at})")
        
        if len(stale_records) > 20:
            lines.append(f"\n... и ещё {len(stale_records) - 20} записей")
        
        lines.append(f"\nИспользуйте /admin_cleanup_stale confirm для очистки.")
        
        await message.answer("\n".join(lines))
        logger.info(f"[admin] Показано {min(20, len(stale_records))} стейл записей из {len(stale_records)}")
    except Exception as e:
        logger.exception(f"[admin] Ошибка при получении списка стейл записей")
        await message.answer(f"❌ Ошибка: {e}")


@router.message(Command("admin_kufar_city_lookup"))
async def cmd_admin_kufar_city_lookup(message: Message):
    """Админ-команда для lookup города в Kufar API"""
    import asyncio
    
    # Проверка прав администратора
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Команда доступна только администратору.")
        return
    
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(
            "Использование: /admin_kufar_city_lookup <город>\n\n"
            "Пример: /admin_kufar_city_lookup Полоцк"
        )
        return
    
    city_name = parts[1].strip()
    
    try:
        logger.info(f"{LOG_KUFAR_LOOKUP} admin command city={city_name}")
        
        # Вызываем async lookup
        result = await lookup_kufar_location_async(city_name)
        
        if result:
            # Формируем ответ
            if isinstance(result, list):
                lines = [f"[KUFAR_LOOKUP] {city_name} → найдено {len(result)} вариантов:\n"]
                for i, item in enumerate(result[:5], 1):  # Максимум 5 вариантов
                    name = item.get("name", "unknown")
                    item_id = item.get("id", "N/A")
                    region = item.get("region", "")
                    lines.append(f"  {i}. {name} (id={item_id})")
                    if region:
                        lines.append(f"     регион: {region}")
                if len(result) > 5:
                    lines.append(f"\n... и ещё {len(result) - 5} вариантов")
            elif isinstance(result, dict):
                name = result.get("name", "unknown")
                item_id = result.get("id", "N/A")
                region = result.get("region", "")
                lines = [
                    f"[KUFAR_LOOKUP] {city_name} → найдено:\n",
                    f"  - {name} (id={item_id})"
                ]
                if region:
                    lines.append(f"    регион: {region}")
            else:
                lines = [f"[KUFAR_LOOKUP] {city_name} → результат: {result}"]
            
            # Сохраняем в кэш
            try:
                await set_kufar_city_cache(city_name.lower().strip(), result)
                lines.append("\n✅ Результат сохранен в кэш")
            except Exception as e:
                logger.warning(f"{LOG_KUFAR_LOOKUP} cache save failed: {e}")
                lines.append(f"\n⚠️ Не удалось сохранить в кэш: {e}")
            
            await message.answer("\n".join(lines))
        else:
            await message.answer(
                f"[KUFAR_LOOKUP] {city_name} → ничего не найдено.\n\n"
                f"API Kufar не даёт подсказки для этого города."
            )
            logger.info(f"{LOG_KUFAR_LOOKUP} {city_name} → not found")
    
    except Exception as e:
        logger.exception(f"{LOG_KUFAR_LOOKUP} admin command failed: {e}")
        await message.answer(f"❌ Ошибка: {e}")


@router.message(Command("admin_refresh_city_map"))
async def cmd_admin_refresh_city_map(message: Message):
    """Админ-команда для обновления карты городов из JSON файла"""
    # Проверка прав администратора
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Команда доступна только администратору.")
        return
    
    try:
        import os
        
        # Путь к JSON файлу
        repo_root = Path(__file__).parent.parent.parent
        json_path = repo_root / 'data' / 'kufar_city_map.json'
        
        if not os.path.exists(json_path):
            await message.answer(
                f"❌ Файл не найден: {json_path}\n\n"
                "Сначала запустите:\n"
                "`python tools/build_city_map_from_candidates.py`"
            )
            return
        
        # Убеждаемся, что таблица существует
        await ensure_city_codes_table()
        
        # Загружаем данные
        await message.answer("🔄 Загрузка карты городов...")
        imported_count = await load_city_map_from_json(str(json_path))
        
        if imported_count > 0:
            await message.answer(
                f"✅ Карта городов обновлена!\n\n"
                f"• Импортировано записей: {imported_count}\n"
                f"• Источник: `{json_path}`"
            )
            logger.info(f"[admin] City map refreshed: {imported_count} records imported")
        else:
            await message.answer(
                "⚠️ Не удалось импортировать записи. Проверьте формат JSON файла."
            )
    
    except Exception as e:
        logger.exception(f"[admin] Ошибка при обновлении city_map")
        await message.answer(f"❌ Ошибка: {e}")
