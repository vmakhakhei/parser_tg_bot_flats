"""
Тестовый скрипт для проверки группировки объявлений
"""
import asyncio
import sys
import os
import json

# Добавляем родительскую директорию в path для импорта
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scrapers.base import Listing
from scrapers.aggregator import group_similar_listings, _extract_coords_from_listing, _extract_city_from_listing, make_group_key
from utils.address_utils import split_address
from database_turso import get_turso_connection


async def test_address_parsing():
    """Тестирует парсинг адресов"""
    print("=" * 60)
    print("ТЕСТ 1: Парсинг адресов")
    print("=" * 60)
    
    test_addresses = [
        "Барановичи, ул. Николы Теслы, 33",
        "г. Барановичи, улица Николы Теслы, 33а",
        "Барановичи, пр-т Советский, 33/1",
        "ул. Ленина, 33 корпус 1",
        "Барановичи, ул. Советская, 33-а",
        "Барановичи, ул. Мира",
    ]
    
    for addr in test_addresses:
        result = split_address(addr)
        print(f"\nАдрес: {addr}")
        print(f"  Улица: '{result['street']}'")
        print(f"  Дом: '{result['house']}'")


async def test_grouping_with_sql():
    """Тестирует группировку на реальных данных из БД"""
    print("\n" + "=" * 60)
    print("ТЕСТ 2: Группировка объявлений из БД")
    print("=" * 60)
    
    conn = get_turso_connection()
    if not conn:
        print("❌ Не удалось подключиться к БД")
        return
    
    try:
        # Запрос объявлений по адресу "Николы Теслы"
        cursor = conn.execute("""
            SELECT ad_id, title, price_usd, rooms, address, raw_json, created_at
            FROM apartments
            WHERE address LIKE '%Николы Теслы%' OR address LIKE '%Николы теслы%'
            ORDER BY created_at DESC
            LIMIT 100
        """)
        
        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        
        print(f"\nНайдено объявлений: {len(rows)}")
        
        if not rows:
            print("⚠️  Объявления не найдены")
            return
        
        # Конвертируем в Listing объекты
        listings = []
        for row in rows:
            row_dict = dict(zip(columns, row))
            
            # Парсим raw_json если есть
            raw_json = row_dict.get("raw_json")
            if raw_json and isinstance(raw_json, str):
                try:
                    raw_json = json.loads(raw_json)
                except:
                    raw_json = None
            
            # Создаем Listing
            listing = Listing(
                id=row_dict.get("ad_id", ""),
                source="kufar",  # Предполагаем kufar
                title=row_dict.get("title", ""),
                price=row_dict.get("price_usd", 0),
                price_formatted=f"${row_dict.get('price_usd', 0):,}".replace(",", " "),
                rooms=row_dict.get("rooms", 0),
                area=0.0,  # Не критично для теста
                address=row_dict.get("address", ""),
                url="",
                photos=[],
            )
            
            # Добавляем raw_json и city
            if raw_json:
                listing.raw_json = raw_json
            
            # Извлекаем город из адреса
            from database_turso import _extract_city_from_address
            listing.city = _extract_city_from_address(listing.address)
            
            listings.append(listing)
            
            # Выводим информацию об объявлении
            print(f"\n📋 ad_id: {listing.id}")
            print(f"   Адрес: {listing.address}")
            addr_split = split_address(listing.address)
            print(f"   Улица: '{addr_split['street']}'")
            print(f"   Дом: '{addr_split['house']}'")
            print(f"   Комнат: {listing.rooms}")
            lat, lon = _extract_coords_from_listing(listing)
            if lat and lon:
                print(f"   Координаты: {lat}, {lon}")
            else:
                print(f"   Координаты: не найдены")
            print(f"   Город: {_extract_city_from_listing(listing)}")
            print(f"   Ключ группировки: {make_group_key(listing)}")
        
        # Группируем
        print("\n" + "=" * 60)
        print("РЕЗУЛЬТАТЫ ГРУППИРОВКИ")
        print("=" * 60)
        
        groups = group_similar_listings(listings)
        
        print(f"\nВсего групп: {len(groups)}")
        
        for i, group in enumerate(groups, 1):
            print(f"\nГруппа {i} ({len(group)} объявлений):")
            for listing in group:
                addr_split = split_address(listing.address)
                print(f"  - {listing.id}: {listing.address} (дом: '{addr_split['house']}', комнат: {listing.rooms})")
            
            # Показываем пример группированного сообщения
            if len(group) > 1:
                print(f"\n  Пример группированного сообщения:")
                prices = [l.price_usd for l in group if l.price_usd]
                if prices:
                    min_price = min(prices)
                    max_price = max(prices)
                    print(f"  🏢 {len(group)} квартир в одном доме")
                    print(f"  📍 {group[0].address}")
                    print(f"  🛏 {group[0].rooms} комнат(ы)")
                    print(f"  💰 ${min_price:,} – ${max_price:,}".replace(",", " "))
        
    finally:
        conn.close()


async def main():
    """Главная функция"""
    print("\n🧪 ТЕСТИРОВАНИЕ ГРУППИРОВКИ ОБЪЯВЛЕНИЙ\n")
    
    # Тест 1: Парсинг адресов
    await test_address_parsing()
    
    # Тест 2: Группировка на реальных данных
    await test_grouping_with_sql()
    
    print("\n" + "=" * 60)
    print("✅ ТЕСТИРОВАНИЕ ЗАВЕРШЕНО")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
