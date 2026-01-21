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
from scrapers.utils.id_utils import normalize_ad_id, normalize_telegram_id

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


def migrate_users_schema(conn):
    """
    Миграция таблицы users:
    user_id -> telegram_id (PRIMARY KEY)
    
    Args:
        conn: Соединение с базой данных (синхронное)
    """
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        if "telegram_id" in cols and "user_id" not in cols:
            return  # уже новая схема
        
        logger.warning("[migration] Начинаю миграцию users → новая схема")
        
        # backup: переименовываем старую таблицу
        conn.execute("ALTER TABLE users RENAME TO users_old")
        logger.info("[migration] Старая таблица users переименована в users_old")
        
        # create new table
        conn.execute("""
        CREATE TABLE users (
            telegram_id INTEGER PRIMARY KEY,
            username TEXT,
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """)
        logger.info("[migration] Новая таблица users создана")
        
        # migrate rows best-effort: user_id -> telegram_id
        try:
            # Проверяем наличие колонок в старой таблице
            cols = [c[1] for c in conn.execute("PRAGMA table_info(users_old)").fetchall()]
            
            has_is_active = "is_active" in cols
            has_username = "username" in cols
            
            select_cols = ["user_id"]
            if has_username:
                select_cols.append("username")
            if has_is_active:
                select_cols.append("is_active")
            
            rows = conn.execute(
                f"SELECT {', '.join(select_cols)} FROM users_old"
            ).fetchall()
            
            migrated_count = 0
            for row in rows:
                try:
                    # Распаковываем строку в зависимости от наличия колонок
                    # row - это кортеж, индексы зависят от порядка select_cols
                    telegram_id = row[0]  # user_id всегда первый
                    
                    # Определяем индексы для username и is_active
                    username_idx = 1 if has_username else None
                    is_active_idx = (2 if has_username else 1) if has_is_active else None
                    
                    username = row[username_idx] if username_idx is not None and len(row) > username_idx else None
                    is_active = row[is_active_idx] if is_active_idx is not None and len(row) > is_active_idx else 1
                    
                    conn.execute("""
                        INSERT OR IGNORE INTO users (telegram_id, username, is_active)
                        VALUES (?, ?, ?)
                    """, (telegram_id, username, is_active))
                    migrated_count += 1
                except Exception as e:
                    logger.warning(f"[migration] Пропущена проблемная строка user_id={row[0] if row else 'unknown'}: {e}")
                    pass
            
            logger.info(f"[migration] Перенесено {migrated_count} записей из users_old")
        except Exception as e:
            logger.error(f"[migration] Ошибка при переносе данных из users_old: {e}")
            # Откатываем изменения
            conn.execute("DROP TABLE IF EXISTS users")
            conn.execute("ALTER TABLE users_old RENAME TO users")
            raise
        
        # Удаляем старую таблицу
        conn.execute("DROP TABLE users_old")
        logger.info("[migration] users schema migrated successfully")
        
    except Exception as e:
        logger.error(f"[migration] Критическая ошибка миграции users: {e}")
        # Пытаемся восстановить старое состояние
        try:
            conn.execute("DROP TABLE IF EXISTS users")
            conn.execute("ALTER TABLE users_old RENAME TO users")
            logger.warning("[migration] Откат миграции users выполнен")
        except:
            pass
        raise


def migrate_sent_ads_schema(conn):
    """
    Миграция таблицы sent_ads:
    user_id (TEXT) -> telegram_id (INTEGER)
    
    Args:
        conn: Соединение с базой данных (синхронное)
    """
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(sent_ads)").fetchall()}
        if "telegram_id" in cols and "user_id" not in cols:
            return  # уже новая схема
        
        logger.warning("[migration] Начинаю миграцию sent_ads → новая схема")
        
        # backup: переименовываем старую таблицу
        conn.execute("ALTER TABLE sent_ads RENAME TO sent_ads_old")
        logger.info("[migration] Старая таблица sent_ads переименована в sent_ads_old")
        
        # create new table
        conn.execute("""
        CREATE TABLE sent_ads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            ad_external_id TEXT NOT NULL,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(telegram_id, ad_external_id)
        )
        """)
        conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_sent_user_ad 
        ON sent_ads(telegram_id, ad_external_id)
        """)
        logger.info("[migration] Новая таблица sent_ads создана")
        
        # migrate rows best-effort: user_id -> telegram_id
        try:
            rows = conn.execute("SELECT user_id, ad_external_id, sent_at FROM sent_ads_old").fetchall()
            migrated_count = 0
            for user_id, ad_external_id, sent_at in rows:
                try:
                    # Конвертируем user_id в int (если был TEXT)
                    telegram_id = int(user_id) if isinstance(user_id, str) else user_id
                    conn.execute("""
                        INSERT OR IGNORE INTO sent_ads (telegram_id, ad_external_id, sent_at)
                        VALUES (?, ?, ?)
                    """, (telegram_id, ad_external_id, sent_at))
                    migrated_count += 1
                except Exception as e:
                    logger.warning(f"[migration] Пропущена проблемная строка user_id={user_id}: {e}")
                    pass
            
            logger.info(f"[migration] Перенесено {migrated_count} записей из sent_ads_old")
        except Exception as e:
            logger.error(f"[migration] Ошибка при переносе данных из sent_ads_old: {e}")
            # Откатываем изменения
            conn.execute("DROP TABLE IF EXISTS sent_ads")
            conn.execute("ALTER TABLE sent_ads_old RENAME TO sent_ads")
            raise
        
        # Удаляем старую таблицу
        conn.execute("DROP TABLE sent_ads_old")
        logger.warning("[migration] Миграция sent_ads завершена успешно")
        
    except Exception as e:
        logger.error(f"[migration] Критическая ошибка миграции sent_ads: {e}")
        # Пытаемся восстановить старое состояние
        try:
            conn.execute("DROP TABLE IF EXISTS sent_ads")
            conn.execute("ALTER TABLE sent_ads_old RENAME TO sent_ads")
            logger.warning("[migration] Откат миграции sent_ads выполнен")
        except:
            pass
        raise


def migrate_user_filters_schema(conn):
    """
    Миграция user_filters:
    user_id / rooms(JSON) -> telegram_id / min_rooms / max_rooms
    
    Args:
        conn: Соединение с базой данных (синхронное)
    """
    try:
        # Проверяем структуру таблицы
        cur = conn.execute("PRAGMA table_info(user_filters)")
        columns_info = cur.fetchall()
        columns = {row[1]: row for row in columns_info}
        
        # Проверяем, является ли telegram_id PRIMARY KEY
        # В PRAGMA table_info: [0]=cid, [1]=name, [2]=type, [3]=notnull, [4]=dflt_value, [5]=pk
        has_telegram_id_pk = False
        for row in columns_info:
            if row[1] == "telegram_id" and row[5] == 1:  # row[5] = pk flag
                has_telegram_id_pk = True
                break
        
        # Если новая схема уже применена — выходим
        if has_telegram_id_pk and "min_rooms" in columns and "max_rooms" in columns:
            return
        
        logger.warning("[migration] Начинаю миграцию user_filters → новая схема")
        
        # Проверяем, есть ли старая таблица для миграции
        if "user_id" not in columns and "telegram_id" not in columns:
            # Таблица пустая или неожиданная структура - создаем заново
            logger.warning("[migration] Таблица user_filters имеет неожиданную структуру, пересоздаю")
            conn.execute("DROP TABLE IF EXISTS user_filters")
            conn.execute("""
                CREATE TABLE user_filters (
                    telegram_id INTEGER PRIMARY KEY,
                    city TEXT,
                    min_rooms INTEGER,
                    max_rooms INTEGER,
                    min_price INTEGER,
                    max_price INTEGER,
                    seller_type TEXT DEFAULT 'all',
                    delivery_mode TEXT DEFAULT 'brief',
                    is_active INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_user_filters_active 
                ON user_filters(is_active)
            """)
            logger.warning("[migration] Таблица user_filters пересоздана с новой схемой")
            return
        
        # 1. Переименовываем старую таблицу
        conn.execute("ALTER TABLE user_filters RENAME TO user_filters_old")
        logger.info("[migration] Старая таблица переименована в user_filters_old")
        
        # 2. Создаём новую таблицу
        conn.execute("""
            CREATE TABLE user_filters (
                telegram_id INTEGER PRIMARY KEY,
                city TEXT,
                min_rooms INTEGER,
                max_rooms INTEGER,
                min_price INTEGER,
                max_price INTEGER,
                seller_type TEXT DEFAULT 'all',
                delivery_mode TEXT DEFAULT 'brief',
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_user_filters_active 
            ON user_filters(is_active)
        """)
        logger.info("[migration] Новая таблица user_filters создана")
        
        # 3. Перенос данных (best-effort)
        try:
            # Определяем какие колонки есть в старой таблице
            cur = conn.execute("PRAGMA table_info(user_filters_old)")
            old_columns = {row[1]: row[0] for row in cur.fetchall()}
            
            # Формируем SELECT с учетом доступных колонок
            select_cols = []
            if "user_id" in old_columns:
                select_cols.append("user_id")
            elif "telegram_id" in old_columns:
                select_cols.append("telegram_id")
            else:
                raise ValueError("Не найдено поле user_id или telegram_id в старой таблице")
            
            # Добавляем остальные поля если они есть
            if "region" in old_columns:
                select_cols.append("region")
            elif "city" in old_columns:
                select_cols.append("city")
            else:
                select_cols.append("'барановичи' as city")
            
            if "rooms" in old_columns:
                select_cols.append("rooms")
            else:
                select_cols.append("NULL as rooms")
            
            if "min_price" in old_columns:
                select_cols.append("min_price")
            else:
                select_cols.append("0 as min_price")
            
            if "max_price" in old_columns:
                select_cols.append("max_price")
            else:
                select_cols.append("100000 as max_price")
            
            if "active" in old_columns:
                select_cols.append("active")
            elif "is_active" in old_columns:
                select_cols.append("is_active")
            else:
                select_cols.append("1 as active")
            
            select_query = f"SELECT {', '.join(select_cols)} FROM user_filters_old"
            cur = conn.execute(select_query)
            
            migrated_count = 0
            for row in cur.fetchall():
                # Распаковываем строку в зависимости от количества колонок
                telegram_id = row[0]  # user_id из старой схемы становится telegram_id
                city = row[1] if len(row) > 1 else "барановичи"
                rooms_json = row[2] if len(row) > 2 else None
                min_price = row[3] if len(row) > 3 else 0
                max_price = row[4] if len(row) > 4 else 100000
                is_active = row[5] if len(row) > 5 else 1
                
                min_rooms = 1
                max_rooms = 4
                
                if rooms_json:
                    try:
                        rooms = json.loads(rooms_json)
                        if isinstance(rooms, list) and rooms:
                            min_rooms = min(rooms)
                            max_rooms = max(rooms)
                    except Exception as e:
                        logger.warning(f"[migration] Не удалось распарсить rooms для telegram_id={telegram_id}: {e}")
                
                # Нормализуем city
                if city is None:
                    city = "барановичи"
                
                conn.execute("""
                    INSERT OR IGNORE INTO user_filters (
                        telegram_id, city, min_rooms, max_rooms,
                        min_price, max_price, is_active
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    telegram_id, city, min_rooms, max_rooms,
                    min_price, max_price, is_active
                ))
                migrated_count += 1
            
            logger.info(f"[migration] Перенесено {migrated_count} записей")
            
        except Exception as e:
            logger.error(f"[migration] Ошибка при переносе данных: {e}")
            # Откатываем изменения - переименовываем обратно
            conn.execute("DROP TABLE IF EXISTS user_filters")
            conn.execute("ALTER TABLE user_filters_old RENAME TO user_filters")
            raise
        
        # 4. Удаляем старую таблицу
        conn.execute("DROP TABLE user_filters_old")
        logger.warning("[migration] Миграция user_filters завершена успешно")
        
    except Exception as e:
        logger.error(f"[migration] Критическая ошибка миграции user_filters: {e}")
        # Пытаемся восстановить старое состояние
        try:
            conn.execute("DROP TABLE IF EXISTS user_filters")
            conn.execute("ALTER TABLE user_filters_old RENAME TO user_filters")
            logger.warning("[migration] Откат миграции выполнен")
        except:
            pass
        raise


def assert_no_legacy_user_id_columns(conn):
    """
    Проверяет, что в таблицах нет колонок user_id (только telegram_id)
    Вызывается после миграции для проверки корректности схемы (fail-fast)
    
    Args:
        conn: Соединение с базой данных (синхронное)
    """
    for table in ("users", "user_filters", "sent_ads"):
        try:
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
            if "user_id" in cols:
                raise RuntimeError(f"[SCHEMA ERROR] table {table} still has column user_id")
        except RuntimeError:
            raise
        except Exception as e:
            logger.warning(f"[schema] Не удалось проверить таблицу {table}: {e}")


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
                            telegram_id INTEGER PRIMARY KEY,
                            username TEXT,
                            is_active INTEGER DEFAULT 1,
                            created_at TEXT DEFAULT CURRENT_TIMESTAMP
                        )
                    """)
                    logger.info("✅ Таблица users создана")
                else:
                    # Проверяем схему и мигрируем при необходимости
                    try:
                        migrate_users_schema(conn)
                    except Exception as e:
                        logger.critical("[migration] USERS MIGRATION FAILED", exc_info=e)
                        raise
                
                # 2. Таблица user_filters (исправленная структура)
                cursor = conn.execute("""
                    SELECT name FROM sqlite_master 
                    WHERE type='table' AND name='user_filters'
                """)
                if not cursor.fetchone():
                    logger.info("📋 Создание таблицы user_filters...")
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS user_filters (
                            telegram_id INTEGER PRIMARY KEY,
                            city TEXT DEFAULT 'барановичи',
                            min_rooms INTEGER DEFAULT 1,
                            max_rooms INTEGER DEFAULT 4,
                            min_price INTEGER DEFAULT 0,
                            max_price INTEGER DEFAULT 100000,
                            seller_type TEXT DEFAULT 'all',
                            delivery_mode TEXT DEFAULT 'brief',
                            is_active INTEGER DEFAULT 1,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )
                    """)
                    conn.execute("""
                        CREATE INDEX IF NOT EXISTS idx_user_filters_active 
                        ON user_filters(is_active)
                    """)
                    logger.info("✅ Таблица user_filters создана")
                else:
                    # Проверяем схему и мигрируем при необходимости
                    migrate_user_filters_schema(conn)
                    
                    # Добавляем seller_type и delivery_mode если их нет
                    try:
                        cols = {r[1] for r in conn.execute("PRAGMA table_info(user_filters)").fetchall()}
                        if "seller_type" not in cols:
                            conn.execute("ALTER TABLE user_filters ADD COLUMN seller_type TEXT DEFAULT 'all'")
                            logger.info("[migration] Добавлена колонка seller_type в user_filters")
                        if "delivery_mode" not in cols:
                            conn.execute("ALTER TABLE user_filters ADD COLUMN delivery_mode TEXT DEFAULT 'brief'")
                            logger.info("[migration] Добавлена колонка delivery_mode в user_filters")
                        if "city_json" not in cols:
                            conn.execute("ALTER TABLE user_filters ADD COLUMN city_json TEXT NULL")
                            logger.info("[migration] Добавлена колонка city_json в user_filters")
                    except Exception as e:
                        logger.warning(f"[migration] Ошибка добавления колонок seller_type/delivery_mode/city_json: {e}")
                
                # 2.1. Таблица locations_cache (для кэширования локаций)
                cursor = conn.execute("""
                    SELECT name FROM sqlite_master 
                    WHERE type='table' AND name='locations_cache'
                """)
                if not cursor.fetchone():
                    logger.info("📋 Создание таблицы locations_cache...")
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS locations_cache (
                            id TEXT PRIMARY KEY,
                            name TEXT,
                            region TEXT,
                            type TEXT,
                            slug TEXT,
                            lat REAL,
                            lng REAL,
                            raw_json TEXT,
                            fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )
                    """)
                    conn.execute("""
                        CREATE INDEX IF NOT EXISTS idx_locations_cache_fetched_at 
                        ON locations_cache(fetched_at)
                    """)
                    logger.info("✅ Таблица locations_cache создана")
                
                # 2.2. Таблица kufar_city_cache (для кэширования lookup городов Kufar)
                cursor = conn.execute("""
                    SELECT name FROM sqlite_master 
                    WHERE type='table' AND name='kufar_city_cache'
                """)
                if not cursor.fetchone():
                    logger.info("📋 Создание таблицы kufar_city_cache...")
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS kufar_city_cache (
                            city_normalized TEXT PRIMARY KEY,
                            payload TEXT,
                            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )
                    """)
                    conn.execute("""
                        CREATE INDEX IF NOT EXISTS idx_kufar_city_cache_updated_at 
                        ON kufar_city_cache(updated_at)
                    """)
                    logger.info("✅ Таблица kufar_city_cache создана")
                
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
                
                # 6. Таблица sent_ads (для отслеживания отправленных объявлений пользователям)
                cursor = conn.execute("""
                    SELECT name FROM sqlite_master 
                    WHERE type='table' AND name='sent_ads'
                """)
                if not cursor.fetchone():
                    logger.info("📋 Создание таблицы sent_ads...")
                    conn.execute("""
                        CREATE TABLE IF NOT EXISTS sent_ads (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            telegram_id INTEGER NOT NULL,
                            ad_external_id TEXT NOT NULL,
                            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            UNIQUE(telegram_id, ad_external_id)
                        )
                    """)
                    conn.execute("""
                        CREATE UNIQUE INDEX IF NOT EXISTS idx_sent_user_ad 
                        ON sent_ads(telegram_id, ad_external_id)
                    """)
                    logger.info("✅ Таблица sent_ads создана")
                else:
                    # Проверяем схему и мигрируем при необходимости
                    migrate_sent_ads_schema(conn)
                
                # Проверяем, что все миграции прошли успешно (fail-fast)
                assert_no_legacy_user_id_columns(conn)
                
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
    telegram_id: int,
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
                    INSERT INTO users (telegram_id, username)
                    VALUES (?, ?)
                    ON CONFLICT(telegram_id) DO UPDATE SET
                        username = COALESCE(excluded.username, username)
                """, (telegram_id, username))
                # Commit происходит автоматически
        
        await asyncio.to_thread(_execute)
        return True
    except Exception as e:
        log_error("turso_users", f"Ошибка создания/обновления пользователя {telegram_id}", e)
        return False


async def get_user_filters_turso(telegram_id: int) -> Optional[Dict[str, Any]]:
    """
    Получает фильтры пользователя из Turso
    Использует telegram_id как PRIMARY KEY
    Возвращает словарь с фильтрами или None
    """
    conn = get_turso_connection()
    if not conn:
        return None
    
    try:
        def _execute():
            with turso_transaction() as conn:
                query = """
                SELECT telegram_id, city, city_json, min_rooms, max_rooms, min_price, max_price,
                       seller_type, delivery_mode, is_active
                FROM user_filters
                WHERE telegram_id = ?
                LIMIT 1
                """
                cursor = conn.execute(query, (telegram_id,))
                row = cursor.fetchone()
                
                if not row:
                    logger.critical(f"[FILTER_LOAD] telegram_id={telegram_id} NOT_FOUND")
                    return None
                
                # Конвертируем Row в словарь
                columns = [desc[0] for desc in cursor.description]
                result = dict(zip(columns, row))
                
                # Конвертируем INTEGER в bool
                result["is_active"] = bool(result.get("is_active", 1))
                
                # Обрабатываем city_json: если есть - используем его, иначе city (обратная совместимость)
                city_data = result.get("city")
                city_json_str = result.get("city_json")
                
                if city_json_str:
                    try:
                        city_data = json.loads(city_json_str)
                    except Exception:
                        # Если не удалось распарсить, используем city как строку
                        pass
                
                logger.critical(f"[FILTER_LOAD] telegram_id={telegram_id} FOUND city_json={'yes' if city_json_str else 'no'}")
                
                return {
                    "telegram_id": result.get("telegram_id"),
                    "city": city_data,  # Может быть dict или str
                    "min_rooms": result.get("min_rooms"),
                    "max_rooms": result.get("max_rooms"),
                    "min_price": result.get("min_price"),
                    "max_price": result.get("max_price"),
                    "seller_type": result.get("seller_type"),
                    "delivery_mode": result.get("delivery_mode"),
                    "is_active": result.get("is_active"),
                }
        
        result = await asyncio.to_thread(_execute)
        
        return result
    except Exception as e:
        logger.error(f"Ошибка получения фильтров пользователя {telegram_id}: {e}")
        return None
    finally:
        if conn:
            conn.close()


def has_valid_user_filters(filters: dict | None) -> bool:
    """
    Проверяет валидность фильтров пользователя.
    
    Фильтры считаются валидными, если:
    - filters не None
    - есть city (не пустая строка)
    - min_rooms и max_rooms заданы (не None)
    
    Args:
        filters: Словарь с фильтрами пользователя или None
        
    Returns:
        True если фильтры валидны, False иначе
    """
    if not filters:
        return False
    
    # Проверяем обязательные поля
    city = filters.get("city")
    min_rooms = filters.get("min_rooms")
    max_rooms = filters.get("max_rooms")
    
    # city должен быть не None и не пустой строкой
    if not city or not isinstance(city, str) or not city.strip():
        return False
    
    # min_rooms и max_rooms должны быть заданы (не None)
    if min_rooms is None or max_rooms is None:
        return False
    
    return True


async def activate_user(telegram_id: int, is_active: bool = True) -> bool:
    """
    Активирует пользователя (алиас для upsert_user с is_active=True).
    Гарантирует, что пользователь будет активным.
    
    Args:
        telegram_id: ID пользователя в Telegram
        is_active: Активен ли пользователь (по умолчанию True)
    
    Returns:
        True если успешно, False при ошибке
    """
    return await upsert_user(telegram_id, username=None, is_active=is_active)


async def upsert_user(
    telegram_id: int,
    username: Optional[str] = None,
    is_active: bool = True
) -> bool:
    """
    Создаёт пользователя, если его нет.
    Если пользователь есть — обновляет is_active и username.
    Работает без ON CONFLICT (совместимо с SQLite/Turso).
    
    Args:
        telegram_id: ID пользователя в Telegram
        username: Имя пользователя (опционально)
        is_active: Активен ли пользователь (по умолчанию True)
    
    Returns:
        True если успешно, False при ошибке
    """
    try:
        def _execute():
            with turso_transaction() as conn:
                # 1. Проверяем, существует ли пользователь в таблице users
                cur = conn.execute(
                    "SELECT telegram_id FROM users WHERE telegram_id = ?",
                    (telegram_id,),
                )
                row = cur.fetchone()
                
                if row:
                    # 2. Обновляем существующего пользователя в users
                    conn.execute(
                        """
                        UPDATE users
                        SET username = COALESCE(?, username), 
                            is_active = ?
                        WHERE telegram_id = ?
                        """,
                        (username, 1 if is_active else 0, telegram_id),
                    )
                else:
                    # 3. Создаём нового пользователя в users
                    conn.execute(
                        """
                        INSERT INTO users (telegram_id, username, is_active)
                        VALUES (?, ?, ?)
                        """,
                        (telegram_id, username, 1 if is_active else 0),
                    )
                
                # 4. Проверяем, существует ли запись в user_filters
                cur = conn.execute(
                    "SELECT telegram_id FROM user_filters WHERE telegram_id = ?",
                    (telegram_id,),
                )
                row = cur.fetchone()
                
                if row:
                    # 5. Обновляем существующую запись в user_filters
                    conn.execute(
                        """
                        UPDATE user_filters
                        SET is_active = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE telegram_id = ?
                        """,
                        (1 if is_active else 0, telegram_id),
                    )
                else:
                    # 6. Создаём новую запись в user_filters с дефолтными значениями
                    conn.execute(
                        """
                        INSERT INTO user_filters 
                        (telegram_id, city, min_rooms, max_rooms, min_price, max_price, is_active, updated_at)
                        VALUES (?, 'барановичи', 1, 4, 0, 100000, ?, CURRENT_TIMESTAMP)
                        """,
                        (telegram_id, 1 if is_active else 0),
                    )
                # Commit происходит автоматически
        
        await asyncio.to_thread(_execute)
        return True
    except Exception as e:
        log_error("turso_users", f"Ошибка создания/обновления пользователя {telegram_id}", e)
        return False


async def set_user_filters_turso(telegram_id: int, filters: Dict[str, Any]) -> None:
    """
    Устанавливает фильтры пользователя в Turso (атомарная операция)
    Использует telegram_id как PRIMARY KEY для ON CONFLICT
    
    Args:
        telegram_id: ID пользователя в Telegram
        filters: Словарь с фильтрами (city может быть dict или str, city_json для location dict)
    """
    # Обрабатываем city: может быть dict (location) или str (старый формат)
    city_value = filters.get("city")
    city_json_value = None
    
    if isinstance(city_value, dict):
        # Новый формат - location dict
        city_json_value = json.dumps(city_value)
        city_value = city_value.get("name", "")  # Сохраняем имя для обратной совместимости
    elif city_value:
        # Старый формат - строка
        city_value = str(city_value)
    
    # Также проверяем city_json напрямую (если передан отдельно)
    if "city_json" in filters and filters["city_json"]:
        if isinstance(filters["city_json"], dict):
            city_json_value = json.dumps(filters["city_json"])
        else:
            city_json_value = filters["city_json"]
    
    city_id = None
    city_name = city_value
    if isinstance(filters.get("city"), dict):
        city_id = filters["city"].get("id")
        city_name = filters["city"].get("name", city_value)
    
    from constants.constants import LOG_FILTER_SAVE
    
    logger.info(
        f"{LOG_FILTER_SAVE} user={telegram_id} city={city_name!r} city_id={city_id} rooms={filters.get('min_rooms')}-{filters.get('max_rooms')} price={filters.get('min_price')}-{filters.get('max_price')} seller={filters.get('seller_type')} mode={filters.get('delivery_mode', 'brief')}"
    )
    
    try:
        def _execute():
            with turso_transaction() as conn:
                conn.execute(
                    """
                    INSERT INTO user_filters (
                        telegram_id, city, city_json, min_rooms, max_rooms,
                        min_price, max_price, seller_type,
                        delivery_mode, is_active
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    ON CONFLICT(telegram_id) DO UPDATE SET
                        city=excluded.city,
                        city_json=excluded.city_json,
                        min_rooms=excluded.min_rooms,
                        max_rooms=excluded.max_rooms,
                        min_price=excluded.min_price,
                        max_price=excluded.max_price,
                        seller_type=excluded.seller_type,
                        delivery_mode=excluded.delivery_mode,
                        is_active=excluded.is_active,
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    (
                        telegram_id,
                        city_value,
                        city_json_value,
                        filters.get("min_rooms", 0),
                        filters.get("max_rooms", 99),
                        filters.get("min_price", 0),
                        filters.get("max_price", 99999999),
                        filters.get("seller_type", "all"),
                        filters.get("delivery_mode", "brief"),
                    ),
                )
        
        await asyncio.to_thread(_execute)
    except Exception as e:
        log_error("turso_filters", f"Ошибка установки фильтров пользователя {telegram_id}", e)
        raise


async def ensure_user_filters(telegram_id: int) -> None:
    """
    Гарантирует наличие фильтров у пользователя.
    Создает дефолтные фильтры, если их нет.
    
    Args:
        telegram_id: ID пользователя в Telegram
    """
    existing = await get_user_filters_turso(telegram_id)
    if existing is None:
        await set_user_filters_turso(
            telegram_id,
            {
                "city": None,
                "min_rooms": 1,
                "max_rooms": 4,
                "min_price": 0,
                "max_price": 100000,
                "seller_type": "all",
                "delivery_mode": "brief",
            },
        )
        logger.info(f"[FILTER_INIT] default filters created for {telegram_id}")


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


async def get_kufar_city_cache(city_normalized: str) -> Optional[Dict[str, Any]]:
    """
    Получает кэш lookup города для Kufar.
    
    Args:
        city_normalized: Нормализованное название города (lowercase)
    
    Returns:
        Словарь с payload или None
    """
    conn = get_turso_connection()
    if not conn:
        return None
    
    try:
        def _execute():
            with turso_transaction() as conn:
                cursor = conn.execute(
                    "SELECT payload FROM kufar_city_cache WHERE city_normalized = ?",
                    (city_normalized.lower().strip(),)
                )
                row = cursor.fetchone()
                if row:
                    import json
                    return json.loads(row[0])
                return None
        
        return await asyncio.to_thread(_execute)
    except Exception as e:
        logger.error(f"Ошибка получения кэша Kufar для города {city_normalized}: {e}")
        return None
    finally:
        if conn:
            conn.close()


async def set_kufar_city_cache(city_normalized: str, payload: Dict[str, Any]) -> bool:
    """
    Сохраняет кэш lookup города для Kufar.
    
    Args:
        city_normalized: Нормализованное название города (lowercase)
        payload: Данные для кэширования
    
    Returns:
        True если успешно сохранено
    """
    conn = get_turso_connection()
    if not conn:
        return False
    
    try:
        def _execute():
            with turso_transaction() as conn:
                import json
                conn.execute(
                    """
                    INSERT OR REPLACE INTO kufar_city_cache 
                    (city_normalized, payload, updated_at)
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                    """,
                    (city_normalized.lower().strip(), json.dumps(payload))
                )
                return True
        
        await asyncio.to_thread(_execute)
        return True
    except Exception as e:
        logger.error(f"Ошибка сохранения кэша Kufar для города {city_normalized}: {e}")
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
                SELECT DISTINCT telegram_id FROM user_filters
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


async def sync_apartments_batch(listings: List[Listing]) -> List[str]:
    """
    Сохраняет список объявлений.
    Возвращает список ad_id, которые были реально вставлены.
    """
    if not listings:
        logger.info("[DB][BATCH] пустой список, сохранять нечего")
        return []

    def _execute():
        inserted_ids = []
        with turso_transaction() as conn:
            for listing in listings:
                try:
                    # --- Подготовка данных ---
                    ad_id = str(listing.id)
                    source = listing.source
                    title = listing.title or ""
                    address = listing.address or ""
                    price_usd = listing.price_usd if listing.price_usd is not None else None
                    url = listing.url

                    # --- Batch INSERT ---
                    cur = conn.execute(
                        """
                        INSERT OR IGNORE INTO apartments (
                            ad_id, source, title, address, price_usd, url,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                        """,
                        (
                            ad_id,
                            source,
                            title,
                            address,
                            price_usd,
                            url,
                        ),
                    )

                    if cur.rowcount == 1:
                        inserted_ids.append(ad_id)

                except Exception as e:
                    ad_id_str = str(listing.id) if listing else "unknown"
                    logger.error(
                        f"[DB][BATCH] пропущено ad_id={ad_id_str}: {e}"
                    )
                    # продолжаем batch, не падаем
        
        logger.info(f"[DB][BATCH] вставлено {len(inserted_ids)} из {len(listings)}")
        return inserted_ids
    
    try:
        inserted_ids = await asyncio.to_thread(_execute)
        return inserted_ids
    except Exception as e:
        logger.error(f"[DB][BATCH] Ошибка батчевого сохранения объявлений: {e}")
        return []


async def sync_apartment_from_listing(listing: Listing, raw_json: str = "{}") -> bool:
    """
    Универсальная функция для сохранения любого Listing в таблицу apartments
    
    Args:
        listing: Объект Listing для сохранения
        raw_json: Опциональный raw JSON (по умолчанию пустой объект)
    
    Returns:
        True если успешно сохранено, False при ошибке
    """
    try:
        # Определяем source из listing
        source = listing.source.lower() if listing.source else "unknown"
        
        # Конвертируем Listing в словарь
        ad_data = _listing_to_ad_data(listing)
        
        # Используем listing.id как ad_id
        ad_id = str(listing.id)
        
        # Вызываем существующую функцию сохранения
        return await sync_apartment_from_kufar(
            ad_id=ad_id,
            ad_data=ad_data,
            raw_json=raw_json,
            source=source
        )
    except Exception as e:
        logger.error(f"Ошибка сохранения объявления {listing.id} в apartments: {e}")
        return False


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
    Умная синхронизация объявления в таблицу apartments
    
    Логика:
    - Если ad_id нет в БД -> INSERT
    - Если ad_id есть, но list_time из API > list_time в БД -> UPDATE (цена/описание изменились)
    - В любом случае обновляем last_checked = NOW()
    - Если last_checked не обновлялся > 48 часов -> is_active = 0
    
    Args:
        ad_id: ID объявления (например "kufar_1048044245" или "realt_12345")
        ad_data: Распарсенные данные объявления (словарь)
        raw_json: Полный JSON ответ от API (может быть пустым для не-Kufar источников)
        source: Источник объявления ("kufar", "realt", "domovita", etc.)
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


async def is_ad_sent_to_user_turso(telegram_id: int, ad_external_id: str) -> bool:
    """
    Проверяет, было ли объявление уже отправлено пользователю (идемпотентная проверка для Turso)
    
    Args:
        telegram_id: ID пользователя в Telegram
        ad_external_id: Внешний ID объявления (listing.id)
    
    Returns:
        True если объявление уже было отправлено, False иначе
    """
    # Нормализуем входные параметры
    tg = normalize_telegram_id(telegram_id)
    ad = normalize_ad_id(ad_external_id)
    
    conn = get_turso_connection()
    if not conn:
        return False
    
    try:
        def _execute():
            with turso_transaction() as conn:
                # 1) Сначала проверить, есть ли такое объявление в apartments
                cursor = conn.execute(
                    "SELECT 1 FROM apartments WHERE ad_id = ? LIMIT 1",
                    (ad,)
                )
                row = cursor.fetchone()
                if not row:
                    # Объявление отсутствует в apartments — считаем, что оно НЕ отправлено;
                    # логируем для диагностики (не удаляем автоматически).
                    logger.warning(f"[sent_check][STALE] ad={ad} not found in apartments; treating as NOT sent for user={tg}")
                    return False
                
                # 2) Затем проверить sent_ads
                cursor = conn.execute(
                    "SELECT 1 FROM sent_ads WHERE telegram_id = ? AND ad_external_id = ? LIMIT 1",
                    (tg, ad)
                )
                return cursor.fetchone() is not None
        
        return await asyncio.to_thread(_execute)
    except Exception as e:
        logger.error(f"Ошибка проверки отправки объявления {ad} пользователю {tg}: {e}")
        return False
    finally:
        if conn:
            conn.close()


async def mark_ad_sent_to_user_turso(telegram_id: int, ad_external_id: str) -> bool:
    """
    Отмечает объявление как отправленное пользователю (идемпотентная запись для Turso)
    
    Args:
        telegram_id: ID пользователя в Telegram
        ad_external_id: Внешний ID объявления (listing.id)
    
    Returns:
        True если успешно, False при ошибке
    """
    # Нормализуем входные параметры
    tg = normalize_telegram_id(telegram_id)
    ad = normalize_ad_id(ad_external_id)
    
    try:
        def _execute():
            with turso_transaction() as conn:
                conn.execute("""
                    INSERT OR IGNORE INTO sent_ads (telegram_id, ad_external_id, sent_at)
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                """, (tg, ad))
                # Commit происходит автоматически при выходе из контекста
        
        await asyncio.to_thread(_execute)
        return True
    except Exception as e:
        logger.error(f"Ошибка отметки объявления {ad} как отправленного пользователю {tg}: {e}")
        return False


async def delete_sent_ads_for_user(telegram_id: int) -> int:
    """
    Безопасно удаляет все записи sent_ads для указанного пользователя.
    
    Args:
        telegram_id: ID пользователя в Telegram
        
    Returns:
        Количество оставшихся записей после удаления (0 если все удалены)
    """
    tg = normalize_telegram_id(telegram_id)
    
    try:
        def _execute():
            with turso_transaction() as conn:
                # Удаляем все записи для пользователя
                conn.execute("DELETE FROM sent_ads WHERE telegram_id = ?", (tg,))
                # Возвращаем количество оставшихся записей
                cursor = conn.execute(
                    "SELECT COUNT(*) as cnt FROM sent_ads WHERE telegram_id = ?",
                    (tg,)
                )
                result = cursor.fetchone()
                return result[0] if result else 0
        
        return await asyncio.to_thread(_execute)
    except Exception as e:
        logger.error(f"Ошибка удаления sent_ads для пользователя {tg}: {e}")
        return -1


async def find_stale_sent_ads() -> List[Dict[str, Any]]:
    """
    Находит записи в sent_ads, которые ссылаются на несуществующие объявления в apartments.
    
    Returns:
        Список словарей с информацией о стейл записях:
        [{"telegram_id": int, "ad_external_id": str, "sent_at": str}, ...]
    """
    conn = get_turso_connection()
    if not conn:
        return []
    
    try:
        def _execute():
            # Находим записи в sent_ads, которых нет в apartments
            cursor = conn.execute("""
                SELECT sa.telegram_id, sa.ad_external_id, sa.sent_at
                FROM sent_ads sa
                LEFT JOIN apartments a ON sa.ad_external_id = a.ad_id
                WHERE a.ad_id IS NULL
                ORDER BY sa.sent_at DESC
            """)
            rows = cursor.fetchall()
            return [
                {
                    "telegram_id": row[0],
                    "ad_external_id": row[1],
                    "sent_at": row[2]
                }
                for row in rows
            ]
        
        return await asyncio.to_thread(_execute)
    except Exception as e:
        logger.error(f"Ошибка поиска стейл записей sent_ads: {e}")
        return []
    finally:
        if conn:
            conn.close()


async def list_stale_sent_ads(limit: int = 100) -> List[tuple]:
    """
    Возвращает список стейл записей sent_ads (для админ-команды).
    
    Args:
        limit: Максимальное количество записей для возврата
        
    Returns:
        Список кортежей: [(ad_external_id, telegram_id, sent_at), ...]
    """
    conn = get_turso_connection()
    if not conn:
        return []
    
    try:
        def _execute():
            with turso_transaction() as conn:
                rows = conn.execute(
                    """
                    SELECT s.ad_external_id, s.telegram_id, s.sent_at
                    FROM sent_ads s
                    LEFT JOIN apartments a ON s.ad_external_id = a.ad_id
                    WHERE a.ad_id IS NULL
                    LIMIT ?
                    """,
                    (limit,)
                ).fetchall()
                return rows
        
        return await asyncio.to_thread(_execute)
    except Exception as e:
        logger.error(f"Ошибка получения списка стейл записей: {e}")
        return []
    finally:
        if conn:
            conn.close()


async def cleanup_stale_sent_ads(dry_run: bool = True) -> Dict[str, Any]:
    """
    Очищает стейл записи из sent_ads (записи, ссылающиеся на несуществующие apartments).
    
    Args:
        dry_run: Если True, только подсчитывает записи без удаления
        
    Returns:
        Словарь с результатами:
        {
            "total_stale": int,
            "deleted": int,
            "errors": int,
            "dry_run": bool
        }
    """
    stale_records = await find_stale_sent_ads()
    total_stale = len(stale_records)
    
    if total_stale == 0:
        return {
            "total_stale": 0,
            "deleted": 0,
            "errors": 0,
            "dry_run": dry_run
        }
    
    if dry_run:
        logger.info(f"[cleanup] Найдено {total_stale} стейл записей (dry_run=True, удаление не выполнено)")
        return {
            "total_stale": total_stale,
            "deleted": 0,
            "errors": 0,
            "dry_run": True
        }
    
    # Удаляем стейл записи
    deleted = 0
    errors = 0
    
    try:
        def _execute():
            with turso_transaction() as conn:
                # Удаляем все стейл записи одной транзакцией
                cursor = conn.execute("""
                    DELETE FROM sent_ads
                    WHERE ad_external_id NOT IN (
                        SELECT ad_id FROM apartments
                    )
                """)
                return cursor.rowcount
        
        deleted = await asyncio.to_thread(_execute)
        logger.info(f"[cleanup] Удалено {deleted} стейл записей из sent_ads")
    except Exception as e:
        logger.error(f"[cleanup] Ошибка удаления стейл записей: {e}")
        errors = 1
    
    return {
        "total_stale": total_stale,
        "deleted": deleted,
        "errors": errors,
        "dry_run": False
    }


async def check_sent_ads_sync() -> Dict[str, Any]:
    """
    Проверяет синхронизацию между sent_ads и apartments.
    
    Returns:
        Словарь с результатами проверки:
        {
            "total_sent_ads": int,
            "total_apartments": int,
            "stale_count": int,
            "sync_percent": float,
            "is_synced": bool
        }
    """
    conn = get_turso_connection()
    if not conn:
        return {
            "total_sent_ads": 0,
            "total_apartments": 0,
            "stale_count": 0,
            "sync_percent": 0.0,
            "is_synced": False,
            "error": "Turso connection unavailable"
        }
    
    try:
        def _execute():
            # Подсчитываем общее количество записей
            cursor = conn.execute("SELECT COUNT(*) FROM sent_ads")
            total_sent_ads = cursor.fetchone()[0]
            
            cursor = conn.execute("SELECT COUNT(*) FROM apartments")
            total_apartments = cursor.fetchone()[0]
            
            # Подсчитываем стейл записи
            cursor = conn.execute("""
                SELECT COUNT(*)
                FROM sent_ads sa
                LEFT JOIN apartments a ON sa.ad_external_id = a.ad_id
                WHERE a.ad_id IS NULL
            """)
            stale_count = cursor.fetchone()[0]
            
            return {
                "total_sent_ads": total_sent_ads,
                "total_apartments": total_apartments,
                "stale_count": stale_count,
                "sync_percent": (1.0 - stale_count / total_sent_ads * 100) if total_sent_ads > 0 else 100.0,
                "is_synced": stale_count == 0
            }
        
        result = await asyncio.to_thread(_execute)
        return result
    except Exception as e:
        logger.error(f"Ошибка проверки синхронизации sent_ads: {e}")
        return {
            "total_sent_ads": 0,
            "total_apartments": 0,
            "stale_count": 0,
            "sync_percent": 0.0,
            "is_synced": False,
            "error": str(e)
        }
    finally:
        if conn:
            conn.close()


