"""
Модуль для работы с базой данных SQLite
Хранит информацию об уже отправленных объявлениях и настройках фильтров
"""
import aiosqlite
from config import DATABASE_PATH
from typing import Optional, List, Dict, Any
from datetime import datetime


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
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
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
    """Проверяет, было ли объявление уже отправлено"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute(
            "SELECT id FROM sent_listings WHERE id = ?",
            (listing_id,)
        )
        result = await cursor.fetchone()
        return result is not None


async def mark_listing_sent(listing: Dict[str, Any]):
    """Отмечает объявление как отправленное"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
            INSERT OR REPLACE INTO sent_listings 
            (id, title, price, rooms, area, address, url, sent_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            listing.get("id"),
            listing.get("title"),
            listing.get("price"),
            listing.get("rooms"),
            listing.get("area"),
            listing.get("address"),
            listing.get("url"),
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

