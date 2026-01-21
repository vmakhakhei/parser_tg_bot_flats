#!/usr/bin/env python3
"""
CLI утилита для lookup города в Kufar API
Использование: python tools/kufar_city_lookup.py "Полоцк"
"""
import sys
import os
import json
import asyncio

# Добавляем путь к проекту
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scrapers.kufar import lookup_kufar_location
from database_turso import get_kufar_city_cache, set_kufar_city_cache
from constants.constants import LOG_KUFAR_LOOKUP


async def main():
    if len(sys.argv) < 2:
        print("Использование: python tools/kufar_city_lookup.py <город>")
        print("Пример: python tools/kufar_city_lookup.py Полоцк")
        sys.exit(1)
    
    city_name = sys.argv[1].strip()
    city_norm = city_name.lower().strip()
    
    print(f"{LOG_KUFAR_LOOKUP} CLI lookup для города: {city_name}")
    print("-" * 60)
    
    # Проверяем кэш
    print("\n1. Проверка кэша...")
    cached = await get_kufar_city_cache(city_norm)
    if cached:
        print(f"✅ Найдено в кэше:")
        print(json.dumps(cached, indent=2, ensure_ascii=False))
        print("\nКэш-статус: HIT")
        return
    
    print("❌ Не найдено в кэше")
    print("\n2. Выполнение lookup через API...")
    
    # Выполняем lookup
    result = lookup_kufar_location(city_name)
    
    if result:
        print(f"\n✅ Результат lookup:")
        print(json.dumps(result, indent=2, ensure_ascii=False))
        
        # Сохраняем в кэш
        print("\n3. Сохранение в кэш...")
        try:
            success = await set_kufar_city_cache(city_norm, result)
            if success:
                print("✅ Сохранено в кэш")
            else:
                print("❌ Не удалось сохранить в кэш")
        except Exception as e:
            print(f"❌ Ошибка сохранения в кэш: {e}")
        
        print("\nКэш-статус: MISS → SAVED")
    else:
        print(f"\n❌ Результат lookup: пусто")
        print("API Kufar не даёт подсказки для этого города.")
        print("\nКэш-статус: MISS → NOT_FOUND")


if __name__ == "__main__":
    asyncio.run(main())
