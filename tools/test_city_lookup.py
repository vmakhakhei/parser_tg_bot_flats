#!/usr/bin/env python3
"""
Тестовый скрипт для проверки city lookup
"""
import sys
import os
import asyncio

# Добавляем путь к проекту
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.utils.city_lookup import find_city_slug_by_text, normalize_query
from database_turso import ensure_city_codes_table, load_city_map_from_json
from pathlib import Path


async def main():
    """Главная функция тестирования"""
    print("=" * 80)
    print("City Lookup Test")
    print("=" * 80)
    
    # Убеждаемся, что таблица существует
    print("\n1. Проверка таблицы city_codes...")
    await ensure_city_codes_table()
    print("✅ Таблица проверена")
    
    # Загружаем данные если нужно
    repo_root = Path(__file__).parent.parent
    json_path = repo_root / 'data' / 'kufar_city_map.json'
    
    if json_path.exists():
        print(f"\n2. Загрузка city_map из {json_path}...")
        imported = await load_city_map_from_json(str(json_path))
        print(f"✅ Импортировано {imported} записей")
    else:
        print(f"\n⚠️ Файл {json_path} не найден. Пропускаем загрузку.")
        print("   Запустите: python tools/build_city_map_from_candidates.py")
    
    # Тестовые запросы
    test_queries = [
        "минск",
        "барановичи",
        "полоцк",
        "орша",
        "брест",
        "гродно",
        "витебск",
        "гомель",
        "могилёв",
        "могилев",
    ]
    
    print("\n3. Тестирование поиска...")
    print("-" * 80)
    
    all_results = {}
    
    for query in test_queries:
        print(f"\nQuery: '{query}'")
        normalized = normalize_query(query)
        print(f"  Normalized: '{normalized}'")
        
        results = await find_city_slug_by_text(query, limit=5)
        
        if results:
            print(f"  ✅ Найдено {len(results)} результатов:")
            for i, result in enumerate(results, 1):
                slug = result['slug']
                label = result['label_ru']
                score = result.get('score', 0)
                match_type = result.get('match_type', 'unknown')
                province = result.get('province', '')
                
                print(f"    {i}. {label} (score={score}, type={match_type})")
                print(f"       slug: {slug}")
                if province:
                    print(f"       province: {province}")
            
            all_results[query] = results
        else:
            print(f"  ❌ Не найдено")
            all_results[query] = []
    
    # Итоговая статистика
    print("\n" + "=" * 80)
    print("Summary")
    print("=" * 80)
    
    found_count = sum(1 for r in all_results.values() if r)
    total_count = len(all_results)
    
    print(f"\nНайдено результатов: {found_count}/{total_count}")
    
    if found_count == total_count:
        print("✅ Все тесты пройдены!")
    else:
        print(f"⚠️ {total_count - found_count} запросов не дали результатов")
    
    # Сохраняем результаты в файл
    output_file = Path('/tmp/city_lookup_test_output.txt')
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write("City Lookup Test Results\n")
        f.write("=" * 80 + "\n\n")
        
        for query, results in all_results.items():
            f.write(f"Query: '{query}'\n")
            if results:
                f.write(f"  Found {len(results)} results:\n")
                for i, result in enumerate(results, 1):
                    f.write(f"    {i}. {result['label_ru']} (score={result.get('score', 0)})\n")
                    f.write(f"       slug: {result['slug']}\n")
            else:
                f.write("  No results\n")
            f.write("\n")
        
        f.write(f"\nSummary: {found_count}/{total_count} queries found results\n")
    
    print(f"\nРезультаты сохранены в: {output_file}")


if __name__ == '__main__':
    asyncio.run(main())
