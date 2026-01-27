#!/usr/bin/env python3
"""
from database_turso import get_turso_connection

Pre-deploy проверки для безопасного деплоя
Проверяет наличие критических данных и конфигурации перед деплоем
"""
import os
import sys
import json
import asyncio
from pathlib import Path

# Добавляем корневую директорию в путь
sys.path.insert(0, str(Path(__file__).parent.parent))

def check_env_vars():
    """Проверяет наличие критических переменных окружения"""
    print("[PREDEPLOY] Checking environment variables...")
    
    required_vars = {
        "BOT_TOKEN": os.getenv("BOT_TOKEN"),
        "TURSO_DB_URL": os.getenv("TURSO_DB_URL"),
        "TURSO_AUTH_TOKEN": os.getenv("TURSO_AUTH_TOKEN"),
    }
    
    missing = []
    for var_name, var_value in required_vars.items():
        if not var_value:
            missing.append(var_name)
            print(f"[PREDEPLOY][ERROR] Missing required env var: {var_name}")
        else:
            # Маскируем токены для безопасности
            if "TOKEN" in var_name or "AUTH" in var_name:
                masked = var_value[:8] + "..." if len(var_value) > 8 else "***"
                print(f"[PREDEPLOY][OK] {var_name} = {masked}")
            else:
                print(f"[PREDEPLOY][OK] {var_name} = {var_value[:50]}...")
    
    if missing:
        print(f"[PREDEPLOY][FAIL] Missing {len(missing)} required environment variables")
        return False
    
    print("[PREDEPLOY][OK] All required environment variables present")
    return True


def check_city_map_file():
    """Проверяет наличие файла карты городов"""
    print("[PREDEPLOY] Checking city map file...")
    
    city_map_path = Path(__file__).parent.parent / "data" / "kufar_city_map.json"
    
    if not city_map_path.exists():
        print(f"[PREDEPLOY][FAIL] City map file not found: {city_map_path}")
        return False
    
    try:
        with open(city_map_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        city_count = len(data) if isinstance(data, list) else len(data) if isinstance(data, dict) else 0
        
        if city_count < 300:
            print(f"[PREDEPLOY][FAIL] City map contains only {city_count} cities (minimum: 300)")
            return False
        
        print(f"[PREDEPLOY][OK] City map file exists with {city_count} cities")
        return True
        
    except Exception as e:
        print(f"[PREDEPLOY][FAIL] Error reading city map file: {e}")
        return False


async def check_database():
    """Проверяет состояние базы данных"""
    print("[PREDEPLOY] Checking database...")
    
    try:
        
        conn = get_turso_connection()
        if not conn:
            print("[PREDEPLOY][FAIL] Could not connect to database")
            return False
        
        # Проверяем наличие таблиц
        required_tables = ["apartments", "sent_ads", "city_codes", "user_filters"]
        missing_tables = []
        
        for table in required_tables:
            try:
                cursor = conn.execute(f"SELECT COUNT(*) FROM {table}")
                count = cursor.fetchone()[0]
                print(f"[PREDEPLOY][OK] Table '{table}' exists (count: {count})")
            except Exception as e:
                missing_tables.append(table)
                print(f"[PREDEPLOY][ERROR] Table '{table}' check failed: {e}")
        
        if missing_tables:
            print(f"[PREDEPLOY][FAIL] Missing tables: {missing_tables}")
            conn.close()
            return False
        
        # Проверяем city_codes
        try:
            cursor = conn.execute("SELECT COUNT(*) FROM city_codes")
            city_count = cursor.fetchone()[0]
            
            if city_count < 300:
                print(f"[PREDEPLOY][FAIL] city_codes table contains only {city_count} records (minimum: 300)")
                conn.close()
                return False
            
            print(f"[PREDEPLOY][OK] city_codes table has {city_count} records")
        except Exception as e:
            print(f"[PREDEPLOY][FAIL] Error checking city_codes: {e}")
            conn.close()
            return False
        
        # Проверяем user_filters (должна быть хотя бы одна запись или пустая таблица - это нормально)
        try:
            cursor = conn.execute("SELECT COUNT(*) FROM user_filters")
            filters_count = cursor.fetchone()[0]
            print(f"[PREDEPLOY][OK] user_filters table has {filters_count} records")
        except Exception as e:
            print(f"[PREDEPLOY][WARNING] Could not check user_filters: {e}")
            # Это не критично, продолжаем
        
        conn.close()
        print("[PREDEPLOY][OK] Database checks passed")
        return True
        
    except ImportError as e:
        print(f"[PREDEPLOY][FAIL] Could not import database_turso: {e}")
        return False
    except Exception as e:
        print(f"[PREDEPLOY][FAIL] Database check failed: {e}")
        return False


async def main():
    """Основная функция проверок"""
    print("=" * 60)
    print("[PREDEPLOY] Starting pre-deploy checks...")
    print("=" * 60)
    
    checks = [
        ("Environment Variables", check_env_vars()),
        ("City Map File", check_city_map_file()),
        ("Database", await check_database()),
    ]
    
    failed = []
    for check_name, result in checks:
        if not result:
            failed.append(check_name)
    
    print("=" * 60)
    if failed:
        print(f"[PREDEPLOY][FAIL] Pre-deploy checks failed: {', '.join(failed)}")
        sys.exit(1)
    else:
        print("[PREDEPLOY][OK] All pre-deploy checks passed")
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
