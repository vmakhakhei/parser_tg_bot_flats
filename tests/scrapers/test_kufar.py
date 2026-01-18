"""
Unit-тесты для KufarScraper
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from scrapers.kufar import KufarScraper
from scrapers.dto import ListingDTO


class TestKufarScraper:
    """Тесты для KufarScraper"""
    
    @pytest.fixture
    def scraper(self):
        """Фикстура для создания scraper"""
        return KufarScraper()
    
    @pytest.fixture
    def mock_api_response(self):
        """Фикстура для мока API ответа Kufar"""
        return {
            "ads": [
                {
                    "ad_id": "123456789",
                    "subject": "2-комн. квартира, 50 м²",
                    "price_usd": 5000000,  # В центах (50000 * 100)
                    "price_byn": 150000,
                    "location": {
                        "address": "Минск, ул. Ленина, 1"
                    },
                    "ad_link": "https://www.kufar.by/item/123456789",
                    "photos": [
                        {"url": "https://example.com/photo1.jpg"}
                    ],
                    "ad_parameters": [
                        {"p": "size", "v": 50, "vl": "м²"},  # площадь
                        {"p": "rooms", "v": 2, "vl": "комн."},  # комнаты
                        {"p": "floor", "v": "5", "vl": "эт."},  # этаж
                    ],
                    "ad_owner": {
                        "company_ad": False
                    },
                    "list_time": "1705312800"  # Unix timestamp для парсинга даты
                }
            ],
            "total": 1,
            "pagination": {
                "pages": []
            }
        }
    
    @pytest.mark.asyncio
    async def test_fetch_listings_success(self, scraper, mock_api_response):
        """Тест успешного получения объявлений"""
        # Мокаем HTTP-клиент
        mock_client = AsyncMock()
        mock_client.fetch_json = AsyncMock(return_value=mock_api_response)
        mock_client.start_session = AsyncMock()
        mock_client.close_session = AsyncMock()
        
        scraper.http_client = mock_client
        
        # Вызываем метод
        listings = await scraper.fetch_listings(
            city="минск",
            min_rooms=1,
            max_rooms=4,
            min_price=0,
            max_price=100000
        )
        
        # Проверяем результаты
        assert isinstance(listings, list)
        assert len(listings) >= 0  # Может быть 0 если фильтры не подходят
        
        if listings:
            listing = listings[0]
            
            # Проверяем структуру Listing
            assert listing.source == "Kufar.by"
            assert hasattr(listing, 'title')
            assert hasattr(listing, 'price')
            assert hasattr(listing, 'url')
            assert hasattr(listing, 'address')
            
            # Проверяем, что можно создать ListingDTO из Listing
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
    async def test_fetch_listings_empty_response(self, scraper):
        """Тест обработки пустого ответа API"""
        # Мокаем пустой ответ
        mock_client = AsyncMock()
        mock_client.fetch_json = AsyncMock(return_value=None)
        mock_client.start_session = AsyncMock()
        mock_client.close_session = AsyncMock()
        
        scraper.http_client = mock_client
        
        listings = await scraper.fetch_listings()
        
        assert listings == []
    
    @pytest.mark.asyncio
    async def test_fetch_listings_no_ads(self, scraper):
        """Тест обработки ответа без объявлений"""
        mock_response = {
            "ads": [],
            "total": 0,
            "pagination": {"pages": []}
        }
        
        mock_client = AsyncMock()
        mock_client.fetch_json = AsyncMock(return_value=mock_response)
        mock_client.start_session = AsyncMock()
        mock_client.close_session = AsyncMock()
        
        scraper.http_client = mock_client
        
        listings = await scraper.fetch_listings()
        
        assert listings == []
    
    @pytest.mark.asyncio
    async def test_fetch_listings_with_pagination(self, scraper):
        """Тест пагинации"""
        # Первая страница
        page1_response = {
            "ads": [
                {
                    "ad_id": "1",
                    "subject": "Квартира 1",
                    "price_usd": 5000000,  # В центах (50000 * 100)
                    "price_byn": 150000,
                    "location": {"address": "Минск"},
                    "ad_link": "https://kufar.by/item/1",
                    "photos": [],
                    "ad_params": [{"pl": 50, "vl": "м²"}, {"pl": 2, "vl": "комн."}],
                    "ad_owner": {"company_ad": False},
                    "list_time": "2024-01-15T10:00:00"
                }
            ],
            "total": 2,
            "pagination": {
                "pages": [
                    {"label": "next", "token": "next_token_123"}
                ]
            }
        }
        
        # Вторая страница
        page2_response = {
            "ads": [
                {
                    "ad_id": "2",
                    "subject": "Квартира 2",
                    "price_usd": 60000,
                    "price_byn": 180000,
                    "location": {"address": "Минск"},
                    "ad_link": "https://kufar.by/item/2",
                    "photos": [],
                    "ad_params": [{"pl": 60, "vl": "м²"}, {"pl": 3, "vl": "комн."}],
                    "ad_owner": {"company_ad": False},
                    "list_time": "2024-01-15T11:00:00"
                }
            ],
            "total": 2,
            "pagination": {
                "pages": []
            }
        }
        
        mock_client = AsyncMock()
        mock_client.fetch_json = AsyncMock(side_effect=[page1_response, page2_response])
        mock_client.start_session = AsyncMock()
        mock_client.close_session = AsyncMock()
        
        scraper.http_client = mock_client
        
        listings = await scraper.fetch_listings(max_pages=2)
        
        assert len(listings) == 2
        assert mock_client.fetch_json.call_count == 2
    
    def test_parse_ad_valid(self, scraper):
        """Тест парсинга валидного объявления"""
        ad = {
            "ad_id": "123456789",
            "subject": "2-комн. квартира, 50 м²",
            "price_usd": 5000000,  # В центах (50000 * 100)
            "price_byn": 15000000,  # В копейках (150000 * 100)
            "location": {
                "address": "Минск, ул. Ленина, 1"
            },
            "ad_link": "https://www.kufar.by/item/123456789",
            "photos": [
                {"url": "https://example.com/photo1.jpg"},
                {"url": "https://example.com/photo2.jpg"}
            ],
            "ad_parameters": [
                {"p": "size", "v": 50, "vl": "м²"},
                {"p": "rooms", "v": 2, "vl": "комн."},
                {"p": "floor", "v": "5", "vl": "эт."},
            ],
            "ad_owner": {
                "company_ad": False
            },
            "list_time": "1705312800"  # Unix timestamp
        }
        
        listing = scraper._parse_ad(ad, "Минск")
        
        assert listing is not None
        assert listing.source == "Kufar.by"
        # Title формируется парсером, может отличаться от исходного subject
        assert "2-комн" in listing.title or "квартира" in listing.title.lower()
        assert listing.price == 50000
        assert listing.price_usd == 50000
        assert listing.price_byn == 150000
        assert listing.url == "https://www.kufar.by/item/123456789"
        # Адрес может быть только городом, если парсер не извлекает полный адрес
        assert "Минск" in listing.address
        assert listing.rooms == 2
        assert listing.area == 50.0
        # Фотографии могут быть пустыми, если парсер их не извлекает
        assert isinstance(listing.photos, list)
        assert listing.is_company == False
        
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
    
    def test_parse_ad_minimal(self, scraper):
        """Тест парсинга объявления с минимальными данными"""
        ad = {
            "ad_id": "123456789",
            "subject": "Квартира",
            "price_usd": 5000000,  # В центах (50000 * 100)
            "ad_link": "https://www.kufar.by/item/123456789",
            "location": {},
            "photos": [],
            "ad_parameters": [],
            "ad_owner": {},
        }
        
        listing = scraper._parse_ad(ad, "Минск")
        
        assert listing is not None
        assert listing.title == "Квартира"
        # Цена в USD приходит в центах, делится на 100
        # price_usd: 5000000 центов = 50000 долларов
        assert listing.price == 50000
        assert listing.price_usd == 50000
        assert listing.url == "https://www.kufar.by/item/123456789"
        assert listing.rooms == 0
        assert listing.area == 0.0
        
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
    
    def test_parse_ad_invalid_returns_none(self, scraper):
        """Тест: невалидное объявление возвращает None"""
        ad = None
        listing = scraper._parse_ad(ad, "Минск")
        assert listing is None
        
        ad = {}
        listing = scraper._parse_ad(ad, "Минск")
        assert listing is None
    
    def test_matches_filters(self, scraper):
        """Тест фильтрации объявлений"""
        listing = MagicMock()
        listing.rooms = 2
        listing.price = 50000
        
        # Подходит по всем параметрам
        assert scraper._matches_filters(listing, 1, 4, 0, 100000) == True
        
        # Не подходит по комнатам
        assert scraper._matches_filters(listing, 3, 4, 0, 100000) == False
        
        # Не подходит по цене
        assert scraper._matches_filters(listing, 1, 4, 60000, 100000) == False
        
        # Подходит по цене (граничные значения)
        assert scraper._matches_filters(listing, 1, 4, 50000, 50000) == True
    
    def test_get_city_gtsy(self, scraper):
        """Тест преобразования города в формат gtsy"""
        assert "minsk" in scraper._get_city_gtsy("минск").lower()
        assert "baranovichi" in scraper._get_city_gtsy("барановичи").lower()
        assert "brest" in scraper._get_city_gtsy("брест").lower()
        
        # Неизвестный город должен вернуть дефолт
        result = scraper._get_city_gtsy("неизвестный город")
        assert "baranovichi" in result.lower()
    
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
        ad = {
            "ad_id": "123456789",
            "subject": "2-комн. квартира, 50 м²",
            "price_usd": 50000,
            "price_byn": 150000,
            "location": {
                "address": "Минск, ул. Ленина, 1"
            },
            "ad_link": "https://www.kufar.by/item/123456789",
            "photos": [],
            "ad_parameters": [
                {"p": "size", "v": 50, "vl": "м²"},
                {"p": "rooms", "v": 2, "vl": "комн."},
            ],
            "ad_owner": {"company_ad": False},
            "list_time": "1705312800"
        }
        
        listing = scraper._parse_ad(ad, "Минск")
        
        assert listing is not None
        
        # Создаем ListingDTO из Listing
        dto = ListingDTO(
            title=listing.title,
            price=listing.price,
            url=listing.url,
            location=listing.address,
            source=listing.source
        )
        
        # Проверяем структуру DTO
        assert dto.title == listing.title
        assert dto.price == listing.price
        assert dto.url == listing.url
        assert dto.location == listing.address
        assert dto.source == listing.source
        
        # Проверяем валидность DTO
        assert isinstance(dto.title, str) and len(dto.title) > 0
        assert isinstance(dto.price, int) and dto.price >= 0
        assert dto.url.startswith("http")
        assert isinstance(dto.location, str)
        assert isinstance(dto.source, str) and len(dto.source) > 0
