#!/usr/bin/env python3
"""
Stale records check (READ-ONLY)
Проверяет наличие stale записей в sent_ads без соответствующих apartments
НЕ УДАЛЯЕТ данные - только читает и выводит статистику
"""
import sys
from pathlib import Path

# Добавляем корневую директорию в путь
sys.path.insert(0, str(Path(__file__).parent.parent))


def check_stale_records():
    """Проверяет stale записи (read-only)"""
    print("[STALE] Checking for stale sent_ads records...")
    
    try:
        from database_turso import get_turso_connection
        
        conn = get_turso_connection()
        if not conn:
            print("[STALE][ERROR] Could not connect to database")
            return False
        
        # Выполняем read-only запрос
        try:
            cursor = conn.execute("""
                SELECT COUNT(*) AS stale
                FROM sent_ads sa
                LEFT JOIN apartments a ON sa.ad_external_id = a.ad_id
                WHERE a.ad_id IS NULL
            """)
            
            stale_count = cursor.fetchone()[0]
            
            print(f"[STALE] count = {stale_count}")
            
            if stale_count > 0:
                print(f"[STALE][WARNING] Found {stale_count} stale records")
                print("[STALE][INFO] These are orphaned sent_ads records without corresponding apartments")
                print("[STALE][INFO] Use /admin_cleanup_stale command to clean them up")
                # Это WARNING, не ERROR - не блокируем деплой
            else:
                print("[STALE][OK] No stale records found")
            
            conn.close()
            return True
            
        except Exception as e:
            print(f"[STALE][ERROR] Query failed: {e}")
            conn.close()
            return False
        
    except ImportError as e:
        print(f"[STALE][ERROR] Could not import database_turso: {e}")
        return False
    except Exception as e:
        print(f"[STALE][ERROR] Stale check failed: {e}")
        return False


def main():
    """Основная функция"""
    try:
        success = check_stale_records()
        # Всегда возвращаем успех для stale check (это только информационная проверка)
        if success:
            print("[STALE][OK] Stale check completed")
            sys.exit(0)
        else:
            print("[STALE][ERROR] Stale check failed")
            sys.exit(1)
    except Exception as e:
        print(f"[STALE][ERROR] Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
