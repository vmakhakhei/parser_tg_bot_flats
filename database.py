"""
Модуль для работы с базой данных SQLite
Хранит информацию об уже отправленных объявлениях и настройках фильтров
"""
import aiosqlite
import hashlib
from config import DATABASE_PATH
from typing import Optional, List, Dict, Any
from datetime import datetime


def generate_content_hash(rooms: int, area: float, address: str, price: int) -> str:
    """
    Генерирует хеш контента объявления для обнаружения дубликатов.
    Одно и то же объявление с разных сайтов будет иметь одинаковый хеш.
    """
    # Нормализуем данные
    norm_address = address.lower().strip()
    # Убираем "барановичи" из адреса для сравнения
    norm_address = norm_address.replace("барановичи", "").replace(",", "").strip()
    # Округляем площадь до целого
    norm_area = int(area)
    # Цена с погрешностью ±5%
    price_bucket = int(price / 1000) * 1000  # Округляем до тысяч
    
    data = f"{rooms}:{norm_area}:{norm_address}:{price_bucket}"
    return hashlib.md5(data.encode()).hexdigest()[:16]


async def init_database():
    """Инициализация базы данных и создание таблиц"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        # Таблица отправленных объявлений
        await db.execute("""
            CREATE TABLE IF NOT EXISTS sent_listings (
                id TEXT PRIMARY KEY,
                title TEXT,
                price INTEGER,
                rooms INTEGER,
                area REAL,
                address TEXT,
                url TEXT,
                content_hash TEXT,
                source TEXT,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Создаем индекс для быстрого поиска по хешу
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_content_hash ON sent_listings(content_hash)
        """)
        
        # Таблица настроек фильтров
        await db.execute("""
            CREATE TABLE IF NOT EXISTS filters (
                id INTEGER PRIMARY KEY DEFAULT 1,
                city TEXT DEFAULT 'барановичи',
                min_rooms INTEGER DEFAULT 1,
                max_rooms INTEGER DEFAULT 4,
                min_price INTEGER DEFAULT 0,
                max_price INTEGER DEFAULT 100000,
                is_active BOOLEAN DEFAULT 1,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Вставляем дефолтные настройки если их нет
        await db.execute("""
            INSERT OR IGNORE INTO filters (id) VALUES (1)
        """)
        
        await db.commit()


async def is_listing_sent(listing_id: str) -> bool:
    """Проверяет, было ли объявление уже отправлено (по ID)"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute(
            "SELECT id FROM sent_listings WHERE id = ?",
            (listing_id,)
        )
        result = await cursor.fetchone()
        return result is not None


async def is_duplicate_content(rooms: int, area: float, address: str, price: int) -> Dict[str, Any]:
    """
    Проверяет, есть ли уже такое объявление по контенту.
    Возвращает информацию о дубликате если найден, иначе None.
    """
    content_hash = generate_content_hash(rooms, area, address, price)
    
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, source, url, sent_at FROM sent_listings WHERE content_hash = ?",
            (content_hash,)
        )
        result = await cursor.fetchone()
        if result:
            return {
                "is_duplicate": True,
                "original_id": result["id"],
                "original_source": result["source"],
                "original_url": result["url"],
                "sent_at": result["sent_at"],
                "content_hash": content_hash
            }
        return {"is_duplicate": False, "content_hash": content_hash}


async def mark_listing_sent(listing: Dict[str, Any]):
    """Отмечает объявление как отправленное"""
    # Генерируем хеш контента
    content_hash = generate_content_hash(
        listing.get("rooms", 0),
        listing.get("area", 0.0),
        listing.get("address", ""),
        listing.get("price", 0)
    )
    
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
            INSERT OR REPLACE INTO sent_listings 
            (id, title, price, rooms, area, address, url, content_hash, source, sent_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            listing.get("id"),
            listing.get("title"),
            listing.get("price"),
            listing.get("rooms"),
            listing.get("area"),
            listing.get("address"),
            listing.get("url"),
            content_hash,
            listing.get("source", "unknown"),
            datetime.now().isoformat()
        ))
        await db.commit()


async def get_filters() -> Dict[str, Any]:
    """Получает текущие настройки фильтров"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM filters WHERE id = 1")
        row = await cursor.fetchone()
        if row:
            return dict(row)
        return {
            "city": "барановичи",
            "min_rooms": 1,
            "max_rooms": 4,
            "min_price": 0,
            "max_price": 100000,
            "is_active": True
        }


async def update_filters(
    city: Optional[str] = None,
    min_rooms: Optional[int] = None,
    max_rooms: Optional[int] = None,
    min_price: Optional[int] = None,
    max_price: Optional[int] = None,
    is_active: Optional[bool] = None
):
    """Обновляет настройки фильтров"""
    current = await get_filters()
    
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
            UPDATE filters SET
                city = ?,
                min_rooms = ?,
                max_rooms = ?,
                min_price = ?,
                max_price = ?,
                is_active = ?,
                updated_at = ?
            WHERE id = 1
        """, (
            city if city is not None else current["city"],
            min_rooms if min_rooms is not None else current["min_rooms"],
            max_rooms if max_rooms is not None else current["max_rooms"],
            min_price if min_price is not None else current["min_price"],
            max_price if max_price is not None else current["max_price"],
            is_active if is_active is not None else current["is_active"],
            datetime.now().isoformat()
        ))
        await db.commit()


async def get_sent_listings_count() -> int:
    """Возвращает количество отправленных объявлений"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM sent_listings")
        result = await cursor.fetchone()
        return result[0] if result else 0


async def clear_old_listings(days: int = 30):
    """Удаляет старые записи об отправленных объявлениях"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
            DELETE FROM sent_listings 
            WHERE sent_at < datetime('now', ? || ' days')
        """, (f"-{days}",))
        await db.commit()


async def get_duplicates_stats() -> Dict[str, Any]:
    """Возвращает статистику по дубликатам"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        # Общее количество записей
        cursor = await db.execute("SELECT COUNT(*) FROM sent_listings")
        total = (await cursor.fetchone())[0]
        
        # Количество уникальных хешей
        cursor = await db.execute("SELECT COUNT(DISTINCT content_hash) FROM sent_listings WHERE content_hash IS NOT NULL")
        unique_hashes = (await cursor.fetchone())[0]
        
        # Количество дубликатов (записей с одинаковым хешем)
        cursor = await db.execute("""
            SELECT content_hash, COUNT(*) as cnt, GROUP_CONCAT(source) as sources
            FROM sent_listings 
            WHERE content_hash IS NOT NULL
            GROUP BY content_hash 
            HAVING cnt > 1
        """)
        duplicates = await cursor.fetchall()
        
        # Статистика по источникам
        cursor = await db.execute("""
            SELECT source, COUNT(*) as cnt 
            FROM sent_listings 
            GROUP BY source
        """)
        by_source = {row[0]: row[1] for row in await cursor.fetchall()}
        
        return {
            "total_sent": total,
            "unique_content": unique_hashes,
            "duplicate_groups": len(duplicates),
            "by_source": by_source,
            "duplicate_details": [
                {"hash": d[0], "count": d[1], "sources": d[2]} 
                for d in duplicates[:10]  # Первые 10 групп
            ]
        }


async def get_recent_listings(limit: int = 10) -> List[Dict[str, Any]]:
    """Возвращает последние отправленные объявления"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT id, title, price, rooms, area, address, source, sent_at, content_hash
            FROM sent_listings 
            ORDER BY sent_at DESC 
            LIMIT ?
        """, (limit,))
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

