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
    # Убираем названия городов из адреса для сравнения
    cities_to_remove = [
        "барановичи", "минск", "брест", "витебск", "гомель", "гродно", 
        "могилев", "могилёв", "бобруйск", "пинск", "орша", "мозырь",
        "лида", "борисов", "солигорск", "молодечно", "полоцк", "новополоцк"
    ]
    for city in cities_to_remove:
        norm_address = norm_address.replace(city, "")
    norm_address = norm_address.replace(",", "").strip()
    # Убираем лишние пробелы
    norm_address = " ".join(norm_address.split())
    
    # Округляем площадь до целого
    norm_area = int(area)
    # Цена с погрешностью ±5%
    price_bucket = int(price / 1000) * 1000  # Округляем до тысяч
    
    data = f"{rooms}:{norm_area}:{norm_address}:{price_bucket}"
    return hashlib.md5(data.encode()).hexdigest()[:16]


async def init_database():
    """Инициализация базы данных и создание таблиц"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        # Таблица отправленных объявлений (глобальная для дедупликации)
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
        
        # Таблица фильтров пользователей
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_filters (
                user_id INTEGER PRIMARY KEY,
                city TEXT DEFAULT 'барановичи',
                min_rooms INTEGER DEFAULT 1,
                max_rooms INTEGER DEFAULT 4,
                min_price INTEGER DEFAULT 0,
                max_price INTEGER DEFAULT 100000,
                is_active BOOLEAN DEFAULT 1,
                ai_mode BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Добавляем колонку ai_mode если её нет (для существующих БД)
        try:
            await db.execute("ALTER TABLE user_filters ADD COLUMN ai_mode BOOLEAN DEFAULT 0")
        
        # Добавляем поле seller_type если его нет (None = все, True = только агентства, False = только собственники)
        try:
            await db.execute("ALTER TABLE user_filters ADD COLUMN seller_type TEXT")
        except aiosqlite.OperationalError:
            pass  # Поле уже существует
            await db.commit()
        except aiosqlite.OperationalError:
            pass  # Колонка уже существует
        
        # Таблица отправленных объявлений каждому пользователю
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_sent_listings (
                user_id INTEGER,
                listing_id TEXT,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, listing_id)
            )
        """)
        
        # Индекс для быстрого поиска
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_user_sent ON user_sent_listings(user_id, listing_id)
        """)
        
        # Таблица выбранных ИИ вариантов для пользователя (для сравнения с новыми)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS ai_selected_listings (
                user_id INTEGER,
                listing_id TEXT,
                reason TEXT,
                selected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, listing_id)
            )
        """)
        
        # Индекс для быстрого поиска
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_ai_selected ON ai_selected_listings(user_id, selected_at)
        """)
        
        # Таблица ИИ-оценок объявлений (для проверки, было ли уже оценено)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS ai_valuations (
                user_id INTEGER,
                listing_id TEXT,
                evaluated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, listing_id)
            )
        """)
        
        # Индекс для быстрого поиска
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_ai_valuations ON ai_valuations(user_id, listing_id)
        """)
        
        # Старая таблица filters (для обратной совместимости, можно удалить позже)
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


# ========== Функции для работы с пользователями ==========

async def get_user_filters(user_id: int) -> Optional[Dict[str, Any]]:
    """Получает фильтры пользователя"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM user_filters WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if row:
            filters = dict(row)
            # Конвертируем BOOLEAN из SQLite (0/1) в Python bool
            # SQLite хранит BOOLEAN как INTEGER, поэтому нужно явно конвертировать
            if 'is_active' in filters:
                filters['is_active'] = bool(filters['is_active'])
            if 'ai_mode' in filters:
                filters['ai_mode'] = bool(filters['ai_mode'])
            return filters
        return None


async def set_user_filters(
    user_id: int,
    city: str = "барановичи",
    min_rooms: int = 1,
    max_rooms: int = 4,
    min_price: int = 0,
    max_price: int = 100000,
    is_active: bool = True,
    ai_mode: bool = False,
    seller_type: Optional[str] = None  # None = все, "owner" = только собственники, "company" = только агентства
):
    """Устанавливает фильтры пользователя"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        # Получаем текущие фильтры для сохранения seller_type если не передан
        cursor = await db.execute("SELECT seller_type FROM user_filters WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        current_seller_type = row[0] if row else None
        
        # Если seller_type не передан, сохраняем текущее значение
        if seller_type is None:
            seller_type = current_seller_type
        
        await db.execute("""
            INSERT OR REPLACE INTO user_filters 
            (user_id, city, min_rooms, max_rooms, min_price, max_price, is_active, ai_mode, seller_type, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            city,
            min_rooms,
            max_rooms,
            min_price,
            max_price,
            is_active,
            ai_mode,
            seller_type,
            datetime.now().isoformat()
        ))
        await db.commit()


async def set_user_ai_mode(user_id: int, ai_mode: bool):
    """Устанавливает режим ИИ для пользователя"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
            UPDATE user_filters 
            SET ai_mode = ?, updated_at = ?
            WHERE user_id = ?
        """, (ai_mode, datetime.now().isoformat(), user_id))
        await db.commit()


async def is_listing_sent_to_user(user_id: int, listing_id: str) -> bool:
    """Проверяет, было ли объявление отправлено пользователю"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute(
            "SELECT listing_id FROM user_sent_listings WHERE user_id = ? AND listing_id = ?",
            (user_id, listing_id)
        )
        result = await cursor.fetchone()
        return result is not None


async def mark_listing_sent_to_user(user_id: int, listing_id: str):
    """Отмечает объявление как отправленное пользователю"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
            INSERT OR IGNORE INTO user_sent_listings (user_id, listing_id, sent_at)
            VALUES (?, ?, ?)
        """, (user_id, listing_id, datetime.now().isoformat()))
        await db.commit()


async def get_active_users() -> List[int]:
    """Возвращает список ID активных пользователей (с включенными фильтрами)"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute(
            "SELECT user_id FROM user_filters WHERE is_active = 1"
        )
        rows = await cursor.fetchall()
        return [row[0] for row in rows]


async def save_ai_selected_listings(user_id: int, selected_listings: List[Dict[str, Any]]):
    """Сохраняет выбранные ИИ варианты для пользователя"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        # Удаляем старые записи для этого пользователя (оставляем только последние)
        await db.execute("DELETE FROM ai_selected_listings WHERE user_id = ?", (user_id,))
        
        # Сохраняем новые выбранные варианты
        for item in selected_listings:
            listing = item.get("listing")
            reason = item.get("reason", "")
            if listing:
                await db.execute("""
                    INSERT OR REPLACE INTO ai_selected_listings (user_id, listing_id, reason, selected_at)
                    VALUES (?, ?, ?, ?)
                """, (user_id, listing.id, reason, datetime.now().isoformat()))
        
        await db.commit()


async def get_ai_selected_listings(user_id: int) -> List[Dict[str, Any]]:
    """Получает последние выбранные ИИ варианты для пользователя"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("""
            SELECT listing_id, reason, selected_at 
            FROM ai_selected_listings 
            WHERE user_id = ? 
            ORDER BY selected_at DESC 
            LIMIT 10
        """, (user_id,))
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def is_listing_ai_valuated(user_id: int, listing_id: str) -> bool:
    """Проверяет, было ли объявление уже оценено через ИИ для пользователя"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute(
            "SELECT listing_id FROM ai_valuations WHERE user_id = ? AND listing_id = ?",
            (user_id, listing_id)
        )
        result = await cursor.fetchone()
        return result is not None


async def mark_listing_ai_valuated(user_id: int, listing_id: str):
    """Отмечает объявление как оцененное через ИИ"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
            INSERT OR IGNORE INTO ai_valuations (user_id, listing_id, evaluated_at)
            VALUES (?, ?, ?)
        """, (user_id, listing_id, datetime.now().isoformat()))
        await db.commit()


async def get_listing_by_id(listing_id: str) -> Optional[Dict[str, Any]]:
    """Получает объявление из базы данных по ID"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM sent_listings WHERE id = ?",
            (listing_id,)
        )
        row = await cursor.fetchone()
        if row:
            return dict(row)
        return None

