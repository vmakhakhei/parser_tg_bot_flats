"""
from utils.address_utils import split_address

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
    Округляет до 500 для "примерного" сравнения.
    
    Args:
        price: Цена в USD
        
    Returns:
        Нормализованная цена или None
    """
    if price is None or price <= 0:
        return None
    # округляем до 500 для "примерного" сравнения
    return int(round(price / 500) * 500)


def normalize_area(area: Optional[float]) -> Optional[float]:
    """
    Нормализует площадь для сравнения.
    Округляет до 0.5 м² для "примерного" сравнения.
    
    Args:
        area: Площадь в м²
        
    Returns:
        Нормализованная площадь или None
    """
    if area is None or area <= 0:
        return None
    # округляем до 0.5 м²
    return round(area * 2) / 2


def photos_signature(photos: Optional[List[str]], limit: int = 3) -> Optional[str]:
    """
    Создает сигнатуру из первых N фото для сравнения.
    
    Args:
        photos: Список URL фото
        limit: Количество фото для включения в сигнатуру (по умолчанию 3)
    
    Returns:
        MD5 хеш первых N фото или None
    """
    if not photos:
        return None
    # берем первые N пути/урла и хешируем их как строку
    sample = '|'.join(photos[:limit])
    return hashlib.md5(sample.encode('utf-8')).hexdigest()


def build_listing_signature(listing: Listing) -> str:
    """
    Возвращает строковый signature для определения почти-дубликатов:
    (normalized_address, vendor, normalized_price, normalized_area, floor, photos_sig)
    
    Усиленная версия с:
    - vendor (если есть)
    - округлённая price_usd (до 500)
    - округлённая area (до 0.5 м²)
    - первые 3 photo hashes или paths (если нет фото — используем title+area+floor)
    
    Args:
        listing: Объявление для создания сигнатуры
        
    Returns:
        SHA1 хеш сигнатуры объявления
    """
    # Извлечение vendor/agency
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
    
    vendor_str = (vendor or "").strip().lower()
    
    # Нормализация цены (округление до 500)
    price_bucket = normalize_price(listing.price_usd) or 0
    
    # Нормализация площади (округление до 0.5 м²)
    area = getattr(listing, 'total_area', None) or getattr(listing, 'area', None)
    area_bucket = normalize_area(area) or 0.0
    
    # Этаж
    floor = getattr(listing, 'floor', None) or ''
    
    # Фото-сигнатура
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
    
    # Если нет фото, используем title+area+floor как fallback
    photos_sig = photos_signature(photos, limit=3)
    if not photos_sig:
        fallback = f"{listing.title or ''}|{area_bucket}|{floor}"
        photos_sig = hashlib.md5(fallback.encode('utf-8')).hexdigest()
    
    # Адрес
    addr = listing.address or ''
    
    # Формируем ключ
    key = f"{vendor_str}|{price_bucket}|{area_bucket}|{floor}|{photos_sig}"
    
    # Используем SHA1 для более надежного хеширования
    return hashlib.sha1(key.encode('utf-8')).hexdigest()


def dedupe_by_signature(listings: List[Listing]) -> List[Listing]:
    """
    Удаляет дубликаты объявлений по сигнатуре.
    
    Два объявления считаются дубликатами если:
    1. signature совпадает ИЛИ
    2. совпадает vendor+house+abs(price diff) < 5% + abs(area diff) < 1.0
    
    Args:
        listings: Список объявлений для дедупликации
        
    Returns:
        Список уникальных объявлений
    """
    seen = {}
    result = []
    removed_count = 0
    
    for l in listings:
        sig = build_listing_signature(l)
        
        # Проверка 1: точное совпадение signature
        if sig in seen:
            removed_count += 1
            logger.debug(f"[dedupe] duplicate signature: {l.id} same_as {seen[sig]}")
            continue
        
        # Проверка 2: дополнительная проверка по vendor+house+цена+площадь
        is_duplicate = False
        vendor = None
        try:
            raw_json = getattr(l, 'raw_json', None)
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
            pass
        
        # Извлекаем номер дома из адреса
        house = None
        try:
            addr = split_address(l.address or "")
            house = addr.get("house")
        except:
            pass
        
        # Если есть vendor и house, проверяем похожие объявления
        if vendor and house:
            price_usd = l.price_usd or 0
            area = getattr(l, 'total_area', None) or getattr(l, 'area', None) or 0.0
            
            for existing_sig, existing_id in seen.items():
                # Находим существующее объявление
                existing_l = next((x for x in listings if x.id == existing_id), None)
                if not existing_l:
                    continue
                
                existing_vendor = None
                try:
                    existing_raw_json = getattr(existing_l, 'raw_json', None)
                    if existing_raw_json:
                        if isinstance(existing_raw_json, dict):
                            existing_vendor = existing_raw_json.get('agency') or existing_raw_json.get('seller')
                        elif isinstance(existing_raw_json, str):
                            try:
                                existing_raw_data = json.loads(existing_raw_json)
                                existing_vendor = existing_raw_data.get('agency') or existing_raw_data.get('seller')
                            except:
                                pass
                except Exception:
                    pass
                
                if existing_vendor and existing_vendor.lower() == vendor.lower():
                    existing_house = None
                    try:
                        existing_addr = split_address(existing_l.address or "")
                        existing_house = existing_addr.get("house")
                    except:
                        pass
                    
                    if existing_house and existing_house == house:
                        existing_price = existing_l.price_usd or 0
                        existing_area = getattr(existing_l, 'total_area', None) or getattr(existing_l, 'area', None) or 0.0
                        
                        # Проверяем разницу в цене (< 5%)
                        if price_usd > 0 and existing_price > 0:
                            price_diff_pct = abs(price_usd - existing_price) / max(price_usd, existing_price)
                            if price_diff_pct < 0.05:  # 5%
                                # Проверяем разницу в площади (< 1.0 м²)
                                area_diff = abs(area - existing_area)
                                if area_diff < 1.0:
                                    is_duplicate = True
                                    removed_count += 1
                                    logger.info(
                                        f"[dedupe] duplicate by vendor+house+price+area: {l.id} same_as {existing_id} "
                                        f"(vendor={vendor}, house={house}, price_diff={price_diff_pct:.2%}, area_diff={area_diff:.1f})"
                                    )
                                    break
        
        if is_duplicate:
            continue
        
        seen[sig] = l.id
        result.append(l)
    
    if removed_count > 0:
        logger.info(f"[dedupe] удалено {removed_count} дубликатов из {len(listings)} объявлений")
    
    return result
