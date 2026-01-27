"""
from rapidfuzz import process, fuzz
from error_logger import log_info, log_warning
from database_turso import turso_transaction

Модуль для поиска городов по тексту с использованием локальной карты городов
Поддерживает exact match, prefix match и fuzzy search
"""
import re
import json
import asyncio
from typing import List, Dict, Any, Optional
from difflib import get_close_matches

# Пробуем импортировать rapidfuzz для лучшего fuzzy search
try:
    from rapidfuzz import process, fuzz
    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    RAPIDFUZZ_AVAILABLE = False
    process = None
    fuzz = None

from database_turso import turso_transaction

try:
    from error_logger import log_info, log_warning
except ImportError:
    def log_info(source, message):
        print(f"[{source}] {message}")
    def log_warning(source, message):
        print(f"[{source}] {message}")


def extract_city_name_from_text(text: str) -> str:
    """
    Извлекает название города из текста, если пользователь скопировал переписку.
    Ищет строки, которые могут быть названием города (короткие строки без спецсимволов).
    """
    if not text:
        return ""
    
    # Разбиваем на строки
    lines = text.split('\n')
    
    # Ищем строки, которые могут быть названием города
    # Город обычно: короткая строка (2-30 символов), без эмодзи, без дат, без команд
    city_candidates = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        # Пропускаем строки с эмодзи в начале
        if line.startswith(('👨', '💻', 'ℹ️', '✏️', '❌', '🔍', '✅', '⚠️')):
            continue
        
        # Пропускаем команды
        if line.startswith('/'):
            continue
        
        # Пропускаем строки с датами
        if re.search(r'\d{1,2}\s+(янв|фев|мар|апр|май|июн|июл|авг|сен|окт|ноя|дек)', line, re.I):
            continue
        
        # Пропускаем строки с временем
        if re.search(r'\d{1,2}:\d{2}', line):
            continue
        
        # Пропускаем длинные строки (вероятно, это не название города)
        if len(line) > 50:
            continue
        
        # Пропускаем строки с множеством спецсимволов
        if len(re.findall(r'[^\w\s\-]', line)) > 3:
            continue
        
        # Если строка выглядит как название города (2-30 символов, буквы и пробелы)
        if 2 <= len(line) <= 30 and re.match(r'^[А-Яа-яЁё\s\-]+$', line):
            city_candidates.append(line)
    
    # Возвращаем первую подходящую строку
    if city_candidates:
        return city_candidates[0]
    
    # Если ничего не найдено, возвращаем исходный текст
    return text.strip()


def normalize_query(query: str) -> str:
    """
    Нормализует поисковый запрос:
    - извлекает название города из переписки (если пользователь скопировал текст)
    - lower(), strip()
    - удаляет префиксы ('г.', 'город', 'г ')
    - заменяет множественные пробелы на один
    """
    if not query:
        return ""
    
    # Сначала пытаемся извлечь название города из текста
    query = extract_city_name_from_text(query)
    
    # Удаляем префиксы
    prefixes = ['г.', 'город', 'г ', 'г. ']
    normalized = query.strip()
    for prefix in prefixes:
        if normalized.lower().startswith(prefix.lower()):
            normalized = normalized[len(prefix):].strip()
    
    # Нормализуем пробелы
    normalized = re.sub(r'\s+', ' ', normalized)
    
    # Lowercase и strip
    normalized = normalized.lower().strip()
    
    return normalized


async def find_city_slug_by_text(query: str, limit: int = 10, threshold: int = 80) -> List[Dict[str, Any]]:
    """
    Ищет города по тексту в локальной карте городов.
    
    Args:
        query: Поисковый запрос (название города)
        limit: Максимальное количество результатов
        threshold: Минимальный score для fuzzy search (0-100)
        
    Returns:
        Список словарей с полями: slug, label_ru, score, sample_coords, province
        Отсортирован по score (по убыванию)
    """
    normalized_query = normalize_query(query)
    
    if not normalized_query:
        return []
    
    log_info("city_lookup", f"[CITYLOOKUP] query=\"{query}\" normalized=\"{normalized_query}\"")
    
    def _search_in_db():
        """Синхронная функция для поиска в БД"""
        try:
            
            with turso_transaction() as conn:
                # Загружаем все города из БД
                cursor = conn.execute("""
                    SELECT slug, label_ru, label_by, province, country, sample_coords
                    FROM city_codes
                    WHERE label_ru IS NOT NULL OR label_by IS NOT NULL
                """)
                
                cities = []
                for row in cursor.fetchall():
                    slug, label_ru, label_by, province, country, sample_coords = row
                    
                    # Собираем все варианты названий для поиска
                    search_terms = []
                    if label_ru:
                        search_terms.append(label_ru.lower())
                    if label_by:
                        search_terms.append(label_by.lower())
                    
                    cities.append({
                        'slug': slug,
                        'label_ru': label_ru,
                        'label_by': label_by,
                        'province': province,
                        'country': country,
                        'sample_coords': json.loads(sample_coords) if sample_coords else None,
                        'search_terms': search_terms,
                    })
                
                return cities
        
        except Exception as e:
            log_warning("city_lookup", f"Ошибка загрузки городов из БД: {e}")
            return []
    
    cities = await asyncio.to_thread(_search_in_db)
    
    if not cities:
        log_warning("city_lookup", "[CITYLOOKUP] No cities found in database")
        return []
    
    results = []
    
    # 1. Exact match (точное совпадение)
    for city in cities:
        for term in city['search_terms']:
            if term == normalized_query:
                results.append({
                    'slug': city['slug'],
                    'label_ru': city['label_ru'],
                    'label_by': city['label_by'],
                    'score': 100,
                    'sample_coords': city['sample_coords'],
                    'province': city['province'],
                    'match_type': 'exact',
                })
                break
    
    if results:
        log_info("city_lookup", f"[CITYLOOKUP] query=\"{query}\" results={len(results)} (exact match)")
        return results[:limit]
    
    # 2. Prefix match (начало строки)
    for city in cities:
        for term in city['search_terms']:
            if term.startswith(normalized_query) or normalized_query.startswith(term):
                # Вычисляем score на основе длины совпадения
                match_len = min(len(term), len(normalized_query))
                total_len = max(len(term), len(normalized_query))
                score = int((match_len / total_len) * 95)  # До 95 для prefix match
                
                results.append({
                    'slug': city['slug'],
                    'label_ru': city['label_ru'],
                    'label_by': city['label_by'],
                    'score': score,
                    'sample_coords': city['sample_coords'],
                    'province': city['province'],
                    'match_type': 'prefix',
                })
                break
    
    if results:
        # Удаляем дубликаты по slug
        seen = set()
        unique_results = []
        for r in results:
            if r['slug'] not in seen:
                seen.add(r['slug'])
                unique_results.append(r)
        
        # Сортируем по score
        unique_results.sort(key=lambda x: x['score'], reverse=True)
        log_info("city_lookup", f"[CITYLOOKUP] query=\"{query}\" results={len(unique_results)} (prefix match)")
        return unique_results[:limit]
    
    # 3. Fuzzy search
    search_strings = []
    city_map = {}
    
    for city in cities:
        for term in city['search_terms']:
            search_strings.append(term)
            city_map[term] = city
    
    if RAPIDFUZZ_AVAILABLE:
        # Используем rapidfuzz для лучшего fuzzy search
        matches = process.extract(
            normalized_query,
            search_strings,
            limit=limit * 2,  # Берем больше для фильтрации по threshold
            scorer=fuzz.ratio
        )
        
        for match_term, score, _ in matches:
            if score >= threshold:
                city = city_map[match_term]
                results.append({
                    'slug': city['slug'],
                    'label_ru': city['label_ru'],
                    'label_by': city['label_by'],
                    'score': score,
                    'sample_coords': city['sample_coords'],
                    'province': city['province'],
                    'match_type': 'fuzzy',
                })
    else:
        # Fallback на difflib
        matches = get_close_matches(
            normalized_query,
            search_strings,
            n=limit * 2,
            cutoff=threshold / 100.0
        )
        
        for match_term in matches:
            city = city_map[match_term]
            # Вычисляем примерный score
            score = int(fuzz.ratio(normalized_query, match_term) if hasattr(fuzz, 'ratio') else 85)
            
            results.append({
                'slug': city['slug'],
                'label_ru': city['label_ru'],
                'label_by': city['label_by'],
                'score': score,
                'sample_coords': city['sample_coords'],
                'province': city['province'],
                'match_type': 'fuzzy',
            })
    
    # Удаляем дубликаты по slug и сортируем по score
    seen = set()
    unique_results = []
    for r in results:
        if r['slug'] not in seen:
            seen.add(r['slug'])
            unique_results.append(r)
    
    unique_results.sort(key=lambda x: x['score'], reverse=True)
    
    log_info("city_lookup", f"[CITYLOOKUP] query=\"{query}\" results={len(unique_results)} (fuzzy)")
    
    return unique_results[:limit]


async def get_city_by_slug(slug: str) -> Optional[Dict[str, Any]]:
    """
    Получает информацию о городе по slug.
    
    Args:
        slug: Slug города
        
    Returns:
        Словарь с информацией о городе или None
    """
    def _get_from_db():
        try:
            
            with turso_transaction() as conn:
                cursor = conn.execute("""
                    SELECT slug, label_ru, label_by, province, country, sample_coords
                    FROM city_codes
                    WHERE slug = ?
                """, (slug.lower().strip(),))
                
                row = cursor.fetchone()
                if row:
                    slug_val, label_ru, label_by, province, country, sample_coords = row
                    return {
                        'slug': slug_val,
                        'label_ru': label_ru,
                        'label_by': label_by,
                        'province': province,
                        'country': country,
                        'sample_coords': json.loads(sample_coords) if sample_coords else None,
                    }
                return None
        
        except Exception as e:
            log_warning("city_lookup", f"Ошибка получения города по slug {slug}: {e}")
            return None
    
    return await asyncio.to_thread(_get_from_db)
