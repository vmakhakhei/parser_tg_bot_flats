"""
Unit тесты для location_service
"""
import pytest
import asyncio
from unittest.mock import Mock, patch, AsyncMock
import json

# Импорты
from services.location_service import (
    normalize_location,
    search_locations,
    validate_city_input,
    get_location_by_id
)


def test_normalize_location():
    """Тест нормализации локации"""
    raw_location = {
        "id": "123",
        "name": "Минск",
        "region": "Минская область",
        "type": "city",
        "slug": "minsk",
        "lat": 53.9,
        "lng": 27.5
    }
    
    normalized = normalize_location(raw_location)
    
    assert normalized["id"] == "123"
    assert normalized["name"] == "Минск"
    assert normalized["region"] == "Минская область"
    assert normalized["type"] == "city"
    assert normalized["slug"] == "minsk"
    assert normalized["lat"] == 53.9
    assert normalized["lng"] == 27.5
    assert "raw" in normalized


@pytest.mark.asyncio
async def test_search_locations_success():
    """Тест успешного поиска локаций"""
    mock_response = [
        {
            "id": "1",
            "name": "Минск",
            "region": "Минская область",
            "type": "city",
            "slug": "minsk",
            "lat": 53.9,
            "lng": 27.5
        }
    ]
    
    with patch('services.location_service._get_cached_location', return_value=None):
        with patch('aiohttp.ClientSession') as mock_session:
            mock_response_obj = Mock()
            mock_response_obj.status = 200
            mock_response_obj.json = AsyncMock(return_value=mock_response)
            
            mock_session.return_value.__aenter__.return_value.get.return_value.__aenter__.return_value = mock_response_obj
            
            with patch('services.location_service._save_location_to_cache'):
                result = await search_locations("Минск")
                
                assert len(result) == 1
                assert result[0]["name"] == "Минск"
                assert result[0]["id"] == "1"


@pytest.mark.asyncio
async def test_search_locations_cache_hit():
    """Тест использования кэша"""
    cached_locations = [
        {
            "id": "1",
            "name": "Минск",
            "region": "Минская область",
            "type": "city",
            "slug": "minsk",
            "lat": 53.9,
            "lng": 27.5,
            "raw": {}
        }
    ]
    
    with patch('services.location_service._get_cached_location', return_value=cached_locations):
        result = await search_locations("Минск")
        
        assert len(result) == 1
        assert result[0]["name"] == "Минск"


@pytest.mark.asyncio
async def test_validate_city_input_not_found():
    """Тест валидации - город не найден"""
    with patch('services.location_service.search_locations', return_value=[]):
        result = await validate_city_input("НесуществующийГород")
        
        assert result["status"] == "not_found"


@pytest.mark.asyncio
async def test_validate_city_input_auto_select():
    """Тест валидации - один результат, автоматический выбор"""
    locations = [
        {
            "id": "1",
            "name": "Минск",
            "region": "Минская область",
            "type": "city",
            "slug": "minsk",
            "lat": 53.9,
            "lng": 27.5,
            "raw": {}
        }
    ]
    
    with patch('services.location_service.search_locations', return_value=locations):
        result = await validate_city_input("Минск")
        
        assert result["status"] == "ok"
        assert result.get("auto") is True
        assert result["location"]["name"] == "Минск"


@pytest.mark.asyncio
async def test_validate_city_input_multiple():
    """Тест валидации - несколько результатов"""
    locations = [
        {"id": "1", "name": "Минск", "region": "Минская", "type": "city", "slug": "", "lat": 53.9, "lng": 27.5, "raw": {}},
        {"id": "2", "name": "Минск", "region": "Брестская", "type": "city", "slug": "", "lat": 52.0, "lng": 23.0, "raw": {}}
    ]
    
    with patch('services.location_service.search_locations', return_value=locations):
        result = await validate_city_input("Минск")
        
        assert result["status"] == "multiple"
        assert len(result["choices"]) == 2


@pytest.mark.asyncio
async def test_validate_city_input_too_many():
    """Тест валидации - слишком много результатов"""
    locations = [
        {"id": str(i), "name": f"Город{i}", "region": "", "type": "city", "slug": "", "lat": 0, "lng": 0, "raw": {}}
        for i in range(10)
    ]
    
    with patch('services.location_service.search_locations', return_value=locations):
        result = await validate_city_input("Город")
        
        assert result["status"] == "too_many"


@pytest.mark.asyncio
async def test_get_location_by_id_cache_hit():
    """Тест получения локации по ID - кэш попадание"""
    raw_data = {
        "id": "123",
        "name": "Минск",
        "region": "Минская область",
        "type": "city",
        "slug": "minsk",
        "lat": 53.9,
        "lng": 27.5
    }
    
    def mock_read_cache():
        return normalize_location(raw_data)
    
    with patch('services.location_service._get_turso_connection') as mock_conn:
        mock_cursor = Mock()
        mock_cursor.fetchone.return_value = (json.dumps(raw_data),)
        mock_cursor.execute.return_value = mock_cursor
        
        mock_conn_instance = Mock()
        mock_conn_instance.execute.return_value = mock_cursor
        mock_conn.return_value = mock_conn_instance
        
        result = await get_location_by_id("123")
        
        # Проверяем, что был вызов к БД
        assert mock_conn_instance.execute.called


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
