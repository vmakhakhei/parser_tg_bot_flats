#!/usr/bin/env python3
"""
Score tuning tool.
Usage:
  python tools/score_tuning.py --since 2026-01-20T00:00:00 --topk 5
  python tools/score_tuning.py --city "Барановичи" --topk 5
"""

import argparse
import json
import asyncio
import sys
from pathlib import Path
from statistics import median
from collections import defaultdict
from datetime import datetime

# Добавляем корневую директорию в path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Импорты проекта
try:
    from database_turso import build_dynamic_query
except ImportError:
    build_dynamic_query = None

try:
    from scrapers.aggregator import apartment_dict_to_listing, group_similar_listings
except ImportError:
    raise ImportError("Не удалось импортировать функции из scrapers.aggregator")

from scrapers.base import Listing


# helper scoring (same formula as utils/scoring, but weights parametric)
def safe_div(a, b):
    if b in (0, None) or a is None:
        return 0.0
    try:
        return float(a) / float(b)
    except Exception:
        return 0.0


def calc_price_per_m2(listing):
    """Вычисляет цену за м² для объявления"""
    price = getattr(listing, "price_usd", None) or getattr(listing, "price", None)
    area = getattr(listing, "area", None) or getattr(listing, "area_m2", None) or getattr(listing, "total_area", None)
    if not price or not area:
        return None
    return safe_div(price, area)


def market_median_ppm_from_list(listings):
    """Вычисляет медианную цену за м² по всем объявлениям"""
    vals = [calc_price_per_m2(l) for l in listings if calc_price_per_m2(l) is not None]
    if not vals:
        return 1.0
    return median(vals)


def compute_group_features(group):
    """Вычисляет характеристики группы"""
    ppms = [calc_price_per_m2(l) for l in group if calc_price_per_m2(l) is not None]
    if not ppms:
        return {"median_ppm": None, "dispersion": None, "count": len(group)}
    m = median(ppms)
    disp = 0.0
    if len(ppms) > 0 and m and max(ppms) and min(ppms):
        disp = (max(ppms) - min(ppms)) / m
    return {"median_ppm": m, "dispersion": disp, "count": len(group)}


def score_with_weights(group, market_median_ppm, weights):
    """
    Вычисляет score группы с заданными весами.
    
    Args:
        group: Список объявлений в группе
        market_median_ppm: Медианная цена за м² по рынку
        weights: Кортеж (w_price, w_delta, w_disp, w_count)
    
    Returns:
        Score группы
    """
    w_price, w_delta, w_disp, w_count = weights
    feats = compute_group_features(group)
    if feats["median_ppm"] is None:
        return 0.0
    house_median = feats["median_ppm"]
    price_score = safe_div(market_median_ppm, house_median)
    delta_vs_market = safe_div(market_median_ppm - house_median, market_median_ppm)
    dispersion = feats["dispersion"] or 0.0
    dispersion_score = max(0.0, 1.0 - dispersion)
    count_score = min(feats["count"], 6) / 6.0
    score = (
        w_price * price_score +
        w_delta * delta_vs_market +
        w_disp * dispersion_score +
        w_count * count_score
    )
    return score


def normalize(values):
    """Нормализует значения в диапазон [0, 1]"""
    if not values:
        return []
    mn = min(values)
    mx = max(values)
    if mx == mn:
        return [0.0 for _ in values]
    return [(v - mn) / (mx - mn) for v in values]


def evaluate(weights, groups, market_median_ppm, topk=5):
    """
    Оценивает набор весов на группах.
    
    Returns:
        Словарь с метриками и топ-K группами
    """
    # Вычисляем score для каждой группы
    scored = []
    for g in groups:
        sc = score_with_weights(g, market_median_ppm, weights)
        feats = compute_group_features(g)
        scored.append({
            "group": g,
            "score": sc,
            "median_ppm": feats["median_ppm"] or 0.0,
            "dispersion": feats["dispersion"] or 0.0,
            "count": feats["count"]
        })
    
    # Сортируем по score (убывание)
    scored.sort(key=lambda x: x["score"], reverse=True)
    top = scored[:topk]
    
    # Вычисляем метрики для топ-K
    ppm_vals = [x["median_ppm"] for x in top]
    disp_vals = [x["dispersion"] for x in top]
    count_vals = [x["count"] for x in top]
    
    if not ppm_vals:
        return {
            "weights": weights,
            "market_ppm_topk": None,
            "dispersion_topk": None,
            "count_topk": None,
            "objective": None,
            "top": []
        }
    
    mean_ppm = sum(ppm_vals) / len(ppm_vals)
    mean_disp = sum(disp_vals) / len(disp_vals)
    mean_count = sum(count_vals) / len(count_vals)
    
    return {
        "weights": weights,
        "market_ppm_topk": mean_ppm,
        "dispersion_topk": mean_disp,
        "count_topk": mean_count,
        "top": top
    }


async def load_listings_from_db(city=None, since=None, limit=1000):
    """
    Загружает объявления из БД.
    
    Args:
        city: Город для фильтрации (опционально)
        since: Дата начала (опционально)
        limit: Максимальное количество объявлений
    
    Returns:
        Список словарей с данными объявлений
    """
    if not build_dynamic_query:
        raise RuntimeError("build_dynamic_query не доступен (проверьте импорты)")
    
    # Используем build_dynamic_query для получения данных
    # Если указан город, фильтруем по адресу
    region = None
    if city:
        # Пробуем найти город в адресе
        region = city
    
    listings = await build_dynamic_query(
        min_price=None,
        max_price=None,
        rooms=None,
        region=region,
        source=None,
        is_active=True,
        limit=limit
    )
    
    return listings


async def main(args):
    """Основная функция"""
    print("=" * 60)
    print("Score Tuning Tool")
    print("=" * 60)
    
    # Загружаем объявления из БД
    print(f"\n📥 Загрузка объявлений из БД...")
    if args.city:
        print(f"   Город: {args.city}")
        raw = await load_listings_from_db(city=args.city, limit=2000)
    elif args.since:
        print(f"   С даты: {args.since}")
        raw = await load_listings_from_db(since=args.since, limit=2000)
    else:
        print("   Все активные объявления")
        raw = await load_listings_from_db(limit=2000)
    
    print(f"✅ Загружено {len(raw)} записей из БД")
    
    # Конвертируем в Listing объекты
    print(f"\n🔄 Конвертация в Listing объекты...")
    listings = []
    for r in raw:
        try:
            l = await apartment_dict_to_listing(r)
            if l:
                listings.append(l)
        except Exception as e:
            continue
    
    print(f"✅ Конвертировано {len(listings)} объявлений")
    
    if not listings:
        print("❌ Нет объявлений для анализа!")
        return
    
    # Группируем объявления
    print(f"\n📊 Группировка объявлений...")
    groups = group_similar_listings(listings)
    print(f"✅ Создано {len(groups)} групп")
    
    if not groups:
        print("❌ Нет групп для анализа!")
        return
    
    # Вычисляем медианную цену за м² по рынку
    market_ppm = market_median_ppm_from_list(listings)
    print(f"\n💰 Медианная цена за м² по рынку: {market_ppm:.2f}")
    
    # Набор кандидатов весов
    candidate_weights = [
        (0.45, 0.25, 0.15, 0.15),  # baseline
        (0.55, 0.20, 0.15, 0.10),  # price_heavy
        (0.40, 0.20, 0.25, 0.15),  # dispersion_heavy
        (0.35, 0.20, 0.15, 0.30),  # count_favor
        (0.50, 0.30, 0.10, 0.10),  # conservative
        (0.30, 0.30, 0.20, 0.20),  # balanced
        (0.30, 0.20, 0.30, 0.20),  # low_price
    ]
    
    weight_names = [
        "baseline",
        "price_heavy",
        "dispersion_heavy",
        "count_favor",
        "conservative",
        "balanced",
        "low_price"
    ]
    
    print(f"\n🧪 Тестирование {len(candidate_weights)} наборов весов...")
    print(f"   Top-K: {args.topk}")
    
    # Оцениваем каждый набор весов
    results = []
    for i, w in enumerate(candidate_weights):
        name = weight_names[i] if i < len(weight_names) else f"weights_{i}"
        print(f"   [{i+1}/{len(candidate_weights)}] {name}: {w}")
        res = evaluate(w, groups, market_ppm, topk=args.topk)
        res["name"] = name
        results.append(res)
    
    # Вычисляем composite objective
    print(f"\n📈 Вычисление composite objective...")
    
    ppm_vals = [r["market_ppm_topk"] or 0 for r in results]
    disp_vals = [r["dispersion_topk"] or 0 for r in results]
    count_vals = [r["count_topk"] or 0 for r in results]
    
    ppm_norm = normalize(ppm_vals)
    disp_norm = normalize(disp_vals)
    count_penalty = [abs(c - 3) / 3.0 for c in count_vals]
    
    # Веса для composite objective
    alpha = 0.5   # важность ppm (чем ниже - тем лучше)
    beta = 0.3    # важность dispersion (чем ниже - тем лучше)
    gamma = 0.2   # важность count penalty (чем ближе к 3 - тем лучше)
    
    final = []
    for i, r in enumerate(results):
        obj = alpha * ppm_norm[i] + beta * disp_norm[i] + gamma * count_penalty[i]
        final.append({
            "name": r["name"],
            "weights": r["weights"],
            "market_ppm_topk": r["market_ppm_topk"],
            "dispersion_topk": r["dispersion_topk"],
            "count_topk": r["count_topk"],
            "objective": obj
        })
    
    # Сортируем по objective (меньше = лучше)
    final_sorted = sorted(final, key=lambda x: x["objective"])
    
    print("\n" + "=" * 60)
    print("📊 РЕЗУЛЬТАТЫ (меньше objective = лучше)")
    print("=" * 60)
    
    for i, res in enumerate(final_sorted, 1):
        print(f"\n{i}. {res['name']}")
        print(f"   Веса: {res['weights']}")
        print(f"   market_ppm_topk: {res['market_ppm_topk']:.2f}")
        print(f"   dispersion_topk: {res['dispersion_topk']:.4f}")
        print(f"   count_topk: {res['count_topk']:.2f}")
        print(f"   objective: {res['objective']:.4f}")
    
    # Сохраняем результаты в файл
    output_file = Path(__file__).parent / "score_tuning_results.json"
    
    # Подготовка данных для сохранения (убираем группы из top, оставляем только метрики)
    results_to_save = []
    for res in final_sorted:
        results_to_save.append({
            "name": res["name"],
            "weights": res["weights"],
            "market_ppm_topk": res["market_ppm_topk"],
            "dispersion_topk": res["dispersion_topk"],
            "count_topk": res["count_topk"],
            "objective": res["objective"]
        })
    
    output_file.write_text(
        json.dumps(results_to_save, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )
    
    print(f"\n💾 Результаты сохранены в: {output_file}")
    
    # Показываем топ-3
    print("\n" + "=" * 60)
    print("🏆 ТОП-3 РЕКОМЕНДУЕМЫХ НАБОРА ВЕСОВ")
    print("=" * 60)
    
    for i, res in enumerate(final_sorted[:3], 1):
        print(f"\n{i}. {res['name']}")
        print(f"   Веса (price, delta, dispersion, count): {res['weights']}")
        print(f"   market_ppm_topk: {res['market_ppm_topk']:.2f}")
        print(f"   dispersion_topk: {res['dispersion_topk']:.4f}")
        print(f"   count_topk: {res['count_topk']:.2f}")
        print(f"   objective: {res['objective']:.4f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Инструмент для настройки весов scoring"
    )
    parser.add_argument(
        "--since",
        type=str,
        default=None,
        help="Дата начала в формате ISO (например, 2026-01-18T00:00:00)"
    )
    parser.add_argument(
        "--city",
        type=str,
        default=None,
        help="Город для фильтрации (например, 'Барановичи')"
    )
    parser.add_argument(
        "--topk",
        type=int,
        default=5,
        help="Количество топ групп для оценки (по умолчанию 5)"
    )
    
    args = parser.parse_args()
    
    # Запускаем async функцию
    asyncio.run(main(args))
