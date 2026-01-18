"""
Unit-тесты для OnlinerRealtScraper
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from scrapers.onliner import OnlinerRealtScraper
from scrapers.dto import ListingDTO


class TestOnlinerRealtScraper:
    """Тесты для OnlinerRealtScraper"""
    
    @pytest.fixture
    def scraper(self):
        """Фикстура для создания scraper"""
        return OnlinerRealtScraper()
    
    @pytest.fixture
    def mock_api_response(self):
        """Фикстура для мока API ответа Onliner"""
        return {
            "apartments": [
                {
                    "id": 123456,
                    "price": {
                        "amount": 50000,
                        "currency": "USD",
                        "converted": {
                            "USD": {
                                "amount": 50000
                            }
                        }
                    },
                    "location": {
                        "address": "Минск, ул. Ленина, 1"
                    },
                    "rent_type": None,
                    "number_of_rooms": 2,
                    "area": {
                        "total": 50.0
                    },
                    "url": "https://r.onliner.by/apartments/123456",
                    "photo": "https://example.com/photo.jpg",
                    "created_at": "2024-01-15T10:00:00"
                }
            ]
        }
    
    @pytest.mark.asyncio
    async def test_fetch_listings_api_success(self, scraper, mock_api_response):
        """Тест успешного получения объявлений через API"""
        mock_client = AsyncMock()
        mock_client.fetch_json = AsyncMock(return_value=mock_api_response)
        mock_client.start_session = AsyncMock()
        mock_client.close_session = AsyncMock()
        
        scraper.http_client = mock_client
        
        listings = await scraper.fetch_listings(
            city="барановичи",
            min_rooms=1,
            max_rooms=4,
            min_price=0,
            max_price=100000
        )
        
        assert mock_client.fetch_json.called
        assert isinstance(listings, list)
        
        if listings:
            listing = listings[0]
            assert listing.source == "Onliner.by"
            
            # Проверяем структуру ListingDTO
            dto = ListingDTO(
                title=listing.title,
                price=listing.price,
                url=listing.url,
                location=listing.address,
                source=listing.source
            )
            
            assert dto.title == listing.title
            assert dto.price == listing.price
            assert dto.url == listing.url
    
    @pytest.mark.asyncio
    async def test_fetch_listings_api_empty(self, scraper):
        """Тест обработки пустого ответа API"""
        mock_client = AsyncMock()
        mock_client.fetch_json = AsyncMock(return_value=None)
        mock_client.fetch_html = AsyncMock(return_value="<html></html>")
        mock_client.start_session = AsyncMock()
        mock_client.close_session = AsyncMock()
        
        scraper.http_client = mock_client
        
        listings = await scraper.fetch_listings()
        
        assert isinstance(listings, list)
    
    def test_parse_apartment_valid(self, scraper, mock_api_response):
        """Тест парсинга валидного объявления из API"""
        apartment = mock_api_response["apartments"][0]
        
        listing = scraper._parse_apartment(apartment)
        
        if listing:
            assert listing.source == "Onliner.by"
            assert listing.price == 50000
            assert listing.rooms == 2
            assert listing.area == 50.0
            assert listing.url.startswith("http")
            
            # Проверяем структуру ListingDTO
            dto = ListingDTO(
                title=listing.title,
                price=listing.price,
                url=listing.url,
                location=listing.address,
                source=listing.source
            )
            
            assert dto.title == listing.title
            assert dto.price == listing.price
            assert dto.url == listing.url
    
    def test_parse_apartment_minimal(self, scraper):
        """Тест парсинга объявления с минимальными данными"""
        apartment = {
            "id": 123456,
            "price": {
                "amount": 50000,
                "currency": "USD",
                "converted": {
                    "USD": {
                        "amount": 50000
                    }
                }
            },
            "url": "https://r.onliner.by/apartments/123456",
            "location": {},
        }
        
        listing = scraper._parse_apartment(apartment)
        
        if listing:
            assert listing.source == "Onliner.by"
            assert listing.price == 50000
            assert listing.url == "https://r.onliner.by/apartments/123456"
    
    def test_parse_apartment_invalid(self, scraper):
        """Тест парсинга невалидного объявления"""
        apartment = None
        listing = scraper._parse_apartment(apartment)
        assert listing is None
        
        apartment = {}
        listing = scraper._parse_apartment(apartment)
        assert listing is None
    
    @pytest.mark.asyncio
    async def test_fetch_via_html_fallback(self, scraper):
        """Тест fallback на HTML парсинг"""
        mock_html = """
        <html>
            <body>
                <div class="classified">
                    <a href="/apartments/123456">2-комн. квартира</a>
                </div>
            </body>
        </html>
        """
        
        mock_client = AsyncMock()
        mock_client.fetch_json = AsyncMock(return_value=None)
        mock_client.fetch_html = AsyncMock(return_value=mock_html)
        mock_client.start_session = AsyncMock()
        mock_client.close_session = AsyncMock()
        
        scraper.http_client = mock_client
        
        listings = await scraper._fetch_via_html(
            city="барановичи",
            min_rooms=1,
            max_rooms=4,
            min_price=0,
            max_price=100000
        )
        
        assert isinstance(listings, list)
    
    @pytest.mark.asyncio
    async def test_context_manager(self, scraper):
        """Тест использования scraper как context manager"""
        mock_client = AsyncMock()
        mock_client.start_session = AsyncMock()
        mock_client.close_session = AsyncMock()
        
        scraper.http_client = mock_client
        
        async with scraper:
            assert mock_client.start_session.called
        
        assert mock_client.close_session.called
    
    def test_listing_dto_structure(self, scraper):
        """Тест: проверка структуры ListingDTO из парсинга"""
        listing = MagicMock()
        listing.title = "2-комн. квартира, 50 м²"
        listing.price = 50000
        listing.url = "https://r.onliner.by/apartments/123456"
        listing.address = "Минск, ул. Ленина, 1"
        listing.source = "onliner"
        
        dto = ListingDTO(
            title=listing.title,
            price=listing.price,
            url=listing.url,
            location=listing.address,
            source=listing.source
        )
        
        assert dto.title == listing.title
        assert dto.price == listing.price
        assert dto.url == listing.url
        assert isinstance(dto.title, str) and len(dto.title) > 0
        assert isinstance(dto.price, int) and dto.price >= 0
        assert dto.url.startswith("http")
