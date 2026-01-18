"""
Unit-тесты для ListingDTO
"""
import pytest
from scrapers.dto import ListingDTO


class TestListingDTO:
    """Тесты для валидации структуры ListingDTO"""
    
    def test_valid_dto(self):
        """Тест создания валидного DTO"""
        dto = ListingDTO(
            title="2-комн. квартира, 50 м²",
            price=50000,
            url="https://example.com/listing/123",
            location="Минск, ул. Ленина, 1",
            source="test"
        )
        
        assert dto.title == "2-комн. квартира, 50 м²"
        assert dto.price == 50000
        assert dto.url == "https://example.com/listing/123"
        assert dto.location == "Минск, ул. Ленина, 1"
        assert dto.source == "test"
    
    def test_empty_title_raises_error(self):
        """Тест: пустой title должен вызывать ошибку"""
        with pytest.raises(ValueError, match="title"):
            ListingDTO(
                title="",
                price=50000,
                url="https://example.com/listing/123",
                location="Минск",
                source="test"
            )
    
    def test_none_title_raises_error(self):
        """Тест: None title должен вызывать ошибку"""
        with pytest.raises(ValueError, match="title"):
            ListingDTO(
                title=None,
                price=50000,
                url="https://example.com/listing/123",
                location="Минск",
                source="test"
            )
    
    def test_negative_price_raises_error(self):
        """Тест: отрицательная цена должна вызывать ошибку"""
        with pytest.raises(ValueError, match="price"):
            ListingDTO(
                title="Квартира",
                price=-1000,
                url="https://example.com/listing/123",
                location="Минск",
                source="test"
            )
    
    def test_zero_price_is_valid(self):
        """Тест: нулевая цена допустима (цена договорная)"""
        dto = ListingDTO(
            title="Квартира",
            price=0,
            url="https://example.com/listing/123",
            location="Минск",
            source="test"
        )
        assert dto.price == 0
    
    def test_empty_url_raises_error(self):
        """Тест: пустой URL должен вызывать ошибку"""
        with pytest.raises(ValueError, match="url"):
            ListingDTO(
                title="Квартира",
                price=50000,
                url="",
                location="Минск",
                source="test"
            )
    
    def test_invalid_url_raises_error(self):
        """Тест: невалидный URL должен вызывать ошибку"""
        with pytest.raises(ValueError, match="url"):
            ListingDTO(
                title="Квартира",
                price=50000,
                url="not-a-url",
                location="Минск",
                source="test"
            )
    
    def test_valid_http_url(self):
        """Тест: валидный HTTP URL"""
        dto = ListingDTO(
            title="Квартира",
            price=50000,
            url="http://example.com/listing/123",
            location="Минск",
            source="test"
        )
        assert dto.url == "http://example.com/listing/123"
    
    def test_valid_https_url(self):
        """Тест: валидный HTTPS URL"""
        dto = ListingDTO(
            title="Квартира",
            price=50000,
            url="https://example.com/listing/123",
            location="Минск",
            source="test"
        )
        assert dto.url == "https://example.com/listing/123"
    
    def test_none_location_raises_error(self):
        """Тест: None location должен вызывать ошибку"""
        with pytest.raises(ValueError, match="location"):
            ListingDTO(
                title="Квартира",
                price=50000,
                url="https://example.com/listing/123",
                location=None,
                source="test"
            )
    
    def test_empty_location_is_valid(self):
        """Тест: пустая строка location допустима"""
        dto = ListingDTO(
            title="Квартира",
            price=50000,
            url="https://example.com/listing/123",
            location="",
            source="test"
        )
        assert dto.location == ""
    
    def test_empty_source_raises_error(self):
        """Тест: пустой source должен вызывать ошибку"""
        with pytest.raises(ValueError, match="source"):
            ListingDTO(
                title="Квартира",
                price=50000,
                url="https://example.com/listing/123",
                location="Минск",
                source=""
            )
    
    def test_from_dict_valid(self):
        """Тест: создание DTO из словаря с валидными данными"""
        data = {
            "title": "2-комн. квартира",
            "price": 50000,
            "url": "https://example.com/123",
            "location": "Минск",
        }
        dto = ListingDTO.from_dict(data, source="test")
        
        assert dto is not None
        assert dto.title == "2-комн. квартира"
        assert dto.price == 50000
        assert dto.url == "https://example.com/123"
        assert dto.location == "Минск"
        assert dto.source == "test"
    
    def test_from_dict_with_alternative_fields(self):
        """Тест: создание DTO из словаря с альтернативными полями"""
        data = {
            "name": "2-комн. квартира",  # альтернатива title
            "price": "50,000",  # строка с запятыми
            "link": "https://example.com/123",  # альтернатива url
            "address": "Минск",  # альтернатива location
        }
        dto = ListingDTO.from_dict(data, source="test")
        
        assert dto is not None
        assert dto.title == "2-комн. квартира"
        assert dto.price == 50000  # должно быть распарсено
        assert dto.url == "https://example.com/123"
        assert dto.location == "Минск"
    
    def test_from_dict_invalid_returns_none(self):
        """Тест: создание DTO из невалидного словаря возвращает None"""
        data = {
            "title": "",  # пустой title
            "price": 50000,
            "url": "https://example.com/123",
            "location": "Минск",
        }
        dto = ListingDTO.from_dict(data, source="test")
        
        assert dto is None
    
    def test_to_dict(self):
        """Тест: конвертация DTO в словарь"""
        dto = ListingDTO(
            title="Квартира",
            price=50000,
            url="https://example.com/listing/123",
            location="Минск",
            source="test"
        )
        
        result = dto.to_dict()
        
        assert result == {
            "title": "Квартира",
            "price": 50000,
            "url": "https://example.com/listing/123",
            "location": "Минск",
            "source": "test",
        }
    
    def test_str_representation(self):
        """Тест: строковое представление DTO"""
        dto = ListingDTO(
            title="Квартира" * 10,  # длинный заголовок
            price=50000,
            url="https://example.com/listing/123",
            location="Минск",
            source="test"
        )
        
        str_repr = str(dto)
        
        assert "ListingDTO" in str_repr
        assert "test" in str_repr
        assert "50000" in str_repr
        assert len(str_repr) < len(dto.title) + 50  # должно быть обрезано
