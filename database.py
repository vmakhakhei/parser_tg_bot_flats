"""
Модуль для работы с базой данных SQLite
Хранит информацию об уже отправленных объявлениях и настройках фильтров

Абстракция для работы с БД:
- SQLite (локальная БД) - основная реализация
- Turso (кэширование) - опциональная реализация через абстракцию

ВАЖНО: Весь код должен использовать только интерфейс из этого модуля,
а не конкретную реализацию Turso (database_turso) напрямую.
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
            await db.commit()
        except aiosqlite.OperationalError:
            pass  # Колонка уже существует
        
        # Добавляем поле seller_type если его нет (None = все, True = только агентства, False = только собственники)
        try:
            await db.execute("ALTER TABLE user_filters ADD COLUMN seller_type TEXT")
            await db.commit()
        except aiosqlite.OperationalError:
            pass  # Поле уже существует
        
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
        
        # Таблица для идемпотентной отправки объявлений (предотвращение дублей)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS sent_ads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                ad_external_id TEXT NOT NULL,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Уникальный индекс для предотвращения дублей
        await db.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_sent_user_ad ON sent_ads(user_id, ad_external_id)
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


async def clear_old_listings(days: int = 30):
    """Удаляет старые записи об отправленных объявлениях"""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
            DELETE FROM sent_listings 
            WHERE sent_at < datetime('now', ? || ' days')
        """, (f"-{days}",))
        await db.commit()


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
    # #region agent log
    import json
    import time
    try:
        with open('/Users/vmakhakei/TG BOT/.cursor/debug.log', 'a') as f:
            f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"A","location":"database.py:320","message":"get_active_users entry","data":{"DATABASE_PATH":DATABASE_PATH},"timestamp":int(time.time()*1000)})+'\n')
    except: pass
    # #endregion
    
    # Проверяем, используется ли Turso
    from config import USE_TURSO_CACHE
    # #region agent log
    try:
        with open('/Users/vmakhakei/TG BOT/.cursor/debug.log', 'a') as f:
            f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"B","location":"database.py:325","message":"USE_TURSO_CACHE check","data":{"USE_TURSO_CACHE":USE_TURSO_CACHE},"timestamp":int(time.time()*1000)})+'\n')
    except: pass
    # #endregion
    
    if USE_TURSO_CACHE:
        try:
            from database_turso import get_active_users_turso
            result = await get_active_users_turso()
            # #region agent log
            try:
                with open('/Users/vmakhakei/TG BOT/.cursor/debug.log', 'a') as f:
                    f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"B","location":"database.py:332","message":"get_active_users_turso result","data":{"count":len(result),"user_ids":result},"timestamp":int(time.time()*1000)})+'\n')
            except: pass
            # #endregion
            return result
        except Exception as e:
            # #region agent log
            try:
                with open('/Users/vmakhakei/TG BOT/.cursor/debug.log', 'a') as f:
                    f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"B","location":"database.py:337","message":"get_active_users_turso error","data":{"error":str(e)},"timestamp":int(time.time()*1000)})+'\n')
            except: pass
            # #endregion
            # Fallback to SQLite
            pass
    
    # SQLite fallback
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute(
            "SELECT user_id FROM user_filters WHERE is_active = 1"
        )
        rows = await cursor.fetchall()
        result = [row[0] for row in rows]
        # #region agent log
        try:
            with open('/Users/vmakhakei/TG BOT/.cursor/debug.log', 'a') as f:
                f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"A","location":"database.py:350","message":"get_active_users SQLite result","data":{"count":len(result),"user_ids":result},"timestamp":int(time.time()*1000)})+'\n')
        except: pass
        # #endregion
        return result


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


async def is_ad_sent_to_user(user_id: int, ad_external_id: str) -> bool:
    """Проверяет, было ли объявление уже отправлено пользователю (идемпотентная проверка)
    
    Args:
        user_id: ID пользователя
        ad_external_id: Внешний ID объявления (listing.id)
    
    Returns:
        True если объявление уже было отправлено, False иначе
    """
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute(
            "SELECT 1 FROM sent_ads WHERE user_id = ? AND ad_external_id = ?",
            (str(user_id), ad_external_id)
        )
        result = await cursor.fetchone()
        return result is not None


async def mark_ad_sent_to_user(user_id: int, ad_external_id: str):
    """Отмечает объявление как отправленное пользователю (идемпотентная запись)
    
    Args:
        user_id: ID пользователя
        ad_external_id: Внешний ID объявления (listing.id)
    """
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
            INSERT OR IGNORE INTO sent_ads (user_id, ad_external_id, sent_at)
            VALUES (?, ?, ?)
        """, (str(user_id), ad_external_id, datetime.now().isoformat()))
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


# ========== АБСТРАКЦИЯ ДЛЯ TURSO (кэширование) ==========
# Все функции Turso доступны через этот интерфейс
# Код не должен импортировать database_turso напрямую

async def ensure_turso_tables_exist() -> bool:
    """
    Проверяет и создает все необходимые таблицы Turso если их нет
    
    Returns:
        True если успешно, False при ошибке
    """
    from config import USE_TURSO_CACHE
    if not USE_TURSO_CACHE:
        return False
    
    try:
        from database_turso import ensure_tables_exist
        return await ensure_tables_exist()
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"Не удалось создать таблицы Turso: {e}")
        return False


async def create_or_update_user_turso(
    user_id: int,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None
) -> bool:
    """
    Создает или обновляет пользователя в Turso
    
    Returns:
        True если успешно, False при ошибке
    """
    from config import USE_TURSO_CACHE
    if not USE_TURSO_CACHE:
        return False
    
    try:
        from database_turso import create_or_update_user
        return await create_or_update_user(user_id, username, first_name, last_name)
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"Не удалось создать/обновить пользователя в Turso: {e}")
        return False


async def get_user_filters_turso(user_id: int) -> Optional[Dict[str, Any]]:
    """
    Получает фильтры пользователя из Turso
    
    Returns:
        Словарь с фильтрами или None
    """
    from config import USE_TURSO_CACHE
    if not USE_TURSO_CACHE:
        return None
    
    try:
        from database_turso import get_user_filters_turso
        return await get_user_filters_turso(user_id)
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"Не удалось получить фильтры из Turso: {e}")
        return None


async def set_user_filters_turso(
    user_id: int,
    min_price: int = 0,
    max_price: Optional[int] = None,
    rooms: Optional[List[int]] = None,
    region: str = "барановичи",
    active: bool = True,
    ai_mode: bool = False,
    seller_type: Optional[str] = None
) -> bool:
    """
    Устанавливает фильтры пользователя в Turso
    
    Returns:
        True если успешно, False при ошибке
    """
    from config import USE_TURSO_CACHE
    if not USE_TURSO_CACHE:
        return False
    
    try:
        from database_turso import set_user_filters_turso
        return await set_user_filters_turso(
            user_id, min_price, max_price, rooms, region, active, ai_mode, seller_type
        )
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"Не удалось установить фильтры в Turso: {e}")
        return False


async def get_cached_listings_by_filters_turso(
    city: str,
    min_rooms: int,
    max_rooms: int,
    min_price: int,
    max_price: int,
    limit: int = 100,
    status: str = "active"
) -> List[Dict[str, Any]]:
    """
    Получает объявления из кэша Turso по фильтрам
    
    Returns:
        Список объявлений из кэша или пустой список при ошибке
    """
    from config import USE_TURSO_CACHE
    if not USE_TURSO_CACHE:
        return []
    
    try:
        from database_turso import get_cached_listings_by_filters
        return await get_cached_listings_by_filters(
            city, min_rooms, max_rooms, min_price, max_price, limit, status
        )
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"Не удалось получить объявления из кэша Turso: {e}")
        return []


def cached_listing_to_listing_turso(cached_dict: Dict[str, Any]):
    """
    Конвертирует объявление из кэша Turso (словарь) в объект Listing
    
    Returns:
        Объект Listing или None при ошибке
    """
    try:
        from database_turso import cached_listing_to_listing
        return cached_listing_to_listing(cached_dict)
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"Не удалось конвертировать объявление из кэша: {e}")
        return None


async def cache_listings_batch_turso(listings: List) -> int:
    """
    Сохраняет несколько объявлений в кэш Turso батчем
    
    Args:
        listings: Список объектов Listing
    
    Returns:
        Количество успешно сохраненных объявлений
    """
    from config import USE_TURSO_CACHE
    if not USE_TURSO_CACHE:
        return 0
    
    try:
        from database_turso import cache_listings_batch
        return await cache_listings_batch(listings)
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"Не удалось сохранить объявления в кэш Turso: {e}")
        return 0


async def update_cached_listings_daily_turso():
    """
    Ежедневное обновление кэша Turso: проверка статуса объявлений
    """
    from config import USE_TURSO_CACHE
    if not USE_TURSO_CACHE:
        return
    
    try:
        from database_turso import update_cached_listings_daily
        await update_cached_listings_daily()
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"Не удалось обновить кэш Turso: {e}")


# ========== АБСТРАКЦИЯ ДЛЯ TURSO (кэширование) ==========
# Все функции Turso доступны через этот интерфейс
# Код не должен импортировать database_turso напрямую

async def ensure_turso_tables_exist() -> bool:
    """
    Проверяет и создает все необходимые таблицы Turso если их нет
    
    Returns:
        True если успешно, False при ошибке
    """
    from config import USE_TURSO_CACHE
    if not USE_TURSO_CACHE:
        return False
    
    try:
        from database_turso import ensure_tables_exist
        return await ensure_tables_exist()
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"Не удалось создать таблицы Turso: {e}")
        return False


async def create_or_update_user_turso(
    user_id: int,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None
) -> bool:
    """
    Создает или обновляет пользователя в Turso
    
    Returns:
        True если успешно, False при ошибке
    """
    from config import USE_TURSO_CACHE
    if not USE_TURSO_CACHE:
        return False
    
    try:
        from database_turso import create_or_update_user
        return await create_or_update_user(user_id, username, first_name, last_name)
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"Не удалось создать/обновить пользователя в Turso: {e}")
        return False


async def get_user_filters_turso(user_id: int) -> Optional[Dict[str, Any]]:
    """
    Получает фильтры пользователя из Turso
    
    Returns:
        Словарь с фильтрами или None
    """
    from config import USE_TURSO_CACHE
    if not USE_TURSO_CACHE:
        return None
    
    try:
        from database_turso import get_user_filters_turso
        return await get_user_filters_turso(user_id)
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"Не удалось получить фильтры из Turso: {e}")
        return None


async def set_user_filters_turso(
    user_id: int,
    min_price: int = 0,
    max_price: Optional[int] = None,
    rooms: Optional[List[int]] = None,
    region: str = "барановичи",
    active: bool = True,
    ai_mode: bool = False,
    seller_type: Optional[str] = None
) -> bool:
    """
    Устанавливает фильтры пользователя в Turso
    
    Returns:
        True если успешно, False при ошибке
    """
    from config import USE_TURSO_CACHE
    if not USE_TURSO_CACHE:
        return False
    
    try:
        from database_turso import set_user_filters_turso
        return await set_user_filters_turso(
            user_id, min_price, max_price, rooms, region, active, ai_mode, seller_type
        )
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"Не удалось установить фильтры в Turso: {e}")
        return False


async def get_cached_listings_by_filters_turso(
    city: str,
    min_rooms: int,
    max_rooms: int,
    min_price: int,
    max_price: int,
    limit: int = 100,
    status: str = "active"
) -> List[Dict[str, Any]]:
    """
    Получает объявления из кэша Turso по фильтрам
    
    Returns:
        Список объявлений из кэша или пустой список при ошибке
    """
    from config import USE_TURSO_CACHE
    if not USE_TURSO_CACHE:
        return []
    
    try:
        from database_turso import get_cached_listings_by_filters
        return await get_cached_listings_by_filters(
            city, min_rooms, max_rooms, min_price, max_price, limit, status
        )
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"Не удалось получить объявления из кэша Turso: {e}")
        return []


def cached_listing_to_listing_turso(cached_dict: Dict[str, Any]):
    """
    Конвертирует объявление из кэша Turso (словарь) в объект Listing
    
    Returns:
        Объект Listing
    """
    try:
        from database_turso import cached_listing_to_listing
        return cached_listing_to_listing(cached_dict)
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"Не удалось конвертировать объявление из кэша: {e}")
        return None


async def cache_listings_batch_turso(listings: List) -> int:
    """
    Сохраняет несколько объявлений в кэш Turso батчем
    
    Args:
        listings: Список объектов Listing
    
    Returns:
        Количество успешно сохраненных объявлений
    """
    from config import USE_TURSO_CACHE
    if not USE_TURSO_CACHE:
        return 0
    
    try:
        from database_turso import cache_listings_batch
        return await cache_listings_batch(listings)
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"Не удалось сохранить объявления в кэш Turso: {e}")
        return 0


async def update_cached_listings_daily_turso():
    """
    Ежедневное обновление кэша Turso: проверка статуса объявлений
    """
    from config import USE_TURSO_CACHE
    if not USE_TURSO_CACHE:
        return
    
    try:
        from database_turso import update_cached_listings_daily
        await update_cached_listings_daily()
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"Не удалось обновить кэш Turso: {e}")

