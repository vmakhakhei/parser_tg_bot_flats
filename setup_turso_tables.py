"""
Скрипт для создания таблиц в Turso Database
Запустите этот скрипт один раз для инициализации таблицы кэша объявлений
"""
import asyncio
import sys
from libsql import create_client
from config import TURSO_DB_URL, TURSO_AUTH_TOKEN, USE_TURSO_CACHE


async def create_tables():
    """Создает таблицу cached_listings в Turso"""
    
    if not USE_TURSO_CACHE:
        print("⚠️ USE_TURSO_CACHE отключен в конфигурации")
        return
    
    if not TURSO_DB_URL or not TURSO_AUTH_TOKEN:
        print("❌ Ошибка: TURSO_DB_URL и TURSO_AUTH_TOKEN должны быть установлены в .env")
        print("Добавьте в .env:")
        print("TURSO_DB_URL=libsql://your-db-name.turso.io")
        print("TURSO_AUTH_TOKEN=your-token-here")
        sys.exit(1)
    
    print("=" * 60)
    print("Создание таблиц в Turso Database")
    print("=" * 60)
    print(f"URL: {TURSO_DB_URL[:50]}...")
    
    try:
        client = create_client(
            url=TURSO_DB_URL,
            auth_token=TURSO_AUTH_TOKEN
        )
        
        print("\n✅ Подключение к Turso установлено")
        
        # Создаем таблицу кэшированных объявлений
        print("\n📋 Создание таблицы cached_listings...")
        await client.execute("""
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
                
                -- Статус и время
                status TEXT DEFAULT 'active',
                last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        print("✅ Таблица cached_listings создана")
        
        # Создаем индексы для быстрого поиска
        print("\n📋 Создание индексов...")
        
        await client.execute("""
            CREATE INDEX IF NOT EXISTS idx_city_rooms_price 
            ON cached_listings(city, rooms, price)
        """)
        print("✅ Индекс idx_city_rooms_price создан")
        
        await client.execute("""
            CREATE INDEX IF NOT EXISTS idx_content_hash 
            ON cached_listings(content_hash)
        """)
        print("✅ Индекс idx_content_hash создан")
        
        await client.execute("""
            CREATE INDEX IF NOT EXISTS idx_status_last_seen 
            ON cached_listings(status, last_seen_at)
        """)
        print("✅ Индекс idx_status_last_seen создан")
        
        await client.execute("""
            CREATE INDEX IF NOT EXISTS idx_source_url 
            ON cached_listings(source, url)
        """)
        print("✅ Индекс idx_source_url создан")
        
        # Проверяем созданные таблицы
        print("\n📋 Проверка созданных таблиц...")
        result = await client.execute("""
            SELECT name FROM sqlite_master 
            WHERE type='table' AND name NOT LIKE 'sqlite_%'
        """)
        
        print("\n✅ Созданные таблицы:")
        for row in result.rows:
            print(f"   - {row[0]}")
        
        # Проверяем индексы
        result = await client.execute("""
            SELECT name FROM sqlite_master 
            WHERE type='index' AND name NOT LIKE 'sqlite_%'
        """)
        
        print("\n✅ Созданные индексы:")
        for row in result.rows:
            print(f"   - {row[0]}")
        
        await client.close()
        
        print("\n" + "=" * 60)
        print("✅ Инициализация Turso завершена успешно!")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n❌ Ошибка при создании таблиц: {e}")
        print("\nПроверьте:")
        print("1. Правильность TURSO_DB_URL и TURSO_AUTH_TOKEN")
        print("2. Доступность интернета")
        print("3. Что токен имеет права на чтение и запись")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(create_tables())
