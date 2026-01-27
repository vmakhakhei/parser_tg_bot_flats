#!/usr/bin/env python3
"""
from collections import Counter

Скрипт для построения канонической карты городов из city_map_candidates.json
Создает data/kufar_city_map.json с нормализованными записями
"""
import json
import sys
import os
from pathlib import Path
from collections import defaultdict
from datetime import datetime

# Добавляем путь к проекту
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def extract_province_from_slug(slug: str) -> str:
    """Извлекает название провинции из slug"""
    parts = slug.split('~')
    for part in parts:
        if part.startswith('province-'):
            return part.replace('province-', '')
    return ''


def extract_country_from_slug(slug: str) -> str:
    """Извлекает название страны из slug"""
    parts = slug.split('~')
    for part in parts:
        if part.startswith('country-'):
            return part.replace('country-', '')
    return 'belarus'


def normalize_slug(slug: str) -> str:
    """Нормализует slug (lowercase)"""
    return slug.lower().strip()


def build_city_map(candidates_path: str, output_path: str):
    """Строит каноническую карту городов из candidates"""
    print(f"Чтение {candidates_path}...")
    
    with open(candidates_path, 'r', encoding='utf-8') as f:
        candidates = json.load(f)
    
    print(f"Обработка {len(candidates)} записей...")
    
    # Группируем по slug
    slug_groups = defaultdict(list)
    for candidate in candidates:
        slug = candidate.get('slug', '').lower().strip()
        if slug:
            slug_groups[slug].append(candidate)
    
    print(f"Найдено {len(slug_groups)} уникальных slug'ов")
    
    # Строим канонические записи
    city_map = []
    for slug, group in slug_groups.items():
        # Берем наиболее частый label_ru (или первый не-null)
        label_ru_candidates = [c.get('label_ru') for c in group if c.get('label_ru')]
        label_ru = label_ru_candidates[0] if label_ru_candidates else None
        
        # Если несколько вариантов, берем самый частый
        if len(label_ru_candidates) > 1:
            counter = Counter(label_ru_candidates)
            label_ru = counter.most_common(1)[0][0]
        
        # label_by
        label_by_candidates = [c.get('label_by') for c in group if c.get('label_by')]
        label_by = label_by_candidates[0] if label_by_candidates else None
        
        # sample_coords
        coords = None
        for c in group:
            if c.get('sample_coords'):
                coords = c['sample_coords']
                break
        
        # sources (объединяем все источники)
        all_sources = []
        for c in group:
            sources = c.get('sources', [])
            if isinstance(sources, list):
                all_sources.extend(sources)
            elif sources:
                all_sources.append(sources)
        
        # Извлекаем province и country из slug
        province = extract_province_from_slug(slug)
        country = extract_country_from_slug(slug)
        
        city_map.append({
            'slug': slug,
            'label_ru': label_ru,
            'label_by': label_by,
            'province': province,
            'country': country,
            'sample_coords': json.dumps(coords) if coords else None,
            'sources': list(set(all_sources)),  # Уникальные источники
            'occurrences': len(group),
        })
    
    # Сортируем по occurrences (по убыванию)
    city_map.sort(key=lambda x: x['occurrences'], reverse=True)
    
    print(f"Создано {len(city_map)} канонических записей")
    
    # Сохраняем
    output_dir = Path(output_path).parent
    output_dir.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(city_map, f, indent=2, ensure_ascii=False)
    
    print(f"✅ Сохранено в {output_path}")
    
    # Статистика
    print("\nСтатистика:")
    print(f"  - Всего записей: {len(city_map)}")
    print(f"  - С label_ru: {sum(1 for c in city_map if c['label_ru'])}")
    print(f"  - С label_by: {sum(1 for c in city_map if c['label_by'])}")
    print(f"  - С координатами: {sum(1 for c in city_map if c['sample_coords'])}")
    
    # Топ-10 городов
    print("\nТоп-10 городов по occurrences:")
    for i, city in enumerate(city_map[:10], 1):
        print(f"  {i}. {city['label_ru'] or city['slug']} ({city['occurrences']} occurrences)")


def main():
    """Главная функция"""
    # Путь к candidates (из investigation или локальный)
    candidates_paths = [
        '/tmp/kufar_investigation_20260122_072431/city_map_candidates.json',
        'tools/kufar_investigation_result/city_map_candidates.json',
        'city_map_candidates.json',
    ]
    
    candidates_path = None
    for path in candidates_paths:
        if os.path.exists(path):
            candidates_path = path
            break
    
    if not candidates_path:
        print("❌ Не найден city_map_candidates.json")
        print("Проверьте пути:")
        for path in candidates_paths:
            print(f"  - {path}")
        sys.exit(1)
    
    # Путь для сохранения
    repo_root = Path(__file__).parent.parent
    output_path = repo_root / 'data' / 'kufar_city_map.json'
    
    build_city_map(candidates_path, str(output_path))


if __name__ == '__main__':
    main()
