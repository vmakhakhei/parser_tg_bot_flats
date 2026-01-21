"""
Unit тесты для проверки сохранения фильтров
"""
import pytest
import asyncio
from unittest.mock import Mock, patch, AsyncMock

# Импорты
from database_turso import set_user_filters_turso, get_user_filters_turso


@pytest.mark.asyncio
async def test_filters_save_and_load():
    """Тест сохранения и загрузки фильтров"""
    test_telegram_id = 999999999  # Тестовый ID
    test_city = "Полоцк"
    
    # Тестовые фильтры
    test_filters = {
        "city": test_city,
        "min_rooms": 2,
        "max_rooms": 3,
        "min_price": 30000,
        "max_price": 60000,
        "seller_type": "all",
        "delivery_mode": "brief",
    }
    
    # Сохраняем фильтры
    await set_user_filters_turso(test_telegram_id, test_filters)
    
    # Загружаем обратно
    loaded_filters = await get_user_filters_turso(test_telegram_id)
    
    # Проверяем сохранение
    assert loaded_filters is not None, "Фильтры должны быть сохранены"
    assert loaded_filters.get("city") == test_city, f"Город должен быть '{test_city}', получен '{loaded_filters.get('city')}'"
    assert loaded_filters.get("min_rooms") == 2, "min_rooms должен быть 2"
    assert loaded_filters.get("max_rooms") == 3, "max_rooms должен быть 3"
    assert loaded_filters.get("min_price") == 30000, "min_price должен быть 30000"
    assert loaded_filters.get("max_price") == 60000, "max_price должен быть 60000"


@pytest.mark.asyncio
async def test_filters_save_city_dict():
    """Тест сохранения города как location dict"""
    test_telegram_id = 999999998
    test_location = {
        "id": "123",
        "name": "Полоцк",
        "region": "Витебская область",
        "type": "city",
        "slug": "polotsk",
        "lat": 55.5,
        "lng": 28.5,
    }
    
    test_filters = {
        "city": test_location,
        "min_rooms": 1,
        "max_rooms": 4,
        "min_price": 0,
        "max_price": 100000,
        "seller_type": "all",
        "delivery_mode": "brief",
    }
    
    # Сохраняем
    await set_user_filters_turso(test_telegram_id, test_filters)
    
    # Загружаем
    loaded_filters = await get_user_filters_turso(test_telegram_id)
    
    # Проверяем
    assert loaded_filters is not None
    city_data = loaded_filters.get("city")
    assert city_data is not None
    
    # Может быть dict или строка (в зависимости от реализации)
    if isinstance(city_data, dict):
        assert city_data.get("name") == "Полоцк"
        assert city_data.get("id") == "123"
    else:
        # Если сохранено как строка - проверяем имя
        assert "полоцк" in str(city_data).lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
