"""
from config import USE_TURSO_CACHE, TURSO_DB_URL, TURSO_AUTH_TOKEN

Тестовый скрипт для проверки динамических фильтров и работы с Turso
"""
import asyncio
import json
import sys
import os
from datetime import datetime

# Добавляем путь к проекту
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Импортируем напрямую функции из database_turso, избегая циклических импортов
import sys
import os

# Добавляем путь к проекту
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Импортируем config напрямую
try:
except ImportError:
    print("⚠️ Не удалось импортировать config, используем значения по умолчанию")
    USE_TURSO_CACHE = True
    TURSO_DB_URL = os.getenv("TURSO_DB_URL")
    TURSO_AUTH_TOKEN = os.getenv("TURSO_AUTH_TOKEN")

# Импортируем libsql напрямую
try:
    import libsql
except ImportError:
    print("❌ libsql не установлен. Установите: pip install libsql")
    sys.exit(1)

# Импортируем функции из database_turso, обходя импорт database
import asyncio
import json
from datetime import datetime
from typing import Optional, List, Dict, Any
import logging

logger = logging.getLogger(__name__)

# Копируем необходимые функции из database_turso
def get_turso_connection():
    """Создает соединение с Turso (синхронное)"""
    if not USE_TURSO_CACHE:
        return None
    
    if not TURSO_DB_URL or not TURSO_AUTH_TOKEN:
        logger.warning("Turso не настроен: отсутствуют TURSO_DB_URL или TURSO_AUTH_TOKEN")
        return None
    
    try:
        return libsql.connect(
            TURSO_DB_URL,
            auth_token=TURSO_AUTH_TOKEN
        )
    except Exception as e:
        logger.error(f"Ошибка создания соединения с Turso: {e}")
        return None


async def ensure_tables_exist():
    """Проверяет и создает все необходимые таблицы если их нет"""
    conn = get_turso_connection()
    if not conn:
        logger.warning("Turso недоступен, пропускаем создание таблиц")
        return False
    
    try:
        def _check_and_create():
            # Проверяем наличие таблицы apartments
            cursor = conn.execute("""
                SELECT name FROM sqlite_master 
                WHERE type='table' AND name='apartments'
            """)
            if not cursor.fetchone():
                logger.info("📋 Создание таблицы apartments...")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS apartments (
                        ad_id TEXT PRIMARY KEY,
                        source TEXT NOT NULL,
                        price_usd INTEGER,
                        price_byn INTEGER,
                        rooms INTEGER,
                        floor TEXT,
                        total_area REAL,
                        list_time TIMESTAMP,
                        last_checked TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        is_active INTEGER DEFAULT 1,
                        url TEXT NOT NULL,
                        address TEXT,
                        raw_json TEXT,
                        title TEXT,
                        description TEXT,
                        photos TEXT,
                        currency TEXT,
                        year_built TEXT,
                        is_company INTEGER,
                        balcony TEXT,
                        bathroom TEXT,
                        total_floors TEXT,
                        house_type TEXT,
                        renovation_state TEXT,
                        kitchen_area REAL,
                        living_area REAL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                # Создаем индексы
                conn.execute("CREATE INDEX IF NOT EXISTS idx_apartments_price_usd ON apartments(price_usd)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_apartments_rooms ON apartments(rooms)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_apartments_source ON apartments(source)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_apartments_is_active ON apartments(is_active)")
                conn.commit()
                logger.info("✅ Таблица apartments создана")
        
        await asyncio.to_thread(_check_and_create)
        return True
    except Exception as e:
        logger.error(f"Ошибка создания таблиц Turso: {e}")
        return False
    finally:
        if conn:
            conn.close()


async def build_dynamic_query(
    min_price: Optional[int] = None,
    max_price: Optional[int] = None,
    rooms: Optional[List[int]] = None,
    region: Optional[str] = None,
    source: Optional[str] = None,
    is_active: bool = True,
    limit: int = 100
) -> List[Dict[str, Any]]:
    """Строит динамический SQL запрос для поиска квартир"""
    conn = get_turso_connection()
    if not conn:
        return []
    
    try:
        def _execute():
            conditions = []
            params = []
            
            if is_active:
                conditions.append("is_active = ?")
                params.append(1)
            
            if min_price is not None and min_price > 0:
                conditions.append("price_usd >= ?")
                params.append(min_price)
            
            if max_price is not None and max_price > 0:
                conditions.append("price_usd <= ?")
                params.append(max_price)
            
            if rooms and len(rooms) > 0:
                placeholders = ",".join(["?"] * len(rooms))
                conditions.append(f"rooms IN ({placeholders})")
                params.extend(rooms)
            
            if region:
                conditions.append("address LIKE ?")
                params.append(f"%{region}%")
            
            if source:
                conditions.append("source = ?")
                params.append(source)
            
            where_clause = " AND ".join(conditions) if conditions else "1=1"
            
            query = f"""
                SELECT * FROM apartments 
                WHERE {where_clause}
                ORDER BY list_time DESC, updated_at DESC
                LIMIT ?
            """
            params.append(limit)
            
            cursor = conn.execute(query, params)
            rows = cursor.fetchall()
            
            columns = [desc[0] for desc in cursor.description]
            results = []
            for row in rows:
                result = dict(zip(columns, row))
                if result.get("photos"):
                    try:
                        result["photos"] = json.loads(result["photos"]) if isinstance(result["photos"], str) else result["photos"]
                    except:
                        result["photos"] = []
                else:
                    result["photos"] = []
                result["is_active"] = bool(result.get("is_active", 1))
                result["is_company"] = bool(result.get("is_company", 0)) if result.get("is_company") is not None else None
                results.append(result)
            
            return results
        
        return await asyncio.to_thread(_execute)
    except Exception as e:
        logger.error(f"Ошибка динамического запроса: {e}")
        import traceback
        traceback.print_exc()
        return []
    finally:
        if conn:
            conn.close()


async def sync_apartment_from_kufar(
    ad_id: str,
    ad_data: dict,
    raw_json: str,
    source: str = "kufar"
) -> bool:
    """Умная синхронизация объявления из Kufar API"""
    conn = get_turso_connection()
    if not conn:
        return False
    
    try:
        def _execute():
            list_time = ad_data.get("list_time")
            if isinstance(list_time, str):
                try:
                    dt = datetime.fromisoformat(list_time.replace("Z", "+00:00"))
                    list_time = dt.isoformat()
                except:
                    list_time = datetime.now().isoformat()
            elif not list_time:
                list_time = datetime.now().isoformat()
            
            current_time = datetime.now().isoformat()
            
            # Проверяем существующее объявление
            cursor = conn.execute("SELECT list_time FROM apartments WHERE ad_id = ?", (ad_id,))
            existing = cursor.fetchone()
            
            if existing:
                conn.execute("""
                    UPDATE apartments SET
                        price_usd = ?, price_byn = ?, rooms = ?, floor = ?,
                        total_area = ?, list_time = ?, last_checked = ?,
                        is_active = 1, url = ?, address = ?, raw_json = ?,
                        title = ?, description = ?, photos = ?, currency = ?,
                        year_built = ?, is_company = ?, balcony = ?, bathroom = ?,
                        total_floors = ?, house_type = ?, renovation_state = ?,
                        kitchen_area = ?, living_area = ?, updated_at = ?
                    WHERE ad_id = ?
                """, (
                    ad_data.get("price_usd", 0),
                    ad_data.get("price_byn", 0),
                    ad_data.get("rooms", 0),
                    ad_data.get("floor", ""),
                    ad_data.get("total_area", 0.0),
                    list_time,
                    current_time,
                    ad_data.get("url", ""),
                    ad_data.get("address", ""),
                    raw_json,
                    ad_data.get("title", ""),
                    ad_data.get("description", ""),
                    json.dumps(ad_data.get("photos", [])),
                    ad_data.get("currency", "USD"),
                    ad_data.get("year_built", ""),
                    1 if ad_data.get("is_company") else 0,
                    ad_data.get("balcony", ""),
                    ad_data.get("bathroom", ""),
                    ad_data.get("total_floors", ""),
                    ad_data.get("house_type", ""),
                    ad_data.get("renovation_state", ""),
                    ad_data.get("kitchen_area", 0.0),
                    ad_data.get("living_area", 0.0),
                    current_time,
                    ad_id
                ))
            else:
                conn.execute("""
                    INSERT INTO apartments (
                        ad_id, source, price_usd, price_byn, rooms, floor, total_area,
                        list_time, last_checked, is_active, url, address, raw_json,
                        title, description, photos, currency, year_built, is_company,
                        balcony, bathroom, total_floors, house_type, renovation_state,
                        kitchen_area, living_area, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    ad_id, source,
                    ad_data.get("price_usd", 0), ad_data.get("price_byn", 0),
                    ad_data.get("rooms", 0), ad_data.get("floor", ""), ad_data.get("total_area", 0.0),
                    list_time, current_time, 1,
                    ad_data.get("url", ""), ad_data.get("address", ""), raw_json,
                    ad_data.get("title", ""), ad_data.get("description", ""),
                    json.dumps(ad_data.get("photos", [])), ad_data.get("currency", "USD"),
                    ad_data.get("year_built", ""), 1 if ad_data.get("is_company") else 0,
                    ad_data.get("balcony", ""), ad_data.get("bathroom", ""),
                    ad_data.get("total_floors", ""), ad_data.get("house_type", ""),
                    ad_data.get("renovation_state", ""), ad_data.get("kitchen_area", 0.0),
                    ad_data.get("living_area", 0.0), current_time, current_time
                ))
            
            conn.commit()
        
        await asyncio.to_thread(_execute)
        return True
    except Exception as e:
        logger.error(f"Ошибка синхронизации объявления {ad_id}: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        if conn:
            conn.close()


async def create_test_apartments():
    """Создает тестовые объявления в таблице apartments"""
    print("=" * 60)
    print("ШАГ 1: Создание тестовых объявлений")
    print("=" * 60)
    
    if not USE_TURSO_CACHE:
        print("❌ USE_TURSO_CACHE отключен, пропускаем тест")
        return
    
    if not TURSO_DB_URL or not TURSO_AUTH_TOKEN:
        print("❌ Turso не настроен (отсутствуют TURSO_DB_URL или TURSO_AUTH_TOKEN)")
        return
    
    # Убеждаемся что таблицы созданы
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
                "description": "Отличная квартира в центре",
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
                "price_usd": "5000000",  # в центах
                "rooms": 1,
                "list_time": int(datetime.now().timestamp() * 1000),
                "ad_link": "https://re.kufar.by/vi/test001"
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
                "description": "Современная квартира с ремонтом",
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
                "price_usd": "7500000",  # в центах
                "rooms": 2,
                "list_time": int(datetime.now().timestamp() * 1000),
                "ad_link": "https://re.kufar.by/vi/test002"
            })
        },
        {
            "ad_id": "kufar_test_003",
            "ad_data": {
                "price_usd": 100000,
                "price_byn": 295000,
                "rooms": 3,
                "floor": "7/10",
                "total_area": 75.0,
                "url": "https://re.kufar.by/vi/test003",
                "address": "Минск, пр. Победителей, 20",
                "title": "3-комнатная квартира в Минске",
                "description": "Просторная квартира с видом",
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
                "price_usd": "10000000",  # в центах
                "rooms": 3,
                "list_time": int(datetime.now().timestamp() * 1000),
                "ad_link": "https://re.kufar.by/vi/test003"
            })
        }
    ]
    
    created_count = 0
    for apt in test_apartments:
        try:
            success = await sync_apartment_from_kufar(
                ad_id=apt["ad_id"],
                ad_data=apt["ad_data"],
                raw_json=apt["raw_json"],
                source="kufar"
            )
            if success:
                created_count += 1
                print(f"✅ Создано объявление: {apt['ad_id']} ({apt['ad_data']['rooms']}к, ${apt['ad_data']['price_usd']:,})")
            else:
                print(f"❌ Ошибка создания объявления: {apt['ad_id']}")
        except Exception as e:
            print(f"❌ Исключение при создании {apt['ad_id']}: {e}")
    
    print(f"\n📊 Создано {created_count} из {len(test_apartments)} тестовых объявлений\n")
    return created_count > 0


async def test_dynamic_query():
    """Тестирует build_dynamic_query с разными фильтрами"""
    print("=" * 60)
    print("ШАГ 2: Тестирование динамических фильтров")
    print("=" * 60)
    
    test_cases = [
        {
            "name": "Тест 1: Только минимальная цена",
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
            "name": "Тест 2: Цена + комнаты",
            "filters": {
                "min_price": 50000,
                "max_price": 80000,
                "rooms": [1, 2],
                "region": None,
                "source": None,
                "is_active": True,
                "limit": 10
            }
        },
        {
            "name": "Тест 3: Только комнаты",
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
            "name": "Тест 4: Регион (Минск)",
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
            "name": "Тест 5: Пустой фильтр (все объявления)",
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
            "name": "Тест 6: Только источник (kufar)",
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
    
    for test_case in test_cases:
        print(f"\n{test_case['name']}")
        print("-" * 60)
        
        try:
            results = await build_dynamic_query(**test_case["filters"])
            
            print(f"📊 Найдено объявлений: {len(results)}")
            
            if results:
                print("\nРезультаты:")
                for i, apt in enumerate(results[:5], 1):  # Показываем первые 5
                    print(f"  {i}. {apt.get('ad_id', 'N/A')} - {apt.get('rooms', 0)}к, "
                          f"${apt.get('price_usd', 0):,}, {apt.get('address', 'N/A')}")
            else:
                print("  (нет результатов)")
                
        except Exception as e:
            print(f"❌ Ошибка: {e}")
            import traceback
            traceback.print_exc()
    
    print("\n" + "=" * 60)


async def inspect_sql_query():
    """Проверяет сгенерированный SQL запрос"""
    print("=" * 60)
    print("ШАГ 3: Инспекция SQL запросов")
    print("=" * 60)
    
    # Для инспекции нужно модифицировать build_dynamic_query чтобы возвращать SQL
    # Пока просто проверим что запросы работают корректно
    
    conn = get_turso_connection()
    if not conn:
        print("❌ Не удалось подключиться к Turso")
        return
    
    try:
        def _inspect():
            # Проверяем структуру таблицы
            cursor = conn.execute("PRAGMA table_info(apartments)")
            columns = cursor.fetchall()
            print("\nСтруктура таблицы apartments:")
            for col in columns:
                print(f"  - {col[1]} ({col[2]})")
            
            # Проверяем количество записей
            cursor = conn.execute("SELECT COUNT(*) FROM apartments")
            count = cursor.fetchone()[0]
            print(f"\nВсего записей в таблице: {count}")
            
            # Проверяем индексы
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='apartments'")
            indexes = cursor.fetchall()
            print(f"\nИндексы на таблице apartments:")
            for idx in indexes:
                print(f"  - {idx[0]}")
        
        await asyncio.to_thread(_inspect)
    except Exception as e:
        print(f"❌ Ошибка инспекции: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if conn:
            conn.close()


async def main():
    """Основная функция тестирования"""
    print("\n" + "=" * 60)
    print("ТЕСТИРОВАНИЕ ДИНАМИЧЕСКИХ ФИЛЬТРОВ TURSO")
    print("=" * 60 + "\n")
    
    # Шаг 1: Создание тестовых данных
    success = await create_test_apartments()
    
    if not success:
        print("❌ Не удалось создать тестовые данные. Проверьте настройки Turso.")
        return
    
    # Шаг 2: Инспекция структуры БД
    await inspect_sql_query()
    
    # Шаг 3: Тестирование динамических запросов
    await test_dynamic_query()
    
    print("\n" + "=" * 60)
    print("ТЕСТИРОВАНИЕ ЗАВЕРШЕНО")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
