"""
from error_logger import log_error, log_warning, log_info

Сервис для работы с локациями через Kufar autocomplete API
Включает кэширование, валидацию и fallback механизмы
"""
import json
import logging
import asyncio
import aiohttp
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from urllib.parse import quote

from config import (
    KUFAR_AUTOCOMPLETE_URL,
    LOCATION_SERVICE_TIMEOUT,
    ENABLE_OSM_FALLBACK,
    TURSO_DB_URL,
    TURSO_AUTH_TOKEN,
    USE_TURSO_CACHE
)
from constants.constants import LOC_CACHE_TTL_DAYS

logger = logging.getLogger(__name__)

# Импортируем error_logger
try:
except ImportError:
    def log_error(source, message, exception=None):
        logger.error(f"[{source}] {message}: {exception}")
    def log_warning(source, message):
        logger.warning(f"[{source}] {message}")
    def log_info(source, message):
        logger.info(f"[{source}] {message}")


def normalize_location(raw_location: Dict[str, Any]) -> Dict[str, Any]:
    """
    Нормализует сырой ответ от Kufar API в стандартный формат.
    
    Args:
        raw_location: Сырой объект локации из API
    
    Returns:
        Нормализованный словарь с полями: id, name, region, type, slug, lat, lng, raw
    """
    return {
        "id": str(raw_location.get("id", "")),
        "name": raw_location.get("name", ""),
        "region": raw_location.get("region", ""),
        "type": raw_location.get("type", ""),
        "slug": raw_location.get("slug", ""),
        "lat": raw_location.get("lat"),
        "lng": raw_location.get("lng"),
        "raw": raw_location  # Сохраняем полный объект для совместимости
    }


async def search_locations(query: str) -> List[Dict[str, Any]]:
    """
    Ищет локации через Kufar autocomplete API с кэшированием.
    
    Args:
        query: Поисковый запрос (название города)
    
    Returns:
        Список нормализованных локаций
    """
    query = query.strip()
    if not query:
        return []
    
    # Проверяем кэш
    cached = await _get_cached_location(query)
    if cached:
        log_info("location", f"[LOC_CACHE_HIT] q={query}")
        return cached
    
    log_info("location", f"[LOC_CACHE_MISS] q={query}")
    
    # Делаем запрос к API с retries
    url = f"{KUFAR_AUTOCOMPLETE_URL}?q={quote(query)}"
    
    for attempt in range(3):
        try:
            timeout = aiohttp.ClientTimeout(total=LOCATION_SERVICE_TIMEOUT)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        
                        # Парсим ответ (формат может варьироваться)
                        locations = []
                        if isinstance(data, list):
                            locations = data
                        elif isinstance(data, dict):
                            # Может быть обернут в объект
                            locations = data.get("locations", data.get("data", []))
                        
                        # Нормализуем локации
                        normalized = [normalize_location(loc) for loc in locations]
                        
                        # Сохраняем в кэш
                        await _save_location_to_cache(query, normalized)
                        
                        log_info("location", f"[LOC_SEARCH] q={query} ok={len(normalized)}")
                        return normalized
                    
                    elif response.status == 429:
                        # Rate limit - используем экспоненциальный backoff
                        wait_time = 2 ** attempt
                        log_warning("location", f"[LOC_SEARCH] Rate limit, waiting {wait_time}s")
                        await asyncio.sleep(wait_time)
                        continue
                    
                    else:
                        log_error("location", f"[LOC_SEARCH_ERR] q={query} status={response.status}")
                        # Пробуем fallback
                        if attempt == 2 and ENABLE_OSM_FALLBACK:
                            return await fallback_geocode(query)
                        return []
        
        except asyncio.TimeoutError:
            log_error("location", f"[LOC_SEARCH_ERR] q={query} err=timeout")
            if attempt == 2:
                # Последняя попытка - пробуем fallback
                if ENABLE_OSM_FALLBACK:
                    return await fallback_geocode(query)
                return []
            await asyncio.sleep(2 ** attempt)
        
        except Exception as e:
            log_error("location", f"[LOC_SEARCH_ERR] q={query} err={str(e)}", e)
            if attempt == 2:
                if ENABLE_OSM_FALLBACK:
                    return await fallback_geocode(query)
                return []
            await asyncio.sleep(2 ** attempt)
    
    return []


async def get_location_by_id(location_id: str) -> Optional[Dict[str, Any]]:
    """
    Получает локацию по ID, сначала проверяя кэш.
    
    Args:
        location_id: ID локации
    
    Returns:
        Нормализованная локация или None
    """
    import asyncio
    
    def _read_cache():
        conn = _get_turso_connection()
        if not conn:
            return None
        
        try:
            cursor = conn.execute(
                "SELECT raw_json FROM locations_cache WHERE id = ?",
                (location_id,)
            )
            row = cursor.fetchone()
            if row:
                raw_data = json.loads(row[0])
                log_info("location", f"[LOC_CACHE_HIT] id={location_id}")
                return normalize_location(raw_data)
        except Exception as e:
            log_error("location", f"Ошибка чтения кэша по ID: {e}")
        finally:
            conn.close()
        
        return None
    
    result = await asyncio.to_thread(_read_cache)
    
    if result:
        return result
    
    log_info("location", f"[LOC_CACHE_MISS] id={location_id}")
    
    # Если нет в кэше, пробуем найти через autocomplete
    # (Kufar может не иметь отдельного endpoint для получения по ID)
    # Пока возвращаем None, можно расширить логику позже
    return None


async def validate_city_input(user_input: str) -> Dict[str, Any]:
    """
    Валидирует ввод города пользователем и возвращает результат.
    
    Args:
        user_input: Введенный пользователем текст
    
    Returns:
        Словарь с полями:
        - status: "not_found" | "ok" | "multiple" | "too_many"
        - location: dict (если status == "ok")
        - auto: bool (если status == "ok" и автоматически выбран)
        - choices: list (если status == "multiple")
    """
    log_info("location", f"[LOC_VALIDATE] q={user_input}")
    
    locations = await search_locations(user_input)
    
    if not locations:
        log_info("location", f"[LOC_VALIDATE] q={user_input} status=not_found")
        return {"status": "not_found"}
    
    if len(locations) == 1:
        log_info("location", f"[LOC_VALIDATE] q={user_input} status=ok auto=True")
        return {
            "status": "ok",
            "location": locations[0],
            "auto": True
        }
    
    if 2 <= len(locations) <= 5:
        log_info("location", f"[LOC_VALIDATE] q={user_input} status=multiple count={len(locations)}")
        return {
            "status": "multiple",
            "choices": locations[:5]  # Максимум 5 вариантов
        }
    
    # Больше 5 результатов
    log_info("location", f"[LOC_VALIDATE] q={user_input} status=too_many count={len(locations)}")
    return {"status": "too_many"}


async def fallback_geocode(query: str) -> List[Dict[str, Any]]:
    """
    Fallback геокодирование через OpenStreetMap Nominatim.
    
    Args:
        query: Поисковый запрос
    
    Returns:
        Список локаций в нормализованном формате
    """
    if not ENABLE_OSM_FALLBACK:
        return []
    
    log_info("location", f"[LOC_FALLBACK_OSM] q={query}")
    
    try:
        url = f"https://nominatim.openstreetmap.org/search?q={quote(query)}&format=json&limit=5&countrycodes=by"
        timeout = aiohttp.ClientTimeout(total=5)
        
        async with aiohttp.ClientSession(timeout=timeout) as session:
            headers = {"User-Agent": "KeyFlat Bot/1.0"}
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    
                    # Конвертируем формат OSM в наш формат
                    locations = []
                    for item in data:
                        locations.append({
                            "id": f"osm_{item.get('osm_id', '')}",
                            "name": item.get("display_name", "").split(",")[0],
                            "region": item.get("address", {}).get("state", ""),
                            "type": item.get("type", ""),
                            "slug": "",
                            "lat": float(item.get("lat", 0)),
                            "lng": float(item.get("lon", 0)),
                            "raw": item
                        })
                    
                    return locations
    except Exception as e:
        log_error("location", f"[LOC_FALLBACK_OSM] q={query} err={str(e)}", e)
    
    return []


def _get_turso_connection():
    """Получает соединение с Turso (синхронное)"""
    if not USE_TURSO_CACHE or not TURSO_DB_URL or not TURSO_AUTH_TOKEN:
        return None
    
    try:
        import libsql
        return libsql.connect(TURSO_DB_URL, auth_token=TURSO_AUTH_TOKEN)
    except Exception as e:
        log_error("location", f"Ошибка подключения к Turso: {e}")
        return None


async def _get_cached_location(query: str) -> Optional[List[Dict[str, Any]]]:
    """Получает локации из кэша (асинхронно)"""
    import asyncio
    
    def _read_cache():
        conn = _get_turso_connection()
        if not conn:
            return None
        
        try:
            # Ищем по query (можно улучшить, добавив поле query в таблицу)
            # Пока ищем по name
            cutoff_date = datetime.now() - timedelta(days=LOC_CACHE_TTL_DAYS)
            
            cursor = conn.execute(
                """
                SELECT raw_json FROM locations_cache 
                WHERE name LIKE ? AND fetched_at > ?
                ORDER BY fetched_at DESC
                LIMIT 10
                """,
                (f"%{query}%", cutoff_date.isoformat())
            )
            
            rows = cursor.fetchall()
            if rows:
                locations = []
                for row in rows:
                    try:
                        raw_data = json.loads(row[0])
                        locations.append(normalize_location(raw_data))
                    except Exception:
                        continue
                
                if locations:
                    return locations
        except Exception as e:
            log_error("location", f"Ошибка чтения кэша: {e}")
        finally:
            conn.close()
        
        return None
    
    return await asyncio.to_thread(_read_cache)


async def _save_location_to_cache(query: str, locations: List[Dict[str, Any]]):
    """Сохраняет локации в кэш (асинхронно)"""
    import asyncio
    
    def _write_cache():
        conn = _get_turso_connection()
        if not conn:
            return
        
        try:
            for loc in locations:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO locations_cache 
                    (id, name, region, type, slug, lat, lng, raw_json, fetched_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        loc["id"],
                        loc["name"],
                        loc.get("region", ""),
                        loc.get("type", ""),
                        loc.get("slug", ""),
                        loc.get("lat"),
                        loc.get("lng"),
                        json.dumps(loc["raw"]),
                        datetime.now().isoformat()
                    )
                )
            conn.commit()
            log_info("location", f"[LOC_SAVE] q={query} saved={len(locations)}")
        except Exception as e:
            log_error("location", f"Ошибка сохранения в кэш: {e}")
        finally:
            conn.close()
    
    await asyncio.to_thread(_write_cache)
