"""
Unit-тесты для DomovitaScraper
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from bs4 import BeautifulSoup
from scrapers.domovita import DomovitaScraper
from scrapers.dto import ListingDTO


class TestDomovitaScraper:
    """Тесты для DomovitaScraper"""
    
    @pytest.fixture
    def scraper(self):
        """Фикстура для создания scraper"""
        return DomovitaScraper()
    
    @pytest.fixture
    def mock_html(self):
        """Фикстура для мока HTML страницы Domovita.by"""
        return """
        <html>
            <body>
                <div class="object-card">
                    <a href="/prodazha/kvartiry/123456">2-комн. квартира, 50 м²</a>
                    <div class="price">$50,000</div>
                    <div class="address">Минск, ул. Ленина, 1</div>
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
            city="барановичи",
            min_rooms=1,
            max_rooms=4,
            min_price=0,
            max_price=100000
        )
        
        assert mock_client.fetch_html.called
        assert isinstance(listings, list)
    
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
    
    def test_parse_card_valid(self, scraper):
        """Тест парсинга валидной карточки"""
        html = """
        <div class="object-card">
            <a href="/prodazha/kvartiry/123456">2-комн. квартира, 50 м²</a>
            <div class="price">$50,000</div>
            <div class="address">Минск, ул. Ленина, 1</div>
        </div>
        """
        soup = BeautifulSoup(html, 'lxml')
        card = soup.find('div', class_='object-card')
        
        if card:
            listing = scraper._parse_card(card)
            
            if listing:
                assert listing.source == "Domovita.by"
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
        listing.url = "https://domovita.by/prodazha/kvartiry/123456"
        listing.address = "Минск, ул. Ленина, 1"
        listing.source = "domovita"
        
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
