"""
Утилиты для агрегации и дедупликации объявлений
"""
import hashlib
import json
import logging
from typing import List, Optional

from scrapers.base import Listing

logger = logging.getLogger(__name__)


def normalize_price(price: Optional[float]) -> Optional[int]:
    """
    Нормализует цену для сравнения.
    Округляет до 500–1000 для "примерного" сравнения.
    
    Args:
        price: Цена в USD
        
    Returns:
        Нормализованная цена или None
    """
    if price is None:
        return None
    # округляем до 500–1000 для "примерного" сравнения
    return round(price / 500) * 500


def photos_signature(photos: Optional[List[str]]) -> Optional[str]:
    """
    Создает сигнатуру из первых 3 фото для сравнения.
    
    Args:
        photos: Список URL фото
        
    Returns:
        MD5 хеш первых 3 фото или None
    """
    if not photos:
        return None
    # берем первые 3 пути/урла и хешируем их как строку
    sample = '|'.join(photos[:3])
    return hashlib.md5(sample.encode('utf-8')).hexdigest()


def build_listing_signature(listing: Listing) -> str:
    """
    Возвращает строковый signature для определения почти-дубликатов:
    (normalized_address, vendor, normalized_price, floor, total_area, photos_sig)
    
    Args:
        listing: Объявление для создания сигнатуры
        
    Returns:
        MD5 хеш сигнатуры объявления
    """
    # извлечение vendor/agency
    vendor = None
    try:
        raw_json = getattr(listing, 'raw_json', None)
        if raw_json:
            if isinstance(raw_json, dict):
                vendor = raw_json.get('agency') or raw_json.get('seller')
            elif isinstance(raw_json, str):
                try:
                    raw_data = json.loads(raw_json)
                    vendor = raw_data.get('agency') or raw_data.get('seller')
                except:
                    pass
    except Exception:
        vendor = None

    addr = listing.address or ''
    price = normalize_price(listing.price_usd)
    floor = getattr(listing, 'floor', None)
    area = getattr(listing, 'total_area', None) or getattr(listing, 'area', None)
    photos = None
    try:
        raw_json = getattr(listing, 'raw_json', None)
        if raw_json:
            if isinstance(raw_json, dict):
                photos = raw_json.get('photos', [])
            elif isinstance(raw_json, str):
                try:
                    raw_data = json.loads(raw_json)
                    photos = raw_data.get('photos', [])
                except:
                    pass
    except Exception:
        photos = None

    photos_sig = photos_signature(photos)
    key = f"{addr}|{vendor or 'V?'}|{price}|{floor or 'F?'}|{int(area) if area else 'A?'}|{photos_sig or 'P?'}"
    return hashlib.md5(key.encode('utf-8')).hexdigest()


def dedupe_by_signature(listings: List[Listing]) -> List[Listing]:
    """
    Удаляет дубликаты объявлений по сигнатуре.
    
    Args:
        listings: Список объявлений для дедупликации
        
    Returns:
        Список уникальных объявлений
    """
    seen = {}
    result = []
    for l in listings:
        sig = build_listing_signature(l)
        if sig in seen:
            # логируем для диагностики
            logger.debug(f"[dedupe] duplicate signature: {l.id} same_as {seen[sig]}")
            continue
        seen[sig] = l.id
        result.append(l)
    
    if len(result) < len(listings):
        logger.info(f"[dedupe] удалено {len(listings) - len(result)} дубликатов из {len(listings)} объявлений")
    
    return result
