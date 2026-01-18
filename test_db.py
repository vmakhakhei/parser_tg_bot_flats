"""
Тестовый скрипт для проверки динамических фильтров и работы с Turso
"""
import asyncio
import json
import sys
import os
from datetime import datetime

# Добавляем родительскую директорию в path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database_turso import (
    get_turso_connection,
    build_dynamic_query,
    ensure_tables_exist,
    sync_apartment_from_kufar
)
from config import USE_TURSO_CACHE, TURSO_DB_URL, TURSO_AUTH_TOKEN


async def create_test_apartments():
    """Создает тестовые записи в таблице apartments"""
    print("=" * 60)
    print("ШАГ 1: Создание тестовых объявлений")
    print("=" * 60)
    
    # Проверяем доступность Turso
    if not USE_TURSO_CACHE:
        print("❌ USE_TURSO_CACHE отключен в config.py")
        return False
    
    if not TURSO_DB_URL or not TURSO_AUTH_TOKEN:
        print("❌ TURSO_DB_URL или TURSO_AUTH_TOKEN не настроены")
        return False
    
    # Убеждаемся что таблицы созданы
    print("📋 Проверка и создание таблиц...")
    await ensure_tables_exist()
    
    # Тестовые данные
    test_apartments = [
        {
            "ad_id": "kufar_test_001",
            "ad_data": {
                "price_usd": 50000,
                "price_byn": 147500,
                "rooms": 1,
                "floor": "5/9",
                "total_area": 35.5,
                "url": "https://re.kufar.by/vi/test001",
                "address": "Минск, ул. Ленина, 1",
                "title": "1-комнатная квартира в Минске",
                "description": "Уютная однокомнатная квартира",
                "photos": [],
                "currency": "USD",
                "year_built": "2010",
                "is_company": False,
                "balcony": "Есть",
                "bathroom": "Раздельный",
                "total_floors": "9",
                "house_type": "Кирпичный",
                "renovation_state": "хорошее",
                "kitchen_area": 8.0,
                "living_area": 20.0,
                "list_time": datetime.now().isoformat()
            },
            "raw_json": json.dumps({
                "ad_id": 1001,
                "ad_link": "https://re.kufar.by/vi/test001",
                "price_usd": "5000000",  # в центах
                "price_byn": "14750000",  # в копейках
                "list_time": int(datetime.now().timestamp() * 1000),
                "subject": "1-комнатная квартира в Минске",
                "body": "Уютная однокомнатная квартира",
                "ad_parameters": [
                    {"p": "rooms", "v": "1"},
                    {"p": "size", "v": "35.5"},
                    {"p": "floor", "v": "5"},
                    {"p": "re_number_floors", "v": "9"}
                ]
            })
        },
        {
            "ad_id": "kufar_test_002",
            "ad_data": {
                "price_usd": 75000,
                "price_byn": 221250,
                "rooms": 2,
                "floor": "3/5",
                "total_area": 55.0,
                "url": "https://re.kufar.by/vi/test002",
                "address": "Минск, ул. Пушкина, 10",
                "title": "2-комнатная квартира в Минске",
                "description": "Просторная двухкомнатная квартира",
                "photos": [],
                "currency": "USD",
                "year_built": "2015",
                "is_company": True,
                "balcony": "Есть",
                "bathroom": "Совмещенный",
                "total_floors": "5",
                "house_type": "Панельный",
                "renovation_state": "отличное",
                "kitchen_area": 12.0,
                "living_area": 35.0,
                "list_time": datetime.now().isoformat()
            },
            "raw_json": json.dumps({
                "ad_id": 1002,
                "ad_link": "https://re.kufar.by/vi/test002",
                "price_usd": "7500000",
                "price_byn": "22125000",
                "list_time": int(datetime.now().timestamp() * 1000),
                "subject": "2-комнатная квартира в Минске",
                "body": "Просторная двухкомнатная квартира",
                "company_ad": True,
                "ad_parameters": [
                    {"p": "rooms", "v": "2"},
                    {"p": "size", "v": "55.0"},
                    {"p": "floor", "v": "3"},
                    {"p": "re_number_floors", "v": "5"}
                ]
            })
        },
        {
            "ad_id": "kufar_test_003",
            "ad_data": {
                "price_usd": 120000,
                "price_byn": 354000,
                "rooms": 3,
                "floor": "7/10",
                "total_area": 75.0,
                "url": "https://re.kufar.by/vi/test003",
                "address": "Минск, пр. Независимости, 50",
                "title": "3-комнатная квартира в Минске",
                "description": "Роскошная трехкомнатная квартира",
                "photos": [],
                "currency": "USD",
                "year_built": "2020",
                "is_company": False,
                "balcony": "Есть",
                "bathroom": "Раздельный",
                "total_floors": "10",
                "house_type": "Монолитный",
                "renovation_state": "отличное",
                "kitchen_area": 15.0,
                "living_area": 50.0,
                "list_time": datetime.now().isoformat()
            },
            "raw_json": json.dumps({
                "ad_id": 1003,
                "ad_link": "https://re.kufar.by/vi/test003",
                "price_usd": "12000000",
                "price_byn": "35400000",
                "list_time": int(datetime.now().timestamp() * 1000),
                "subject": "3-комнатная квартира в Минске",
                "body": "Роскошная трехкомнатная квартира",
                "company_ad": False,
                "ad_parameters": [
                    {"p": "rooms", "v": "3"},
                    {"p": "size", "v": "75.0"},
                    {"p": "floor", "v": "7"},
                    {"p": "re_number_floors", "v": "10"}
                ]
            })
        }
    ]
    
    # Синхронизируем тестовые объявления
    synced_count = 0
    for apt in test_apartments:
        try:
            success = await sync_apartment_from_kufar(
                ad_id=apt["ad_id"],
                ad_data=apt["ad_data"],
                raw_json=apt["raw_json"],
                source="kufar"
            )
            if success:
                synced_count += 1
                print(f"✅ Создано объявление: {apt['ad_id']} ({apt['ad_data']['rooms']}к, ${apt['ad_data']['price_usd']:,})")
            else:
                print(f"❌ Ошибка создания объявления: {apt['ad_id']}")
        except Exception as e:
            print(f"❌ Исключение при создании {apt['ad_id']}: {e}")
    
    print(f"\n📊 Создано {synced_count} из {len(test_apartments)} тестовых объявлений")
    return synced_count == len(test_apartments)


async def test_dynamic_query():
    """Тестирует build_dynamic_query с разными фильтрами"""
    print("\n" + "=" * 60)
    print("ШАГ 2: Тестирование динамических фильтров")
    print("=" * 60)
    
    test_cases = [
        {
            "name": "Только минимальная цена",
            "filters": {
                "min_price": 60000,
                "max_price": None,
                "rooms": None,
                "region": None,
                "source": None,
                "is_active": True,
                "limit": 10
            }
        },
        {
            "name": "Цена + комнаты",
            "filters": {
                "min_price": 50000,
                "max_price": 100000,
                "rooms": [1, 2],
                "region": None,
                "source": None,
                "is_active": True,
                "limit": 10
            }
        },
        {
            "name": "Только комнаты",
            "filters": {
                "min_price": None,
                "max_price": None,
                "rooms": [2, 3],
                "region": None,
                "source": None,
                "is_active": True,
                "limit": 10
            }
        },
        {
            "name": "Пустой фильтр (все объявления)",
            "filters": {
                "min_price": None,
                "max_price": None,
                "rooms": None,
                "region": None,
                "source": None,
                "is_active": True,
                "limit": 10
            }
        },
        {
            "name": "Фильтр по региону",
            "filters": {
                "min_price": None,
                "max_price": None,
                "rooms": None,
                "region": "Минск",
                "source": None,
                "is_active": True,
                "limit": 10
            }
        },
        {
            "name": "Фильтр по источнику",
            "filters": {
                "min_price": None,
                "max_price": None,
                "rooms": None,
                "region": None,
                "source": "kufar",
                "is_active": True,
                "limit": 10
            }
        }
    ]
    
    for i, test_case in enumerate(test_cases, 1):
        print(f"\n--- Тест {i}: {test_case['name']} ---")
        print(f"Фильтры: {json.dumps(test_case['filters'], indent=2, ensure_ascii=False)}")
        
        try:
            results = await build_dynamic_query(**test_case["filters"])
            
            print(f"✅ Найдено объявлений: {len(results)}")
            if results:
                print("Результаты:")
                for j, apt in enumerate(results[:5], 1):  # Показываем первые 5
                    print(f"  {j}. {apt.get('ad_id')} - {apt.get('rooms')}к, ${apt.get('price_usd', 0):,}, {apt.get('address', 'N/A')}")
            else:
                print("⚠️ Объявления не найдены")
                
        except Exception as e:
            print(f"❌ Ошибка выполнения запроса: {e}")
            import traceback
            traceback.print_exc()


async def test_sql_generation():
    """Проверяет генерацию SQL запросов (для отладки)"""
    print("\n" + "=" * 60)
    print("ШАГ 3: Проверка генерации SQL (отладка)")
    print("=" * 60)
    
    # Проверяем что SQL генерируется правильно
    conn = get_turso_connection()
    if not conn:
        print("❌ Не удалось подключиться к Turso")
        return
    
    try:
        import asyncio
        
        def _test_sql():
            # Тестируем разные комбинации условий
            test_queries = []
            
            # 1. Только is_active
            conditions = ["is_active = ?"]
            params = [1]
            where_clause = " AND ".join(conditions)
            query = f"SELECT COUNT(*) FROM apartments WHERE {where_clause}"
            cursor = conn.execute(query, params)
            count = cursor.fetchone()[0]
            test_queries.append(("Только is_active", query, params, count))
            
            # 2. is_active + min_price
            conditions = ["is_active = ?", "price_usd >= ?"]
            params = [1, 60000]
            where_clause = " AND ".join(conditions)
            query = f"SELECT COUNT(*) FROM apartments WHERE {where_clause}"
            cursor = conn.execute(query, params)
            count = cursor.fetchone()[0]
            test_queries.append(("is_active + min_price", query, params, count))
            
            # 3. is_active + rooms IN
            conditions = ["is_active = ?", "rooms IN (?, ?)"]
            params = [1, 1, 2]
            where_clause = " AND ".join(conditions)
            query = f"SELECT COUNT(*) FROM apartments WHERE {where_clause}"
            cursor = conn.execute(query, params)
            count = cursor.fetchone()[0]
            test_queries.append(("is_active + rooms IN", query, params, count))
            
            return test_queries
        
        queries = await asyncio.to_thread(_test_sql)
        
        for name, query, params, count in queries:
            print(f"\n{name}:")
            print(f"  SQL: {query}")
            print(f"  Параметры: {params}")
            print(f"  Результат: {count} объявлений")
            
    except Exception as e:
        print(f"❌ Ошибка проверки SQL: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if conn:
            conn.close()


async def main():
    """Главная функция тестирования"""
    print("🧪 ТЕСТИРОВАНИЕ ДИНАМИЧЕСКИХ ФИЛЬТРОВ TURSO")
    print("=" * 60)
    
    # Шаг 1: Создание тестовых данных
    success = await create_test_apartments()
    if not success:
        print("\n❌ Не удалось создать тестовые данные. Проверьте настройки Turso.")
        return
    
    # Шаг 2: Тестирование динамических запросов
    await test_dynamic_query()
    
    # Шаг 3: Проверка генерации SQL
    await test_sql_generation()
    
    print("\n" + "=" * 60)
    print("✅ ТЕСТИРОВАНИЕ ЗАВЕРШЕНО")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
