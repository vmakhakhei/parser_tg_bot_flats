#!/usr/bin/env python3
"""
Скрипт для загрузки city_map из JSON в таблицу city_codes
"""
import sys
import os
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database_turso import load_city_map_from_json


async def main():
    if len(sys.argv) < 2:
        json_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'kufar_city_map.json')
    else:
        json_path = sys.argv[1]
    
    if not os.path.exists(json_path):
        print(f"❌ Файл не найден: {json_path}")
        sys.exit(1)
    
    print(f"Загрузка city_map из {json_path}...")
    imported = await load_city_map_from_json(json_path)
    
    if imported > 0:
        print(f"✅ Импортировано {imported} записей")
        sys.exit(0)
    else:
        print("❌ Не удалось импортировать записи")
        sys.exit(1)


if __name__ == '__main__':
    asyncio.run(main())
