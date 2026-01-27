#!/usr/bin/env python3
"""
from bot.utils.city_lookup import find_city_slug_by_text
from database_turso import get_user_filters_turso, get_turso_connection
from bot.services.search_service import validate_user_filters

Post-deploy smoke tests
Проверяет критическую функциональность после деплоя
НЕ отправляет сообщения в Telegram - только проверяет логику
"""
import sys
import asyncio
from pathlib import Path

# Добавляем корневую директорию в путь
sys.path.insert(0, str(Path(__file__).parent.parent))


async def test_city_lookup():
    """Тестирует city lookup для нескольких городов"""
    print("[SMOKE] Testing city lookup...")
    
    try:
        
        test_cities = ["Минск", "Барановичи", "Полоцк"]
        all_passed = True
        
        for city in test_cities:
            try:
                results = await find_city_slug_by_text(city, limit=1)
                
                if results and len(results) > 0:
                    slug = results[0].get('slug', 'N/A')
                    label = results[0].get('label_ru', 'N/A')
                    print(f"[SMOKE][OK] {city} → {label} ({slug[:50]}...)")
                else:
                    print(f"[SMOKE][FAIL] {city} → no results found")
                    all_passed = False
                    
            except Exception as e:
                print(f"[SMOKE][FAIL] {city} → error: {e}")
                all_passed = False
        
        return all_passed
        
    except ImportError as e:
        print(f"[SMOKE][FAIL] Could not import city_lookup: {e}")
        return False
    except Exception as e:
        print(f"[SMOKE][FAIL] City lookup test failed: {e}")
        return False


async def test_user_filters_read():
    """Тестирует чтение фильтров пользователя"""
    print("[SMOKE] Testing user filters read...")
    
    try:
        
        # Проверяем, что можем подключиться к БД
        conn = get_turso_connection()
        if not conn:
            print("[SMOKE][FAIL] Could not connect to database")
            return False
        
        # Проверяем наличие таблицы user_filters
        try:
            cursor = conn.execute("SELECT COUNT(*) FROM user_filters")
            count = cursor.fetchone()[0]
            print(f"[SMOKE][OK] user_filters table accessible ({count} records)")
        except Exception as e:
            print(f"[SMOKE][FAIL] user_filters table check failed: {e}")
            conn.close()
            return False
        
        conn.close()
        
        # Пробуем прочитать фильтры для тестового пользователя (если есть)
        # Это не критично, если пользователя нет - просто проверяем, что функция работает
        try:
            # Используем тестовый ID из конфига (если есть)
            test_id = 714797710  # Из config.py ADMIN_TELEGRAM_IDS
            filters = await get_user_filters_turso(test_id)
            
            if filters is not None:
                print(f"[SMOKE][OK] Can read filters for user {test_id}")
            else:
                print(f"[SMOKE][OK] No filters for user {test_id} (this is OK)")
        except Exception as e:
            # Это не критично - просто проверяем, что функция не падает
            print(f"[SMOKE][WARNING] Could not read filters: {e} (non-critical)")
        
        return True
        
    except ImportError as e:
        print(f"[SMOKE][FAIL] Could not import database_turso: {e}")
        return False
    except Exception as e:
        print(f"[SMOKE][FAIL] User filters read test failed: {e}")
        return False


async def test_filter_validation():
    """Тестирует валидацию фильтров (без блокировки)"""
    print("[SMOKE] Testing filter validation...")
    
    try:
        
        # Тест 1: Валидные фильтры
        valid_filters = {
            "city": "минск",
            "min_rooms": 1,
            "max_rooms": 3,
            "min_price": 0,
            "max_price": 100000,
        }
        
        is_valid, error = validate_user_filters(valid_filters)
        if is_valid:
            print("[SMOKE][OK] Valid filters pass validation")
        else:
            print(f"[SMOKE][FAIL] Valid filters failed validation: {error}")
            return False
        
        # Тест 2: Невалидные фильтры (без города)
        invalid_filters = {
            "min_rooms": 1,
            "max_rooms": 3,
        }
        
        is_valid, error = validate_user_filters(invalid_filters)
        if not is_valid:
            print(f"[SMOKE][OK] Invalid filters correctly rejected: {error}")
        else:
            print("[SMOKE][FAIL] Invalid filters should be rejected")
            return False
        
        return True
        
    except ImportError as e:
        print(f"[SMOKE][FAIL] Could not import search_service: {e}")
        return False
    except Exception as e:
        print(f"[SMOKE][FAIL] Filter validation test failed: {e}")
        return False


async def main():
    """Основная функция smoke tests"""
    print("=" * 60)
    print("[SMOKE] Starting post-deploy smoke tests...")
    print("=" * 60)
    
    tests = [
        ("City Lookup", await test_city_lookup()),
        ("User Filters Read", await test_user_filters_read()),
        ("Filter Validation", await test_filter_validation()),
    ]
    
    failed = []
    for test_name, result in tests:
        if not result:
            failed.append(test_name)
    
    print("=" * 60)
    if failed:
        print(f"[SMOKE][FAIL] Smoke tests failed: {', '.join(failed)}")
        sys.exit(1)
    else:
        print("[SMOKE][OK] All smoke tests passed")
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
