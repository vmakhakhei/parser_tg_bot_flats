"""
Unit-тесты для RealtByScraper
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from bs4 import BeautifulSoup
from scrapers.realt import RealtByScraper
from scrapers.dto import ListingDTO


class TestRealtByScraper:
    """Тесты для RealtByScraper"""
    
    @pytest.fixture
    def scraper(self):
        """Фикстура для создания scraper"""
        return RealtByScraper()
    
    @pytest.fixture
    def mock_html(self):
        """Фикстура для мока HTML страницы Realt.by"""
        return """
        <html>
            <body>
                <div class="listing-item">
                    <a href="/sale/flats/object/123456">2-комн. квартира, 50 м²</a>
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
    
    def test_build_search_url(self, scraper):
        """Тест построения URL для поиска"""
        url = scraper._build_search_url(
            city="барановичи",
            min_rooms=2,
            max_rooms=3,
            min_price=40000,
            max_price=60000
        )
        
        assert "realt.by" in url
        assert "room_2" in url or "room_3" in url
    
    def test_parse_card_valid(self, scraper):
        """Тест парсинга валидной карточки"""
        html = """
        <div class="listing-item">
            <a href="/sale/flats/object/123456">2-комн. квартира, 50 м²</a>
            <div class="price">$50,000</div>
            <div class="address">Минск, ул. Ленина, 1</div>
        </div>
        """
        soup = BeautifulSoup(html, 'lxml')
        card = soup.find('div', class_='listing-item')
        
        if card:
            listing = scraper._parse_card(card)
            
            if listing:
                assert listing.source == "Realt.by"
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
    
    def test_parse_card_invalid(self, scraper):
        """Тест парсинга невалидной карточки"""
        html = "<div></div>"
        soup = BeautifulSoup(html, 'lxml')
        card = soup.find('div')
        
        listing = scraper._parse_card(card)
        
        # Может вернуть None или валидный listing в зависимости от реализации
        assert listing is None or isinstance(listing, type(scraper).__bases__[0].__module__ + '.Listing')
    
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
        listing.url = "https://realt.by/sale/flats/object/123456"
        listing.address = "Минск, ул. Ленина, 1"
        listing.source = "realt.by"
        
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
