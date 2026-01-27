#!/usr/bin/env python3
"""
Database health check script
Проверяет состояние базы данных и выводит статистику
"""
import sys
import asyncio
from pathlib import Path

# Добавляем корневую директорию в путь
sys.path.insert(0, str(Path(__file__).parent.parent))


def check_database_health():
    """Проверяет здоровье базы данных"""
    print("[DB] Starting database health check...")
    
    try:
        from database_turso import get_turso_connection
        
        conn = get_turso_connection()
        if not conn:
            print("[DB][ERROR] Could not connect to database")
            return False
        
        # Проверяем наличие таблиц
        required_tables = ["apartments", "sent_ads", "city_codes", "user_filters"]
        table_status = {}
        
        for table in required_tables:
            try:
                cursor = conn.execute(f"SELECT COUNT(*) FROM {table}")
                count = cursor.fetchone()[0]
                table_status[table] = count
                print(f"[DB][OK] {table}: {count} records")
            except Exception as e:
                table_status[table] = None
                print(f"[DB][ERROR] {table}: check failed - {e}")
                conn.close()
                return False
        
        # Выводим сводку
        print("=" * 60)
        print("[DB] Database Health Summary:")
        print(f"  apartments: {table_status.get('apartments', 'N/A')}")
        print(f"  sent_ads: {table_status.get('sent_ads', 'N/A')}")
        print(f"  city_codes: {table_status.get('city_codes', 'N/A')}")
        print(f"  user_filters: {table_status.get('user_filters', 'N/A')}")
        print("=" * 60)
        
        # Проверяем, что все таблицы существуют и доступны
        all_ok = all(count is not None for count in table_status.values())
        
        if all_ok:
            print("[DB][OK] All tables accessible")
        else:
            print("[DB][ERROR] Some tables are not accessible")
        
        conn.close()
        return all_ok
        
    except ImportError as e:
        print(f"[DB][ERROR] Could not import database_turso: {e}")
        return False
    except Exception as e:
        print(f"[DB][ERROR] Database health check failed: {e}")
        return False


def main():
    """Основная функция"""
    try:
        success = check_database_health()
        if success:
            print("[DB][OK] Database health check completed successfully")
            sys.exit(0)
        else:
            print("[DB][ERROR] Database health check failed")
            sys.exit(1)
    except Exception as e:
        print(f"[DB][ERROR] Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
