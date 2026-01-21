"""
Детерминированный scoring для групп объявлений (домов).

Используется ТОЛЬКО для сортировки домов в summary-first режиме.
"""
from statistics import median
from typing import List

from scrapers.base import Listing


def safe_div(a: float, b: float) -> float:
    """
    Безопасное деление с защитой от деления на ноль.
    
    Args:
        a: Числитель
        b: Знаменатель
    
    Returns:
        Результат деления или 0.0 если деление невозможно
    """
    if not a or not b or b == 0:
        return 0.0
    return a / b


def calc_price_per_m2(listing: Listing) -> float | None:
    """
    Вычисляет цену за м² для объявления.
    
    Args:
        listing: Объявление
    
    Returns:
        Цена за м² или None если данных недостаточно
    """
    if not listing.price_usd or not listing.area:
        return None
    return safe_div(listing.price_usd, listing.area)


def calc_market_median_ppm(listings: List[Listing]) -> float:
    """
    Вычисляет медианную цену за м² по всему рынку (все объявления).
    
    Args:
        listings: Список всех объявлений
    
    Returns:
        Медианная цена за м² (защита от деления на 0: минимум 1.0)
    """
    values = [
        calc_price_per_m2(l)
        for l in listings
        if calc_price_per_m2(l) is not None
    ]

    if not values:
        return 1.0  # защита от деления на 0

    return median(values)


def score_group(
    group: List[Listing],
    market_median_ppm: float,
) -> float:
    """
    Возвращает score дома (чем больше — тем лучше).
    
    Используется для сортировки домов в summary-first режиме.
    
    Формула scoring:
    - 45%: Цена за метр (чем ниже — тем лучше)
    - 25%: Отклонение от рынка (чем больше отклонение вниз — тем лучше)
    - 15%: Разброс цен внутри дома (чем меньше разброс — тем лучше)
    - 15%: Количество вариантов (soft cap на 6)
    
    Args:
        group: Список объявлений в группе (дом)
        market_median_ppm: Медианная цена за м² по всему рынку
    
    Returns:
        Числовой score (чем выше, тем лучше), округленный до 4 знаков
    """
    prices_per_m2 = [
        calc_price_per_m2(l)
        for l in group
        if calc_price_per_m2(l) is not None
    ]

    if not prices_per_m2:
        return 0.0

    house_median_ppm = median(prices_per_m2)

    # A. Цена за метр (чем ниже — тем лучше)
    price_score = safe_div(market_median_ppm, house_median_ppm)

    # B. Отклонение от рынка
    delta_vs_market = safe_div(
        market_median_ppm - house_median_ppm,
        market_median_ppm
    )

    # C. Разброс цен внутри дома
    dispersion = (
        (max(prices_per_m2) - min(prices_per_m2))
        / house_median_ppm
    )

    dispersion_score = max(0.0, 1.0 - dispersion)

    # D. Количество вариантов (soft cap)
    count_score = min(len(group), 6) / 6

    score = (
        0.45 * price_score +
        0.25 * delta_vs_market +
        0.15 * dispersion_score +
        0.15 * count_score
    )

    return round(score, 4)
