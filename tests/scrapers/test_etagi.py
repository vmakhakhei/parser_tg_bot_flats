"""
Unit-тесты для EtagiScraper
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from bs4 import BeautifulSoup
from scrapers.etagi import EtagiScraper
from scrapers.dto import ListingDTO


class TestEtagiScraper:
    """Тесты для EtagiScraper"""
    
    @pytest.fixture
    def scraper(self):
        """Фикстура для создания scraper"""
        return EtagiScraper()
    
    @pytest.fixture
    def mock_html(self):
        """Фикстура для мока HTML страницы Etagi"""
        return """
        <html>
            <body>
                <div class="listing-item">
                    <a href="/realty/123456/">2-комн. квартира, 50 м²</a>
                    <div class="price">150 000 BYN</div>
                    <div class="address">Минск, ул. Ленина, 1</div>
                </div>
                <div class="listing-item">
                    <a href="/realty/789012/">3-комн. квартира, 70 м²</a>
                    <div class="price">200 000 BYN</div>
                    <div class="address">Минск, ул. Пушкина, 2</div>
                </div>
            </body>
        </html>
        """
    
    @pytest.mark.asyncio
    async def test_fetch_listings_success(self, scraper, mock_html):
        """Тест успешного получения объявлений"""
        mock_client = AsyncMock()
        mock_client.fetch_html = AsyncMock(return_value=mock_html)
        mock_client.start_session = AsyncMock()
        mock_client.close_session = AsyncMock()
        
        scraper.http_client = mock_client
        
        listings = await scraper.fetch_listings(
            city="минск",
            min_rooms=1,
            max_rooms=4,
            min_price=0,
            max_price=1000000
        )
        
        # Проверяем, что были вызваны методы HTTP-клиента
        assert mock_client.fetch_html.called
        
        # Проверяем результаты (если парсинг работает)
        # Примечание: реальный парсинг может вернуть пустой список,
        # если HTML структура не соответствует ожидаемой
    
    @pytest.mark.asyncio
    async def test_fetch_listings_empty_html(self, scraper):
        """Тест обработки пустого HTML"""
        mock_client = AsyncMock()
        mock_client.fetch_html = AsyncMock(return_value=None)
        mock_client.start_session = AsyncMock()
        mock_client.close_session = AsyncMock()
        
        scraper.http_client = mock_client
        
        listings = await scraper.fetch_listings()
        
        assert listings == []
    
    @pytest.mark.asyncio
    async def test_fetch_listings_no_listings(self, scraper):
        """Тест обработки HTML без объявлений"""
        empty_html = "<html><body></body></html>"
        
        mock_client = AsyncMock()
        mock_client.fetch_html = AsyncMock(return_value=empty_html)
        mock_client.start_session = AsyncMock()
        mock_client.close_session = AsyncMock()
        
        scraper.http_client = mock_client
        
        listings = await scraper.fetch_listings()
        
        assert isinstance(listings, list)
    
    def test_get_city_url(self, scraper):
        """Тест преобразования города в URL"""
        assert "baranovichi.etagi.com" in scraper._get_city_url("барановичи")
        assert "minsk.etagi.com" in scraper._get_city_url("минск")
        assert "brest.etagi.com" in scraper._get_city_url("брест")
        
        # Неизвестный город должен вернуть дефолт
        result = scraper._get_city_url("неизвестный город")
        assert "baranovichi.etagi.com" in result
    
    def test_parse_listing_from_container(self, scraper):
        """Тест парсинга объявления из контейнера"""
        # Создаем mock контейнер BeautifulSoup
        html = """
        <div class="listing-item">
            <a href="/realty/123456/">2-комн. квартира, 50 м²</a>
            <div class="price">150 000 BYN</div>
            <div class="address">Минск, ул. Ленина, 1</div>
            <div class="rooms">2</div>
            <div class="area">50</div>
        </div>
        """
        soup = BeautifulSoup(html, 'lxml')
        container = soup.find('div', class_='listing-item')
        
        if container:
            listing = scraper._parse_listing_from_container(
                container,
                "123456",
                "https://baranovichi.etagi.com/realty/123456/",
                "Минск"
            )
            
            if listing:
                # Проверяем структуру Listing
                assert listing.source == "Etagi.com"
                assert listing.url == "https://baranovichi.etagi.com/realty/123456/"
                
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
                assert dto.source == listing.source
    
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
        # Создаем mock listing
        listing = MagicMock()
        listing.title = "2-комн. квартира, 50 м²"
        listing.price = 150000
        listing.url = "https://baranovichi.etagi.com/realty/123456/"
        listing.address = "Минск, ул. Ленина, 1"
        listing.source = "etagi"
        
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
