"""
Модуль для работы с Turso Database (кэширование объявлений)
Используется для экономии трафика и API вызовов
"""
import json
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime
try:
    from libsql_client import create_client, Client
except ImportError:
    try:
        from libsql import create_client, Client
    except ImportError:
        # Fallback для разных версий пакета
        create_client = None
        Client = None
from config import TURSO_DB_URL, TURSO_AUTH_TOKEN, USE_TURSO_CACHE
from database import generate_content_hash
from scrapers.base import Listing

logger = logging.getLogger(__name__)


def get_turso_client() -> Optional[Client]:
    """Создает клиент Turso"""
    if not USE_TURSO_CACHE:
        return None
    
    if not TURSO_DB_URL or not TURSO_AUTH_TOKEN:
        logger.warning("Turso не настроен: отсутствуют TURSO_DB_URL или TURSO_AUTH_TOKEN")
        return None
    
    try:
        return create_client(
            url=TURSO_DB_URL,
            auth_token=TURSO_AUTH_TOKEN
        )
    except Exception as e:
        logger.error(f"Ошибка создания клиента Turso: {e}")
        return None


async def get_cached_listings_by_filters(
    city: str,
    min_rooms: int,
    max_rooms: int,
    min_price: int,
    max_price: int,
    limit: int = 100,
    status: str = "active"
) -> List[Dict[str, Any]]:
    """
    Получает объявления из кэша по фильтрам
    Это основная функция для экономии трафика!
    
    Returns:
        Список объявлений из кэша или пустой список при ошибке
    """
    client = get_turso_client()
    if not client:
        return []
    
    try:
        result = await client.execute("""
            SELECT * FROM cached_listings
            WHERE city = ? 
            AND rooms >= ? AND rooms <= ?
            AND price >= ? AND price <= ?
            AND status = ?
            ORDER BY updated_at DESC
            LIMIT ?
        """, (city, min_rooms, max_rooms, min_price, max_price, status, limit))
        
        listings = []
        for row in result.rows:
            listing_dict = dict(row)
            # Конвертируем photos из JSON строки в список
            if listing_dict.get("photos"):
                try:
                    listing_dict["photos"] = json.loads(listing_dict["photos"]) if isinstance(listing_dict["photos"], str) else listing_dict["photos"]
                except:
                    listing_dict["photos"] = []
            else:
                listing_dict["photos"] = []
            
            # Конвертируем is_company из INTEGER в bool
            if "is_company" in listing_dict:
                listing_dict["is_company"] = bool(listing_dict["is_company"]) if listing_dict["is_company"] is not None else None
            
            listings.append(listing_dict)
        
        logger.info(f"Найдено {len(listings)} объявлений в кэше для города {city}")
        return listings
        
    except Exception as e:
        logger.error(f"Ошибка получения объявлений из кэша: {e}")
        return []
    finally:
        if client:
            await client.close()


async def cache_listing(listing: Listing) -> bool:
    """
    Сохраняет объявление в кэш
    
    Returns:
        True если успешно, False при ошибке
    """
    client = get_turso_client()
    if not client:
        return False
    
    try:
        content_hash = generate_content_hash(
            listing.rooms,
            listing.area,
            listing.address,
            listing.price
        )
        
        # Конвертируем photos в JSON строку
        photos_json = json.dumps(listing.photos) if listing.photos else "[]"
        
        # Конвертируем is_company в INTEGER (0/1)
        is_company_int = 1 if listing.is_company is True else (0 if listing.is_company is False else None)
        
        await client.execute("""
            INSERT OR REPLACE INTO cached_listings 
            (id, source, title, price, rooms, area, address, url, city, 
             price_usd, currency, floor, year_built, description, photos, 
             is_company, content_hash, status, updated_at, first_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 
                    COALESCE((SELECT first_seen_at FROM cached_listings WHERE id = ?), ?))
        """, (
            listing.id,
            listing.source,
            listing.title,
            listing.price,
            listing.rooms,
            listing.area,
            listing.address,
            listing.url,
            # Определяем город из адреса (упрощенно)
            _extract_city_from_address(listing.address),
            listing.price_usd,
            listing.currency,
            listing.floor,
            listing.year_built,
            listing.description,
            photos_json,
            is_company_int,
            content_hash,
            "active",
            datetime.now().isoformat(),
            listing.id,  # для COALESCE
            datetime.now().isoformat()  # для COALESCE
        ))
        
        return True
        
    except Exception as e:
        logger.error(f"Ошибка сохранения объявления в кэш: {e}")
        return False
    finally:
        if client:
            await client.close()


async def cache_listings_batch(listings: List[Listing]) -> int:
    """
    Сохраняет несколько объявлений в кэш батчем
    
    Returns:
        Количество успешно сохраненных объявлений
    """
    if not listings:
        return 0
    
    client = get_turso_client()
    if not client:
        return 0
    
    saved_count = 0
    
    try:
        for listing in listings:
            if await cache_listing(listing):
                saved_count += 1
        
        logger.info(f"Сохранено {saved_count} из {len(listings)} объявлений в кэш")
        return saved_count
        
    except Exception as e:
        logger.error(f"Ошибка батч-сохранения в кэш: {e}")
        return saved_count
    finally:
        if client:
            await client.close()


async def mark_listing_deleted(listing_id: str) -> bool:
    """Отмечает объявление как удаленное"""
    client = get_turso_client()
    if not client:
        return False
    
    try:
        await client.execute("""
            UPDATE cached_listings 
            SET status = 'deleted', updated_at = ?
            WHERE id = ?
        """, (datetime.now().isoformat(), listing_id))
        
        return True
        
    except Exception as e:
        logger.error(f"Ошибка отметки объявления как удаленного: {e}")
        return False
    finally:
        if client:
            await client.close()


async def update_cached_listing(listing: Listing) -> bool:
    """Обновляет объявление в кэше"""
    client = get_turso_client()
    if not client:
        return False
    
    try:
        content_hash = generate_content_hash(
            listing.rooms,
            listing.area,
            listing.address,
            listing.price
        )
        
        photos_json = json.dumps(listing.photos) if listing.photos else "[]"
        is_company_int = 1 if listing.is_company is True else (0 if listing.is_company is False else None)
        
        await client.execute("""
            UPDATE cached_listings 
            SET title = ?, price = ?, rooms = ?, area = ?, address = ?,
                price_usd = ?, currency = ?, floor = ?, year_built = ?,
                description = ?, photos = ?, is_company = ?, content_hash = ?,
                status = 'active', updated_at = ?
            WHERE id = ?
        """, (
            listing.title,
            listing.price,
            listing.rooms,
            listing.area,
            listing.address,
            listing.price_usd,
            listing.currency,
            listing.floor,
            listing.year_built,
            listing.description,
            photos_json,
            is_company_int,
            content_hash,
            datetime.now().isoformat(),
            listing.id
        ))
        
        return True
        
    except Exception as e:
        logger.error(f"Ошибка обновления объявления в кэше: {e}")
        return False
    finally:
        if client:
            await client.close()


async def is_listing_cached(listing_id: str) -> bool:
    """Проверяет, есть ли объявление в кэше"""
    client = get_turso_client()
    if not client:
        return False
    
    try:
        result = await client.execute(
            "SELECT id FROM cached_listings WHERE id = ? AND status = 'active'",
            (listing_id,)
        )
        return len(result.rows) > 0
        
    except Exception as e:
        logger.error(f"Ошибка проверки наличия объявления в кэше: {e}")
        return False
    finally:
        if client:
            await client.close()


async def update_cached_listings_daily():
    """
    Ежедневное обновление кэша: проверка статуса объявлений
    Отмечает удаленные объявления и обновляет измененные
    """
    client = get_turso_client()
    if not client:
        logger.warning("Turso недоступен, пропускаем ежедневное обновление кэша")
        return
    
    try:
        logger.info("🔄 Начало ежедневного обновления кэша...")
        
        # Получаем все активные объявления старше 1 дня
        result = await client.execute("""
            SELECT id, url, source FROM cached_listings
            WHERE status = 'active'
            AND last_seen_at < datetime('now', '-1 day')
            LIMIT 100
        """)
        
        updated_count = 0
        deleted_count = 0
        
        # Проверяем каждое объявление (упрощенная версия - просто отмечаем как обновленное)
        # В будущем можно добавить реальную проверку через парсеры
        for row in result.rows:
            listing_id = row[0]
            # Просто обновляем last_seen_at для активных объявлений
            # Реальная проверка удаления требует парсинга, что дорого
            await client.execute("""
                UPDATE cached_listings
                SET last_seen_at = ?
                WHERE id = ?
            """, (datetime.now().isoformat(), listing_id))
            updated_count += 1
        
        # Удаляем очень старые удаленные объявления (старше 7 дней)
        delete_result = await client.execute("""
            DELETE FROM cached_listings
            WHERE status = 'deleted'
            AND updated_at < datetime('now', '-7 days')
        """)
        
        logger.info(f"✅ Ежедневное обновление кэша завершено:")
        logger.info(f"   Обновлено: {updated_count} объявлений")
        logger.info(f"   Удалено старых: {delete_result.rows_affected if hasattr(delete_result, 'rows_affected') else 0}")
        
    except Exception as e:
        logger.error(f"Ошибка ежедневного обновления кэша: {e}")
    finally:
        if client:
            await client.close()


async def get_listing_by_url(url: str) -> Optional[Dict[str, Any]]:
    """Получает объявление из кэша по URL"""
    client = get_turso_client()
    if not client:
        return None
    
    try:
        result = await client.execute(
            "SELECT * FROM cached_listings WHERE url = ? AND status = 'active'",
            (url,)
        )
        
        if result.rows:
            listing_dict = dict(result.rows[0])
            # Конвертируем photos из JSON
            if listing_dict.get("photos"):
                try:
                    listing_dict["photos"] = json.loads(listing_dict["photos"]) if isinstance(listing_dict["photos"], str) else listing_dict["photos"]
                except:
                    listing_dict["photos"] = []
            else:
                listing_dict["photos"] = []
            
            if "is_company" in listing_dict:
                listing_dict["is_company"] = bool(listing_dict["is_company"]) if listing_dict["is_company"] is not None else None
            
            return listing_dict
        
        return None
        
    except Exception as e:
        logger.error(f"Ошибка получения объявления по URL из кэша: {e}")
        return None
    finally:
        if client:
            await client.close()


async def ensure_tables_exist():
    """
    Проверяет и создает таблицы если их нет
    Вызывается автоматически при запуске бота
    """
    client = get_turso_client()
    if not client:
        logger.warning("Turso недоступен, пропускаем создание таблиц")
        return False
    
    try:
        # Проверяем наличие таблицы
        result = await client.execute("""
            SELECT name FROM sqlite_master 
            WHERE type='table' AND name='cached_listings'
        """)
        
        if not result.rows:
            logger.info("📋 Создание таблицы cached_listings...")
            
            # Создаем таблицу
            await client.execute("""
                CREATE TABLE IF NOT EXISTS cached_listings (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    title TEXT,
                    price INTEGER,
                    rooms INTEGER,
                    area REAL,
                    address TEXT,
                    url TEXT NOT NULL UNIQUE,
                    city TEXT,
                    price_usd INTEGER,
                    currency TEXT,
                    floor TEXT,
                    year_built TEXT,
                    description TEXT,
                    photos TEXT,
                    is_company INTEGER DEFAULT 0,
                    content_hash TEXT,
                    status TEXT DEFAULT 'active',
                    last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Создаем индексы
            await client.execute("""
                CREATE INDEX IF NOT EXISTS idx_city_rooms_price 
                ON cached_listings(city, rooms, price)
            """)
            
            await client.execute("""
                CREATE INDEX IF NOT EXISTS idx_content_hash 
                ON cached_listings(content_hash)
            """)
            
            await client.execute("""
                CREATE INDEX IF NOT EXISTS idx_status_last_seen 
                ON cached_listings(status, last_seen_at)
            """)
            
            await client.execute("""
                CREATE INDEX IF NOT EXISTS idx_source_url 
                ON cached_listings(source, url)
            """)
            
            logger.info("✅ Таблица cached_listings и индексы созданы")
        else:
            logger.info("✅ Таблица cached_listings уже существует")
        
        await client.close()
        return True
        
    except Exception as e:
        logger.error(f"Ошибка создания таблиц Turso: {e}")
        if client:
            await client.close()
        return False


def _extract_city_from_address(address: str) -> str:
    """
    Извлекает город из адреса (упрощенная версия)
    Используется для индексации в кэше
    """
    address_lower = address.lower()
    
    cities = [
        "барановичи", "минск", "брест", "витебск", "гомель", "гродно", 
        "могилев", "могилёв", "бобруйск", "пинск", "орша", "мозырь",
        "лида", "борисов", "солигорск", "молодечно", "полоцк", "новополоцк"
    ]
    
    for city in cities:
        if city in address_lower:
            return city
    
    # Если город не найден, возвращаем первый город по умолчанию
    return "барановичи"


def cached_listing_to_listing(cached_dict: Dict[str, Any]) -> Listing:
    """
    Конвертирует объявление из кэша (словарь) в объект Listing
    """
    # Форматируем цену
    price = cached_dict.get("price", 0)
    currency = cached_dict.get("currency", "USD")
    if currency == "USD":
        price_formatted = f"${price:,}".replace(",", " ")
    else:
        price_formatted = f"{price:,} BYN".replace(",", " ")
    
    return Listing(
        id=cached_dict.get("id", ""),
        source=cached_dict.get("source", "unknown"),
        title=cached_dict.get("title", ""),
        price=price,
        price_formatted=price_formatted,
        rooms=cached_dict.get("rooms", 0),
        area=cached_dict.get("area", 0.0),
        address=cached_dict.get("address", ""),
        url=cached_dict.get("url", ""),
        photos=cached_dict.get("photos", []),
        floor=cached_dict.get("floor", ""),
        description=cached_dict.get("description", ""),
        currency=currency,
        price_usd=cached_dict.get("price_usd", 0),
        year_built=cached_dict.get("year_built", ""),
        is_company=cached_dict.get("is_company"),
    )
