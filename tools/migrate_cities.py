"""
from services.location_service import search_locations
from database_turso import get_turso_connection, set_user_filters_turso, get_user_filters_turso

Скрипт миграции существующих пользователей с city_string на city_json
"""
import json
import csv
import asyncio
import logging
from datetime import datetime
from typing import List, Dict, Any

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Импорты
try:
except ImportError as e:
    logger.error(f"Ошибка импорта: {e}")
    raise


async def migrate_user_cities():
    """
    Миграция существующих пользователей:
    - Для каждой записи user_filters где city_json IS NULL and city_string IS NOT NULL
    - Вызывает search_locations(city_string)
    - Если 1 match → автоматом записывает city_json
    - Если multiple -> записывает flag needs_city_review = True и логирует
    """
    conn = get_turso_connection()
    if not conn:
        logger.error("Не удалось подключиться к Turso")
        return
    
    try:
        # Получаем всех пользователей с city_string но без city_json
        cursor = conn.execute("""
            SELECT telegram_id, city 
            FROM user_filters 
            WHERE (city_json IS NULL OR city_json = '') 
            AND city IS NOT NULL 
            AND city != ''
        """)
        
        rows = cursor.fetchall()
        total_users = len(rows)
        logger.info(f"[MIGRATE] Найдено пользователей для миграции: {total_users}")
        
        stats = {
            "total": total_users,
            "auto_migrated": 0,
            "needs_review": 0,
            "not_found": 0,
            "errors": 0
        }
        
        review_list = []
        
        for telegram_id, city_string in rows:
            try:
                logger.info(f"[MIGRATE] Обрабатываю user={telegram_id} city={city_string}")
                
                # Ищем локации
                locations = await search_locations(city_string)
                
                if not locations:
                    # Не найдено
                    stats["not_found"] += 1
                    review_list.append({
                        "telegram_id": telegram_id,
                        "original_string": city_string,
                        "status": "not_found",
                        "choices": []
                    })
                    logger.warning(f"[MIGRATE] user={telegram_id} city={city_string} - не найдено")
                
                elif len(locations) == 1:
                    # Один результат - автоматически мигрируем
                    location = locations[0]
                    
                    # Получаем текущие фильтры
                    filters = await get_user_filters_turso(telegram_id)
                    if filters:
                        filters["city"] = location
                        await set_user_filters_turso(telegram_id, filters)
                        stats["auto_migrated"] += 1
                        logger.info(f"[MIGRATE] user={telegram_id} city={city_string} -> auto_migrated={location.get('name')}")
                    else:
                        logger.warning(f"[MIGRATE] user={telegram_id} - фильтры не найдены")
                        stats["errors"] += 1
                
                else:
                    # Несколько результатов - нужна ручная проверка
                    stats["needs_review"] += 1
                    review_list.append({
                        "telegram_id": telegram_id,
                        "original_string": city_string,
                        "status": "multiple",
                        "choices": [
                            {
                                "id": loc.get("id"),
                                "name": loc.get("name"),
                                "region": loc.get("region"),
                                "type": loc.get("type")
                            }
                            for loc in locations[:5]  # Максимум 5 вариантов
                        ]
                    })
                    logger.warning(f"[MIGRATE] user={telegram_id} city={city_string} - найдено {len(locations)} вариантов, нужна проверка")
            
            except Exception as e:
                stats["errors"] += 1
                logger.error(f"[MIGRATE] Ошибка обработки user={telegram_id}: {e}")
                review_list.append({
                    "telegram_id": telegram_id,
                    "original_string": city_string,
                    "status": "error",
                    "error": str(e),
                    "choices": []
                })
        
        # Выводим статистику
        logger.info(f"[MIGRATE] Статистика миграции:")
        logger.info(f"  Всего пользователей: {stats['total']}")
        logger.info(f"  Автоматически мигрировано: {stats['auto_migrated']}")
        logger.info(f"  Требуют проверки: {stats['needs_review']}")
        logger.info(f"  Не найдено: {stats['not_found']}")
        logger.info(f"  Ошибки: {stats['errors']}")
        
        # Сохраняем CSV с пользователями для ручной проверки
        if review_list:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            csv_filename = f"city_migration_review_{timestamp}.csv"
            
            with open(csv_filename, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=[
                    "telegram_id", "original_string", "status", "choices", "error"
                ])
                writer.writeheader()
                
                for item in review_list:
                    choices_json = json.dumps(item.get("choices", []), ensure_ascii=False)
                    writer.writerow({
                        "telegram_id": item["telegram_id"],
                        "original_string": item["original_string"],
                        "status": item["status"],
                        "choices": choices_json,
                        "error": item.get("error", "")
                    })
            
            logger.info(f"[MIGRATE] CSV файл сохранен: {csv_filename}")
        
        return stats
    
    finally:
        conn.close()


if __name__ == "__main__":
    asyncio.run(migrate_user_cities())
