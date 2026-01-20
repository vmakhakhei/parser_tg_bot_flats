"""
Утилиты для работы с географическими координатами
"""
from math import radians, sin, cos, sqrt, atan2


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Вычисляет расстояние между двумя точками на поверхности Земли по формуле Хаверсина.
    
    Args:
        lat1: Широта первой точки в градусах
        lon1: Долгота первой точки в градусах
        lat2: Широта второй точки в градусах
        lon2: Долгота второй точки в градусах
    
    Returns:
        Расстояние в метрах
    """
    # Радиус Земли в метрах
    R = 6371000.0
    
    # Конвертируем градусы в радианы
    phi1, phi2 = radians(lat1), radians(lat2)
    dphi = radians(lat2 - lat1)
    dlambda = radians(lon2 - lon1)
    
    # Формула Хаверсина
    a = sin(dphi/2)**2 + cos(phi1)*cos(phi2)*sin(dlambda/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1-a))
    
    return R * c  # расстояние в метрах
