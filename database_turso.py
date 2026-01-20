"""
Модуль для работы с Turso Database (кэширование объявлений)
Используется для экономии трафика и API вызовов

Особенности:
- Контекстные менеджеры для транзакций
- Автоматический rollback при ошибках
- Атомарность всех операций записи
"""
import json
import logging
import asyncio
from typing import Optional, List, Dict, Any
from datetime import datetime
from contextlib import contextmanager

logger = logging.getLogger(__name__)

# Импортируем error_logger для логирования ошибок
try:
    from error_logger import log_error, log_warning, log_info
except ImportError:
    def log_error(source, message, exception=None):
        logger.error(f"[{source}] {message}: {exception}")
    def log_warning(source, message):
        logger.warning(f"[{source}] {message}")
    def log_info(source, message):
        logger.info(f"[{source}] {message}")

# Импорт libsql с обработкой ошибок
try:
    import libsql
    LIBSQL_AVAILABLE = True
except ImportError as e:
    logger.error(f"Не удалось импортировать libsql: {e}")
    logger.error("Установите правильный пакет: pip install libsql")
    libsql = None
    LIBSQL_AVAILABLE = False

from config import TURSO_DB_URL, TURSO_AUTH_TOKEN, USE_TURSO_CACHE
from database import generate_content_hash
from scrapers.base import Listing


class TursoTransaction:
    """
    Контекстный менеджер для транзакций Turso
    
    Обеспечивает:
    - Атомарность операций
    - Автоматический rollback при ошибках
    - Автоматическое закрытие соединения
    """
    
    def __init__(self):
        self.conn = None
        self._in_transaction = False
    
    def __enter__(self):
        """Вход в контекст - создаем соединение и начинаем транзакцию"""
        self.conn = get_turso_connection()
        if not self.conn:
            raise RuntimeError("Не удалось создать соединение с Turso")
        
        # Начинаем транзакцию (в SQLite транзакция начинается автоматически)
        # Но для явности можно использовать BEGIN
        try:
            self.conn.execute("BEGIN")
            self._in_transaction = True
        except Exception as e:
            log_error("turso_transaction", "Ошибка начала транзакции", e)
            if self.conn:
                self.conn.close()
            raise
        
        return self.conn
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Выход из контекста - commit или rollback"""
        if not self.conn:
            return False
        
        try:
            if exc_type is None:
                # Нет ошибки - делаем commit
                if self._in_transaction:
                    self.conn.commit()
                    log_info("turso_transaction", "Транзакция успешно зафиксирована")
            else:
                # Есть ошибка - делаем rollback
                if self._in_transaction:
                    self.conn.rollback()
                    log_warning("turso_transaction", f"Транзакция откачена из-за ошибки: {exc_type.__name__}")
        except Exception as e:
            log_error("turso_transaction", "Ошибка при завершении транзакции", e)
        finally:
            # Всегда закрываем соединение
            try:
                if self.conn:
                    self.conn.close()
            except Exception as e:
                log_error("turso_transaction", "Ошибка закрытия соединения", e)
        
        # Возвращаем False, чтобы не подавлять исключение
        return False


@contextmanager
def turso_transaction():
    """
    Контекстный менеджер для транзакций Turso (удобная функция)
    
    Использование:
        with turso_transaction() as conn:
            conn.execute("INSERT INTO ...")
            conn.execute("UPDATE ...")
            # При выходе автоматически commit или rollback
    """
    transaction = TursoTransaction()
    try:
        conn = transaction.__enter__()
        yield conn
    except Exception as e:
        transaction.__exit__(type(e), e, e.__traceback__)
        raise
    else:
        transaction.__exit__(None, None, None)


def get_turso_connection():
    """Создает соединение с Turso (синхронное)"""
    if not USE_TURSO_CACHE:
        return None
    
    if not TURSO_DB_URL or not TURSO_AUTH_TOKEN:
        logger.warning("Turso не настроен: отсутствуют TURSO_DB_URL или TURSO_AUTH_TOKEN")
        return None
    
    if not LIBSQL_AVAILABLE or libsql is None:
        logger.error("Библиотека libsql не установлена. Установите: pip install libsql")
        return None
    
    try:
        return libsql.connect(
            TURSO_DB_URL,
            auth_token=TURSO_AUTH_TOKEN
        )
    except Exception as e:
        logger.error(f"Ошибка создания соединения с Turso: {e}")
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
    conn = get_turso_connection()
    if not conn:
        return []
    
    try:
        # Выполняем запрос в отдельном потоке, т.к. libsql синхронный
        def _execute():
            cursor = conn.execute("""
                SELECT * FROM cached_listings
                WHERE city = ? 
                AND rooms >= ? AND rooms <= ?
                AND price >= ? AND price <= ?
                AND status = ?
                ORDER BY updated_at DESC
                LIMIT ?
            """, (city, min_rooms, max_rooms, min_price, max_price, status, limit))
            return cursor.fetchall()
        
        # Выполняем запрос и получаем колонки
        def _execute_with_columns():
            cursor = conn.execute("""
                SELECT * FROM cached_listings
                WHERE city = ? 
                AND rooms >= ? AND rooms <= ?
                AND price >= ? AND price <= ?
                AND status = ?
                ORDER BY updated_at DESC
                LIMIT ?
            """, (city, min_rooms, max_rooms, min_price, max_price, status, limit))
            rows = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]
            return rows, columns
        
        rows, columns = await asyncio.to_thread(_execute_with_columns)
        
        listings = []
        for row in rows:
            try:
                # Правильная конвертация Row в словарь
                if hasattr(row, '_asdict'):
                    # Если это Row объект с методом _asdict
                    listing_dict = row._asdict()
                elif isinstance(row, dict):
                    # Если уже словарь
                    listing_dict = row
                else:
                    # Если это кортеж или список - используем zip с колонками
                    listing_dict = dict(zip(columns, row))
            except Exception as e:
                log_error("turso_cache", f"Ошибка конвертации строки в словарь: {e}, row={row}, columns={columns}")
                continue
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
        if conn:
            conn.close()


async def cache_listing(listing: Listing) -> bool:
    """
    Сохраняет объявление в кэш (атомарная операция с транзакцией)
    
    Returns:
        True если успешно, False при ошибке
    """
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
        
        def _execute():
            with turso_transaction() as conn:
                # Проверяем, существует ли запись
                cursor = conn.execute("SELECT first_seen_at FROM cached_listings WHERE id = ?", (listing.id,))
                existing = cursor.fetchone()
                first_seen = datetime.now().isoformat()
                if existing:
                    first_seen = existing[0] if existing[0] else first_seen
                
                conn.execute("""
                    INSERT OR REPLACE INTO cached_listings 
                    (id, source, title, price, rooms, area, address, url, city, 
                     price_usd, currency, floor, year_built, description, photos, 
                     is_company, content_hash, status, updated_at, first_seen_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    listing.id,
                    listing.source,
                    listing.title,
                    listing.price,
                    listing.rooms,
                    listing.area,
                    listing.address,
                    listing.url,
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
                    first_seen
                ))
                # Commit происходит автоматически при выходе из контекста
        
        await asyncio.to_thread(_execute)
        return True
        
    except Exception as e:
        log_error("turso_cache", f"Ошибка сохранения объявления {listing.id} в кэш", e)
        return False


async def cache_listings_batch(listings: List[Listing]) -> int:
    """
    Сохраняет несколько объявлений в кэш батчем (атомарная операция)
    
    Все объявления сохраняются в одной транзакции - либо все успешно, либо все откатываются.
    
    Returns:
        Количество успешно сохраненных объявлений
    """
    if not listings:
        return 0
    
    try:
        def _execute_batch():
            saved_count = 0
            with turso_transaction() as conn:
                for listing in listings:
                    try:
                        content_hash = generate_content_hash(
                            listing.rooms,
                            listing.area,
                            listing.address,
                            listing.price
                        )
                        
                        photos_json = json.dumps(listing.photos) if listing.photos else "[]"
                        is_company_int = 1 if listing.is_company is True else (0 if listing.is_company is False else None)
                        
                        # Проверяем, существует ли запись
                        cursor = conn.execute("SELECT first_seen_at FROM cached_listings WHERE id = ?", (listing.id,))
                        existing = cursor.fetchone()
                        first_seen = datetime.now().isoformat()
                        if existing:
                            first_seen = existing[0] if existing[0] else first_seen
                        
                        conn.execute("""
                            INSERT OR REPLACE INTO cached_listings 
                            (id, source, title, price, rooms, area, address, url, city, 
                             price_usd, currency, floor, year_built, description, photos, 
                             is_company, content_hash, status, updated_at, first_seen_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            listing.id,
                            listing.source,
                            listing.title,
                            listing.price,
                            listing.rooms,
                            listing.area,
                            listing.address,
                            listing.url,
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
                            first_seen
                        ))
                        saved_count += 1
                    except Exception as e:
                        log_error("turso_cache", f"Ошибка сохранения объявления {listing.id} в батче", e)
                        # Продолжаем с другими объявлениями, но транзакция откатится при выходе
                        raise  # Пробрасываем ошибку, чтобы транзакция откатилась
                
                # Если все успешно, транзакция зафиксируется автоматически
                return saved_count
        
        saved_count = await asyncio.to_thread(_execute_batch)
        log_info("turso_cache", f"Сохранено {saved_count} из {len(listings)} объявлений в кэш (атомарно)")
        return saved_count
        
    except Exception as e:
        log_error("turso_cache", f"Ошибка батчевого сохранения объявлений: все изменения откачены", e)
        return 0


async def mark_listing_deleted(listing_id: str) -> bool:
    """Отмечает объявление как удаленное (атомарная операция)"""
    try:
        def _execute():
            with turso_transaction() as conn:
                conn.execute("""
                    UPDATE cached_listings 
                    SET status = 'deleted', updated_at = ?
                    WHERE id = ?
                """, (datetime.now().isoformat(), listing_id))
                # Commit происходит автоматически
        
        await asyncio.to_thread(_execute)
        return True
        
    except Exception as e:
        log_error("turso_cache", f"Ошибка отметки объявления {listing_id} как удаленного", e)
        return False


async def update_cached_listing(listing: Listing) -> bool:
    """Обновляет объявление в кэше (атомарная операция)"""
    try:
        content_hash = generate_content_hash(
            listing.rooms,
            listing.area,
            listing.address,
            listing.price
        )
        
        photos_json = json.dumps(listing.photos) if listing.photos else "[]"
        is_company_int = 1 if listing.is_company is True else (0 if listing.is_company is False else None)
        
        def _execute():
            with turso_transaction() as conn:
                conn.execute("""
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
                # Commit происходит автоматически
        
        await asyncio.to_thread(_execute)
        return True
        
    except Exception as e:
        log_error("turso_cache", f"Ошибка обновления объявления {listing.id} в кэше", e)
        return False


async def is_listing_cached(listing_id: str) -> bool:
    """Проверяет, есть ли объявление в кэше"""
    conn = get_turso_connection()
    if not conn:
        return False
    
    try:
        def _execute():
            cursor = conn.execute(
                "SELECT id FROM cached_listings WHERE id = ? AND status = 'active'",
                (listing_id,)
            )
            return len(cursor.fetchall()) > 0
        
        return await asyncio.to_thread(_execute)
        
    except Exception as e:
        logger.error(f"Ошибка проверки наличия объявления в кэше: {e}")
        return False
    finally:
        if conn:
            conn.close()


async def get_listing_by_url(url: str) -> Optional[Dict[str, Any]]:
    """Получает объявление из кэша по URL"""
    conn = get_turso_connection()
    if not conn:
        return None
    
    try:
        def _execute():
            cursor = conn.execute(
                "SELECT * FROM cached_listings WHERE url = ? AND status = 'active'",
                (url,)
            )
            return cursor.fetchone()
        
        row = await asyncio.to_thread(_execute)
        
        if row:
            listing_dict = dict(row)
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
        if conn:
            conn.close()


async def update_cached_listings_daily():
    """
    Ежедневное обновление кэша: проверка статуса объявлений (атомарная операция)
    Отмечает удаленные объявления и обновляет измененные
    """
    try:
        log_info("turso_daily", "🔄 Начало ежедневного обновления кэша...")
        
        def _execute():
            with turso_transaction() as conn:
                # Получаем все активные объявления старше 1 дня
                cursor = conn.execute("""
                    SELECT id, url, source FROM cached_listings
                    WHERE status = 'active'
                    AND last_seen_at < datetime('now', '-1 day')
                    LIMIT 100
                """)
                rows = cursor.fetchall()
                
                updated_count = 0
                for row in rows:
                    listing_id = row[0]
                    # Просто обновляем last_seen_at для активных объявлений
                    conn.execute("""
                        UPDATE cached_listings
                        SET last_seen_at = ?
                        WHERE id = ?
                    """, (datetime.now().isoformat(), listing_id))
                    updated_count += 1
                
                # Удаляем очень старые удаленные объявления (старше 7 дней)
                conn.execute("""
                    DELETE FROM cached_listings
                    WHERE status = 'deleted'
                    AND updated_at < datetime('now', '-7 days')
                """)
                # Commit происходит автоматически при выходе из контекста
                
                return updated_count
        
        updated_count = await asyncio.to_thread(_execute)
        
        log_info("turso_daily", f"✅ Ежедневное обновление кэша завершено: обновлено {updated_count} объявлений")
        
    except Exception as e:
        log_error("turso_daily", "Ошибка ежедневного обновления кэша", e)


async def ensure_tables_exist():
    """
    Проверяет и создает все необходимые таблицы если их нет (атомарная операция)
    Вызывается автоматически при запуске бота
    """
    try:
        def _check_and_create():
            with turso_transaction() as conn:
                # 1. Таблица users
                cursor = conn.execute("""
                    SELECT name FROM sqlite_master 
                    WHERE type='table' AND name='users'
                """)
                if not cursor.fetchone():
                    logger.info("📋 Создание таблицы users...")
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS users (
                            user_id INTEGER PRIMARY KEY,
                            username TEXT,
                            first_name TEXT,
                            last_name TEXT,
                            last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )
                    """)
                    conn.execute("""
                        CREATE INDEX IF NOT EXISTS idx_users_last_activity 
                        ON users(last_activity)
                    """)
                    logger.info("✅ Таблица users создана")
                
                # 2. Таблица user_filters (новая структура)
                cursor = conn.execute("""
                    SELECT name FROM sqlite_master 
                    WHERE type='table' AND name='user_filters'
                """)
                if not cursor.fetchone():
                    logger.info("📋 Создание таблицы user_filters...")
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS user_filters (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            user_id INTEGER NOT NULL,
                            min_price INTEGER DEFAULT 0,
                            max_price INTEGER,
                            rooms TEXT,  -- JSON массив [1,2,3]
                            region TEXT DEFAULT 'барановичи',
                            active INTEGER DEFAULT 1,
                            ai_mode INTEGER DEFAULT 0,
                            seller_type TEXT,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            FOREIGN KEY (user_id) REFERENCES users(user_id)
                        )
                    """)
                    conn.execute("""
                        CREATE INDEX IF NOT EXISTS idx_user_filters_user_id 
                        ON user_filters(user_id)
                    """)
                    conn.execute("""
                        CREATE INDEX IF NOT EXISTS idx_user_filters_active 
                        ON user_filters(active)
                    """)
                    logger.info("✅ Таблица user_filters создана")
                
                # 3. Таблица apartments (основная таблица объявлений)
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
                    
                    # Создаем индексы для быстрого поиска
                    conn.execute("""
                        CREATE INDEX IF NOT EXISTS idx_apartments_price_usd 
                        ON apartments(price_usd)
                    """)
                    conn.execute("""
                        CREATE INDEX IF NOT EXISTS idx_apartments_rooms 
                        ON apartments(rooms)
                    """)
                    conn.execute("""
                        CREATE INDEX IF NOT EXISTS idx_apartments_source 
                        ON apartments(source)
                    """)
                    conn.execute("""
                        CREATE INDEX IF NOT EXISTS idx_apartments_list_time 
                        ON apartments(list_time)
                    """)
                    conn.execute("""
                        CREATE INDEX IF NOT EXISTS idx_apartments_last_checked 
                        ON apartments(last_checked)
                    """)
                    conn.execute("""
                        CREATE INDEX IF NOT EXISTS idx_apartments_is_active 
                        ON apartments(is_active)
                    """)
                    conn.execute("""
                        CREATE INDEX IF NOT EXISTS idx_apartments_source_active 
                        ON apartments(source, is_active)
                    """)
                    # Уникальный индекс для предотвращения дублей объявлений
                    conn.execute("""
                        CREATE UNIQUE INDEX IF NOT EXISTS idx_apartments_source_ad_id 
                        ON apartments(source, ad_id)
                    """)
                    logger.info("✅ Таблица apartments и индексы созданы")
                
                # 4. Таблица api_query_cache (для кэширования запросов)
                cursor = conn.execute("""
                    SELECT name FROM sqlite_master 
                    WHERE type='table' AND name='api_query_cache'
                """)
                if not cursor.fetchone():
                    logger.info("📋 Создание таблицы api_query_cache...")
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS api_query_cache (
                            query_hash TEXT PRIMARY KEY,
                            last_fetched TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            result_count INTEGER DEFAULT 0,
                            query_params TEXT
                        )
                    """)
                    conn.execute("""
                        CREATE INDEX IF NOT EXISTS idx_query_cache_last_fetched 
                        ON api_query_cache(last_fetched)
                    """)
                    logger.info("✅ Таблица api_query_cache создана")
                
                # 5. Старая таблица cached_listings (оставляем для совместимости, но не используем)
                cursor = conn.execute("""
                    SELECT name FROM sqlite_master 
                    WHERE type='table' AND name='cached_listings'
                """)
                if not cursor.fetchone():
                    logger.info("📋 Создание таблицы cached_listings (legacy)...")
                    conn.execute("""
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
                    logger.info("✅ Таблица cached_listings (legacy) создана")
                
                # Commit происходит автоматически при выходе из контекста
        
        await asyncio.to_thread(_check_and_create)
        return True
        
    except Exception as e:
        log_error("turso_tables", "Ошибка создания таблиц Turso", e)
        return False


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


# ========== НОВЫЕ ФУНКЦИИ ДЛЯ РЕФАКТОРИНГА ==========

async def create_or_update_user(
    user_id: int,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None
) -> bool:
    """
    Создает или обновляет пользователя в таблице users (атомарная операция)
    Автоматически обновляет last_activity
    """
    try:
        def _execute():
            with turso_transaction() as conn:
                conn.execute("""
                    INSERT INTO users (user_id, username, first_name, last_name, last_activity, created_at)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    ON CONFLICT(user_id) DO UPDATE SET
                        username = COALESCE(excluded.username, username),
                        first_name = COALESCE(excluded.first_name, first_name),
                        last_name = COALESCE(excluded.last_name, last_name),
                        last_activity = CURRENT_TIMESTAMP
                """, (user_id, username, first_name, last_name))
                # Commit происходит автоматически
        
        await asyncio.to_thread(_execute)
        return True
    except Exception as e:
        log_error("turso_users", f"Ошибка создания/обновления пользователя {user_id}", e)
        return False


async def get_user_filters_turso(user_id: int) -> Optional[Dict[str, Any]]:
    """
    Получает фильтры пользователя из Turso
    Возвращает словарь с фильтрами или None
    """
    conn = get_turso_connection()
    if not conn:
        return None
    
    try:
        def _execute():
            cursor = conn.execute("""
                SELECT * FROM user_filters 
                WHERE user_id = ? 
                ORDER BY updated_at DESC 
                LIMIT 1
            """, (user_id,))
            row = cursor.fetchone()
            if row:
                # Конвертируем Row в словарь
                columns = [desc[0] for desc in cursor.description]
                result = dict(zip(columns, row))
                # Конвертируем rooms из JSON строки в список
                if result.get("rooms"):
                    try:
                        result["rooms"] = json.loads(result["rooms"])
                    except:
                        result["rooms"] = []
                else:
                    result["rooms"] = []
                # Конвертируем INTEGER в bool
                result["active"] = bool(result.get("active", 1))
                result["ai_mode"] = bool(result.get("ai_mode", 0))
                return result
            return None
        
        return await asyncio.to_thread(_execute)
    except Exception as e:
        logger.error(f"Ошибка получения фильтров пользователя {user_id}: {e}")
        return None
    finally:
        if conn:
            conn.close()


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
    Устанавливает фильтры пользователя в Turso (атомарная операция)
    rooms передается как список [1,2,3] и сохраняется как JSON
    """
    try:
        def _execute():
            # Конвертируем rooms в JSON строку
            rooms_json = json.dumps(rooms) if rooms else None
            
            with turso_transaction() as conn:
                conn.execute("""
                    INSERT INTO user_filters 
                    (user_id, min_price, max_price, rooms, region, active, ai_mode, seller_type, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(user_id) DO UPDATE SET
                        min_price = excluded.min_price,
                        max_price = excluded.max_price,
                        rooms = excluded.rooms,
                        region = excluded.region,
                        active = excluded.active,
                        ai_mode = excluded.ai_mode,
                        seller_type = excluded.seller_type,
                        updated_at = CURRENT_TIMESTAMP
                """, (
                    user_id,
                    min_price,
                    max_price,
                    rooms_json,
                    region,
                    1 if active else 0,
                    1 if ai_mode else 0,
                    seller_type
                ))
                # Commit происходит автоматически
        
        await asyncio.to_thread(_execute)
        return True
    except Exception as e:
        log_error("turso_filters", f"Ошибка установки фильтров пользователя {user_id}", e)
        return False


async def get_latest_ad_external_id(source: str, city: str = None) -> Optional[str]:
    """
    Получает последний сохранённый external_id (ad_id) из базы данных
    
    Args:
        source: Источник объявления (например "kufar")
        city: Город (опционально, для фильтрации)
    
    Returns:
        ad_id последнего объявления или None если нет объявлений
    """
    conn = get_turso_connection()
    if not conn:
        return None
    
    try:
        def _execute():
            if city:
                # Если указан город, фильтруем по нему (нужно добавить поле city в apartments если его нет)
                cursor = conn.execute("""
                    SELECT ad_id
                    FROM apartments
                    WHERE source = ?
                    ORDER BY created_at DESC
                    LIMIT 1
                """, (source,))
            else:
                cursor = conn.execute("""
                    SELECT ad_id
                    FROM apartments
                    WHERE source = ?
                    ORDER BY created_at DESC
                    LIMIT 1
                """, (source,))
            row = cursor.fetchone()
            return row[0] if row else None
        
        return await asyncio.to_thread(_execute)
    except Exception as e:
        logger.error(f"Ошибка получения последнего ad_id для {source}: {e}")
        return None
    finally:
        if conn:
            conn.close()


async def ad_exists(source: str, ad_id: str) -> bool:
    """
    Проверяет, существует ли объявление в базе данных
    
    Args:
        source: Источник объявления (например "kufar")
        ad_id: ID объявления (например "kufar_1048044245")
    
    Returns:
        True если объявление существует, False иначе
    """
    conn = get_turso_connection()
    if not conn:
        return False
    
    try:
        def _execute():
            cursor = conn.execute("""
                SELECT 1
                FROM apartments
                WHERE source = ? AND ad_id = ?
                LIMIT 1
            """, (source, ad_id))
            return cursor.fetchone() is not None
        
        return await asyncio.to_thread(_execute)
    except Exception as e:
        logger.error(f"Ошибка проверки существования объявления {ad_id} для {source}: {e}")
        return False
    finally:
        if conn:
            conn.close()


async def get_active_users_turso() -> List[int]:
    """
    Возвращает список ID активных пользователей из Turso
    Активный пользователь = имеет запись в user_filters (писал боту хотя бы раз)
    УБРАЛИ условие active = 1 - теперь все пользователи с фильтрами считаются активными
    """
    conn = get_turso_connection()
    if not conn:
        return []
    
    try:
        def _execute():
            cursor = conn.execute("""
                SELECT DISTINCT user_id FROM user_filters
            """)
            return [row[0] for row in cursor.fetchall()]
        
        return await asyncio.to_thread(_execute)
    except Exception as e:
        logger.error(f"Ошибка получения активных пользователей: {e}")
        return []
    finally:
        if conn:
            conn.close()


def _listing_to_ad_data(listing: Listing) -> dict:
    """
    Конвертирует объект Listing в словарь для сохранения в apartments
    """
    return {
        "price_usd": listing.price_usd if listing.price_usd else (listing.price if listing.currency == "USD" else 0),
        "price_byn": listing.price_byn if listing.price_byn else (listing.price if listing.currency == "BYN" else 0),
        "rooms": listing.rooms,
        "floor": listing.floor,
        "total_area": listing.area,
        "url": listing.url,
        "address": listing.address,
        "title": listing.title,
        "description": listing.description,
        "photos": listing.photos,
        "currency": listing.currency,
        "year_built": listing.year_built,
        "is_company": listing.is_company,
        "balcony": listing.balcony,
        "bathroom": listing.bathroom,
        "total_floors": listing.total_floors,
        "house_type": listing.house_type,
        "renovation_state": listing.renovation_state,
        "kitchen_area": listing.kitchen_area,
        "living_area": listing.living_area,
        "list_time": listing.created_at  # Может быть timestamp или строка
    }


async def sync_ads_from_kufar(
    listings: List[Listing],
    raw_api_responses: List[dict]
) -> int:
    """
    Умная синхронизация объявлений из Kufar API
    
    Принимает список Listing объектов и соответствующие raw JSON ответы от API.
    Синхронизирует каждое объявление с базой данных используя sync_apartment_from_kufar.
    
    Args:
        listings: Список объектов Listing
        raw_api_responses: Список словарей с raw JSON данными от API (каждый словарь содержит поле "ads" с массивом объявлений)
    
    Returns:
        Количество успешно синхронизированных объявлений
    """
    if not listings:
        return 0
    
    # Создаем словарь raw_json по ad_id для быстрого поиска
    raw_json_map = {}
    for raw_response in raw_api_responses:
        ads = raw_response.get("ads", [])
        for ad in ads:
            ad_id = ad.get("ad_id")
            if ad_id:
                ad_id_str = f"kufar_{ad_id}"
                raw_json_map[ad_id_str] = json.dumps(ad)
    
    synced_count = 0
    
    for listing in listings:
        try:
            # Извлекаем ad_id из listing.id (формат: "kufar_1048044245")
            # Убеждаемся, что ad_id передается как строка
            ad_id = str(listing.id)
            if not ad_id.startswith("kufar_"):
                logger.warning(f"Пропускаю объявление с неверным форматом ID: {listing.id}")
                continue
            
            # Получаем raw_json для этого объявления
            raw_json = raw_json_map.get(ad_id, "{}")
            
            # Конвертируем Listing в словарь
            ad_data = _listing_to_ad_data(listing)
            
            # Синхронизируем объявление
            success = await sync_apartment_from_kufar(
                ad_id=ad_id,
                ad_data=ad_data,
                raw_json=raw_json,
                source="kufar"
            )
            
            if success:
                synced_count += 1
        except Exception as e:
            logger.error(f"Ошибка синхронизации объявления {listing.id}: {e}")
            continue
    
    logger.info(f"Синхронизировано {synced_count} из {len(listings)} объявлений из Kufar")
    return synced_count


async def sync_apartment_from_kufar(
    ad_id: str,
    ad_data: dict,
    raw_json: str,
    source: str = "kufar"
) -> bool:
    """
    Умная синхронизация объявления из Kufar API
    
    Логика:
    - Если ad_id нет в БД -> INSERT
    - Если ad_id есть, но list_time из API > list_time в БД -> UPDATE (цена/описание изменились)
    - В любом случае обновляем last_checked = NOW()
    - Если last_checked не обновлялся > 48 часов -> is_active = 0
    
    Args:
        ad_id: ID объявления из Kufar (например "kufar_1048044245")
        ad_data: Распарсенные данные объявления (словарь)
        raw_json: Полный JSON ответ от API
        source: Источник объявления (по умолчанию "kufar")
    """
    try:
        def _execute():
            with turso_transaction() as conn:
                # Извлекаем list_time из ad_data или raw_json
                list_time = None
                if ad_data.get("list_time"):
                    try:
                        list_time_val = ad_data["list_time"]
                        # Если это timestamp в миллисекундах
                        if len(str(list_time_val)) > 10:
                            timestamp = int(list_time_val) / 1000
                        else:
                            timestamp = int(list_time_val)
                        list_time = datetime.fromtimestamp(timestamp).isoformat()
                    except:
                        pass
                
                # Парсим raw_json если list_time не найден
                if not list_time:
                    try:
                        raw_data = json.loads(raw_json) if isinstance(raw_json, str) else raw_json
                        list_time_val = raw_data.get("list_time")
                        if list_time_val:
                            if len(str(list_time_val)) > 10:
                                timestamp = int(list_time_val) / 1000
                            else:
                                timestamp = int(list_time_val)
                            list_time = datetime.fromtimestamp(timestamp).isoformat()
                    except:
                        pass
                
                # Проверяем существующее объявление
                # Убеждаемся, что ad_id передается как строка
                ad_id_str = str(ad_id)
                cursor = conn.execute("""
                    SELECT list_time, last_checked, is_active 
                    FROM apartments 
                    WHERE ad_id = ?
                """, (ad_id_str,))
                existing = cursor.fetchone()
                
                current_time = datetime.now().isoformat()
                
                if existing:
                    existing_list_time = existing[0]
                    existing_last_checked = existing[1]
                    existing_is_active = existing[2]
                    
                    # Проверяем нужно ли обновление данных
                    should_update_data = False
                    if list_time and existing_list_time:
                        # Сравниваем timestamps
                        try:
                            existing_ts = datetime.fromisoformat(existing_list_time.replace("Z", "+00:00"))
                            new_ts = datetime.fromisoformat(list_time.replace("Z", "+00:00"))
                            if new_ts > existing_ts:
                                should_update_data = True
                        except:
                            # Если не удалось сравнить, обновляем на всякий случай
                            should_update_data = True
                    elif list_time and not existing_list_time:
                        should_update_data = True
                    
                    if should_update_data:
                        # Обновляем данные объявления
                        # Убеждаемся, что ad_id передается как строка
                        ad_id_str = str(ad_id)
                        address = ad_data.get("address", "")
                        
                        conn.execute("""
                            UPDATE apartments SET
                                price_usd = ?,
                                price_byn = ?,
                                rooms = ?,
                                floor = ?,
                                total_area = ?,
                                list_time = ?,
                                last_checked = ?,
                                is_active = 1,
                                url = ?,
                                address = ?,
                                raw_json = ?,
                                title = ?,
                                description = ?,
                                photos = ?,
                                currency = ?,
                                year_built = ?,
                                is_company = ?,
                                balcony = ?,
                                bathroom = ?,
                                total_floors = ?,
                                house_type = ?,
                                renovation_state = ?,
                                kitchen_area = ?,
                                living_area = ?,
                                updated_at = ?
                            WHERE ad_id = ?
                        """, (
                            ad_data.get("price_usd", 0),
                            ad_data.get("price_byn", 0),
                            ad_data.get("rooms", 0),
                            ad_data.get("floor", ""),
                            ad_data.get("total_area", 0.0),
                            list_time or current_time,
                            current_time,
                            ad_data.get("url", ""),
                            address,
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
                            ad_id_str
                        ))
                        
                        # Логируем успешное обновление
                        logger.info(f"[DB] updated apartment ad_id={ad_id_str} address={address}")
                    else:
                        # Обновляем только last_checked
                        # Убеждаемся, что ad_id передается как строка
                        ad_id_str = str(ad_id)
                        conn.execute("""
                            UPDATE apartments 
                            SET last_checked = ?, updated_at = ?
                            WHERE ad_id = ?
                        """, (current_time, current_time, ad_id_str))
                else:
                    # Вставляем новое объявление (INSERT OR IGNORE для предотвращения дублей благодаря уникальному индексу)
                    # Убеждаемся, что ad_id передается как строка
                    ad_id_str = str(ad_id)
                    address = ad_data.get("address", "")
                    
                    conn.execute("""
                    INSERT OR IGNORE INTO apartments (
                        ad_id, source, price_usd, price_byn, rooms, floor, total_area,
                        list_time, last_checked, is_active, url, address, raw_json,
                        title, description, photos, currency, year_built, is_company,
                        balcony, bathroom, total_floors, house_type, renovation_state,
                        kitchen_area, living_area, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    ad_id_str,
                    source,
                    ad_data.get("price_usd", 0),
                    ad_data.get("price_byn", 0),
                    ad_data.get("rooms", 0),
                    ad_data.get("floor", ""),
                    ad_data.get("total_area", 0.0),
                    list_time or current_time,
                    current_time,
                    1,  # is_active = 1 для новых объявлений
                    ad_data.get("url", ""),
                    address,
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
                    current_time
                ))
                    
                    # Логируем успешное сохранение
                    logger.info(f"[DB] saved apartment ad_id={ad_id_str} address={address}")
            
                # Помечаем объявления как неактивные, если last_checked старше 48 часов
                conn.execute("""
                    UPDATE apartments 
                    SET is_active = 0 
                    WHERE last_checked < datetime('now', '-48 hours')
                    AND is_active = 1
                """)
                # Commit происходит автоматически при выходе из контекста
        
        await asyncio.to_thread(_execute)
        return True
    except Exception as e:
        logger.error(f"Ошибка синхронизации объявления {ad_id}: {e}")
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
    """
    Строит динамический SQL запрос для поиска квартир
    
    Условия добавляются только если значения не пустые/не None
    """
    conn = get_turso_connection()
    if not conn:
        return []
    
    try:
        def _execute():
            # Строим WHERE условия динамически
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
                # Используем IN для списка комнат
                placeholders = ",".join(["?"] * len(rooms))
                conditions.append(f"rooms IN ({placeholders})")
                params.extend(rooms)
            
            if region:
                conditions.append("address LIKE ?")
                params.append(f"%{region}%")
            
            if source:
                conditions.append("source = ?")
                params.append(source)
            
            # Формируем SQL запрос
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
            
            # Конвертируем Row в словари
            columns = [desc[0] for desc in cursor.description]
            results = []
            for row in rows:
                result = dict(zip(columns, row))
                # Конвертируем photos из JSON строки в список
                if result.get("photos"):
                    try:
                        result["photos"] = json.loads(result["photos"]) if isinstance(result["photos"], str) else result["photos"]
                    except:
                        result["photos"] = []
                else:
                    result["photos"] = []
                # Конвертируем INTEGER в bool
                result["is_active"] = bool(result.get("is_active", 1))
                result["is_company"] = bool(result.get("is_company", 0)) if result.get("is_company") is not None else None
                results.append(result)
            
            return results
        
        return await asyncio.to_thread(_execute)
    except Exception as e:
        logger.error(f"Ошибка динамического запроса: {e}")
        return []
    finally:
        if conn:
            conn.close()


async def check_api_query_cache(
    query_hash: str,
    cache_minutes: int = 10
) -> Optional[Dict[str, Any]]:
    """
    Проверяет кэш API запросов
    
    Если запрос был сделан менее cache_minutes минут назад, возвращает данные из кэша
    Иначе возвращает None
    """
    conn = get_turso_connection()
    if not conn:
        return None
    
    try:
        def _execute():
            cursor = conn.execute("""
                SELECT last_fetched, result_count, query_params
                FROM api_query_cache
                WHERE query_hash = ?
                AND last_fetched > datetime('now', '-' || ? || ' minutes')
            """, (query_hash, cache_minutes))
            
            row = cursor.fetchone()
            if row:
                return {
                    "last_fetched": row[0],
                    "result_count": row[1],
                    "query_params": row[2]
                }
            return None
        
        return await asyncio.to_thread(_execute)
    except Exception as e:
        logger.error(f"Ошибка проверки кэша запросов: {e}")
        return None
    finally:
        if conn:
            conn.close()


async def save_api_query_cache(
    query_hash: str,
    result_count: int,
    query_params: str
) -> bool:
    """
    Сохраняет результат API запроса в кэш (атомарная операция)
    """
    try:
        def _execute():
            with turso_transaction() as conn:
                conn.execute("""
                    INSERT INTO api_query_cache (query_hash, last_fetched, result_count, query_params)
                    VALUES (?, CURRENT_TIMESTAMP, ?, ?)
                    ON CONFLICT(query_hash) DO UPDATE SET
                        last_fetched = CURRENT_TIMESTAMP,
                        result_count = excluded.result_count,
                        query_params = excluded.query_params
                """, (query_hash, result_count, query_params))
                # Commit происходит автоматически при выходе из контекста
        
        await asyncio.to_thread(_execute)
        return True
    except Exception as e:
        log_error("turso_cache", f"Ошибка сохранения кэша запросов {query_hash}", e)
        return False
