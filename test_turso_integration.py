"""
Тестовый скрипт для проверки интеграции Turso
"""
import asyncio
import sys
from config import TURSO_DB_URL, TURSO_AUTH_TOKEN, USE_TURSO_CACHE
from database_turso import (
    get_turso_client,
    get_cached_listings_by_filters,
    cache_listing,
    is_listing_cached
)
from scrapers.base import Listing


async def test_connection():
    """Тест подключения к Turso"""
    print("=" * 60)
    print("Тест 1: Подключение к Turso")
    print("=" * 60)
    
    if not USE_TURSO_CACHE:
        print("❌ USE_TURSO_CACHE отключен в конфигурации")
        return False
    
    if not TURSO_DB_URL or not TURSO_AUTH_TOKEN:
        print("❌ TURSO_DB_URL или TURSO_AUTH_TOKEN не установлены")
        print("Добавьте в .env:")
        print("TURSO_DB_URL=libsql://your-db-name.turso.io")
        print("TURSO_AUTH_TOKEN=your-token-here")
        return False
    
    client = get_turso_client()
    if not client:
        print("❌ Не удалось создать клиент Turso")
        return False
    
    try:
        result = await client.execute("SELECT 1")
        print("✅ Подключение к Turso успешно!")
        print(f"   URL: {TURSO_DB_URL[:50]}...")
        await client.close()
        return True
    except Exception as e:
        print(f"❌ Ошибка подключения: {e}")
        return False


async def test_table_exists():
    """Тест наличия таблицы cached_listings"""
    print("\n" + "=" * 60)
    print("Тест 2: Проверка таблицы cached_listings")
    print("=" * 60)
    
    client = get_turso_client()
    if not client:
        print("❌ Не удалось создать клиент Turso")
        return False
    
    try:
        result = await client.execute("""
            SELECT name FROM sqlite_master 
            WHERE type='table' AND name='cached_listings'
        """)
        
        if result.rows:
            print("✅ Таблица cached_listings существует")
            await client.close()
            return True
        else:
            print("❌ Таблица cached_listings не найдена")
            print("💡 Запустите: python setup_turso_tables.py")
            await client.close()
            return False
    except Exception as e:
        print(f"❌ Ошибка проверки таблицы: {e}")
        return False


async def test_cache_operations():
    """Тест операций с кэшем"""
    print("\n" + "=" * 60)
    print("Тест 3: Операции с кэшем")
    print("=" * 60)
    
    # Создаем тестовое объявление
    test_listing = Listing(
        id="test_12345",
        source="test",
        title="Тестовая квартира",
        price=50000,
        price_formatted="$50,000",
        rooms=2,
        area=45.5,
        address="г. Барановичи, ул. Тестовая, 1",
        url="https://test.com/12345",
        photos=[],
        currency="USD",
        price_usd=50000
    )
    
    # Тест сохранения в кэш
    print("\n3.1. Сохранение объявления в кэш...")
    saved = await cache_listing(test_listing)
    if saved:
        print("✅ Объявление сохранено в кэш")
    else:
        print("❌ Ошибка сохранения в кэш")
        return False
    
    # Тест проверки наличия
    print("\n3.2. Проверка наличия объявления в кэше...")
    exists = await is_listing_cached("test_12345")
    if exists:
        print("✅ Объявление найдено в кэше")
    else:
        print("❌ Объявление не найдено в кэше")
        return False
    
    # Тест поиска по фильтрам
    print("\n3.3. Поиск объявлений по фильтрам...")
    cached = await get_cached_listings_by_filters(
        city="барановичи",
        min_rooms=1,
        max_rooms=3,
        min_price=0,
        max_price=100000,
        limit=10
    )
    
    if cached:
        print(f"✅ Найдено {len(cached)} объявлений в кэше")
        if any(l.get("id") == "test_12345" for l in cached):
            print("✅ Тестовое объявление найдено в результатах поиска")
        else:
            print("⚠️ Тестовое объявление не найдено в результатах (возможно, фильтры не совпадают)")
    else:
        print("⚠️ В кэше нет объявлений (это нормально для первого запуска)")
    
    return True


async def main():
    """Основная функция тестирования"""
    print("\n🧪 Тестирование интеграции Turso Database\n")
    
    results = []
    
    # Тест 1: Подключение
    results.append(await test_connection())
    
    # Тест 2: Таблица
    if results[0]:  # Только если подключение успешно
        results.append(await test_table_exists())
    
    # Тест 3: Операции
    if all(results):  # Только если предыдущие тесты успешны
        results.append(await test_cache_operations())
    
    # Итоги
    print("\n" + "=" * 60)
    print("ИТОГИ ТЕСТИРОВАНИЯ")
    print("=" * 60)
    
    if all(results):
        print("✅ Все тесты пройдены успешно!")
        print("\n💡 Интеграция Turso готова к использованию")
        print("💡 При следующей проверке объявлений будет использоваться кэш")
    else:
        print("❌ Некоторые тесты не пройдены")
        print("\n💡 Проверьте:")
        print("   1. Правильность TURSO_DB_URL и TURSO_AUTH_TOKEN в .env")
        print("   2. Что таблицы созданы (запустите: python setup_turso_tables.py)")
        print("   3. Доступность интернета")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
