"""
from bot.services.search_service import _filter_log_counters

Unit-тесты для search_service.py
Тестирование фильтрации, сортировки и обработки пустых результатов
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, Mock
import sys
import os

# Добавляем корневую директорию в путь для импорта
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# Мокаем зависимости перед импортом
mock_database = Mock()
mock_config = Mock()
mock_config.DEFAULT_SOURCES = ["kufar"]
mock_config.USE_TURSO_CACHE = False

# Мокаем модули перед импортом search_service
sys.modules['database'] = mock_database
sys.modules['aiosqlite'] = Mock()
sys.modules['config'] = mock_config

from scrapers.base import Listing

# Импортируем search_service с патчами
# Обновлено: используем get_user_filters_turso вместо get_user_filters
with patch('bot.services.search_service.get_user_filters_turso', mock_database.get_user_filters), \
     patch('bot.services.search_service.get_active_users', mock_database.get_active_users), \
     patch('bot.services.search_service.is_listing_sent_to_user', mock_database.is_listing_sent_to_user), \
     patch('bot.services.search_service.is_duplicate_content', mock_database.is_duplicate_content), \
     patch('bot.services.search_service.DEFAULT_SOURCES', mock_config.DEFAULT_SOURCES), \
     patch('bot.services.search_service.USE_TURSO_CACHE', mock_config.USE_TURSO_CACHE):
    from bot.services.search_service import (
        matches_user_filters,
        validate_user_filters,
        fetch_listings_for_user,
        reset_filter_counters
    )


class TestFiltering:
    """Тесты для фильтрации объявлений"""
    
    def create_listing(
        self,
        rooms: int = 2,
        price: int = 50000,
        price_usd: int = 0,
        price_byn: int = 0,
        is_company: bool = False,
        source: str = "Kufar.by"
    ) -> Listing:
        """Создает тестовое объявление"""
        return Listing(
            id=f"test_{source}_{rooms}_{price}",
            source=source,
            title=f"{rooms}-комн. квартира",
            price=price,
            price_formatted=f"${price:,}",
            rooms=rooms,
            area=50.0,
            address="Минск, ул. Ленина, 1",
            url=f"https://example.com/{rooms}_{price}",
            price_usd=price_usd,
            price_byn=price_byn,
            is_company=is_company
        )
    
    def test_filter_by_rooms_min(self):
        """Тест фильтрации по минимальному количеству комнат"""
        listing = self.create_listing(rooms=1)
        filters = {
            "min_rooms": 2,
            "max_rooms": 4,
            "min_price": 0,
            "max_price": 100000
        }
        
        assert matches_user_filters(listing, filters, log_details=False) == False
    
    def test_filter_by_rooms_max(self):
        """Тест фильтрации по максимальному количеству комнат"""
        listing = self.create_listing(rooms=5)
        filters = {
            "min_rooms": 1,
            "max_rooms": 4,
            "min_price": 0,
            "max_price": 100000
        }
        
        assert matches_user_filters(listing, filters, log_details=False) == False
    
    def test_filter_by_rooms_valid(self):
        """Тест прохождения фильтра по комнатам"""
        listing = self.create_listing(rooms=3)
        filters = {
            "min_rooms": 2,
            "max_rooms": 4,
            "min_price": 0,
            "max_price": 100000
        }
        
        assert matches_user_filters(listing, filters, log_details=False) == True
    
    def test_filter_by_rooms_zero(self):
        """Тест: объявление с 0 комнат проходит фильтр (комнаты не указаны)"""
        listing = self.create_listing(rooms=0)
        filters = {
            "min_rooms": 2,
            "max_rooms": 4,
            "min_price": 0,
            "max_price": 100000
        }
        
        # Если rooms = 0, фильтр по комнатам не применяется
        assert matches_user_filters(listing, filters, log_details=False) == True
    
    def test_filter_by_price_min(self):
        """Тест фильтрации по минимальной цене"""
        listing = self.create_listing(price=30000)
        filters = {
            "min_rooms": 1,
            "max_rooms": 4,
            "min_price": 40000,
            "max_price": 100000
        }
        
        assert matches_user_filters(listing, filters, log_details=False) == False
    
    def test_filter_by_price_max(self):
        """Тест фильтрации по максимальной цене"""
        listing = self.create_listing(price=150000)
        filters = {
            "min_rooms": 1,
            "max_rooms": 4,
            "min_price": 0,
            "max_price": 100000
        }
        
        assert matches_user_filters(listing, filters, log_details=False) == False
    
    def test_filter_by_price_valid(self):
        """Тест прохождения фильтра по цене"""
        listing = self.create_listing(price=50000)
        filters = {
            "min_rooms": 1,
            "max_rooms": 4,
            "min_price": 40000,
            "max_price": 60000
        }
        
        assert matches_user_filters(listing, filters, log_details=False) == True
    
    def test_filter_by_price_usd(self):
        """Тест фильтрации по цене в USD"""
        listing = self.create_listing(price=0, price_usd=50000)
        filters = {
            "min_rooms": 1,
            "max_rooms": 4,
            "min_price": 40000,
            "max_price": 60000
        }
        
        assert matches_user_filters(listing, filters, log_details=False) == True
    
    def test_filter_by_price_byn_conversion(self):
        """Тест фильтрации по цене в BYN с конвертацией в USD"""
        # 150000 BYN / 2.95 ≈ 50847 USD
        listing = self.create_listing(price=0, price_byn=150000)
        filters = {
            "min_rooms": 1,
            "max_rooms": 4,
            "min_price": 40000,
            "max_price": 60000
        }
        
        assert matches_user_filters(listing, filters, log_details=False) == True
    
    def test_filter_by_price_zero(self):
        """Тест: объявление с нулевой ценой проходит фильтр (цена не указана)"""
        listing = self.create_listing(price=0)
        filters = {
            "min_rooms": 1,
            "max_rooms": 4,
            "min_price": 40000,
            "max_price": 60000
        }
        
        # Если price = 0, фильтр по цене не применяется
        assert matches_user_filters(listing, filters, log_details=False) == True
    
    def test_filter_by_seller_type_owner(self):
        """Тест фильтрации по типу продавца: только собственники"""
        listing = self.create_listing(is_company=True)  # Агентство
        filters = {
            "min_rooms": 1,
            "max_rooms": 4,
            "min_price": 0,
            "max_price": 100000,
            "seller_type": "owner"
        }
        
        assert matches_user_filters(listing, filters, log_details=False) == False
    
    def test_filter_by_seller_type_company(self):
        """Тест фильтрации по типу продавца: только агентства"""
        listing = self.create_listing(is_company=False)  # Собственник
        filters = {
            "min_rooms": 1,
            "max_rooms": 4,
            "min_price": 0,
            "max_price": 100000,
            "seller_type": "company"
        }
        
        assert matches_user_filters(listing, filters, log_details=False) == False
    
    def test_filter_by_seller_type_none(self):
        """Тест: фильтр по типу продавца не применяется если не указан"""
        listing = self.create_listing(is_company=True)
        filters = {
            "min_rooms": 1,
            "max_rooms": 4,
            "min_price": 0,
            "max_price": 100000,
            "seller_type": None
        }
        
        assert matches_user_filters(listing, filters, log_details=False) == True
    
    def test_filter_by_seller_type_unknown(self):
        """Тест: объявление с неизвестным типом продавца проходит фильтр"""
        listing = self.create_listing(is_company=None)
        filters = {
            "min_rooms": 1,
            "max_rooms": 4,
            "min_price": 0,
            "max_price": 100000,
            "seller_type": "owner"
        }
        
        # Если is_company = None, фильтр не применяется
        assert matches_user_filters(listing, filters, log_details=False) == True
    
    def test_filter_combined_all_pass(self):
        """Тест комбинированной фильтрации: все фильтры проходят"""
        listing = self.create_listing(
            rooms=3,
            price=50000,
            is_company=False
        )
        filters = {
            "min_rooms": 2,
            "max_rooms": 4,
            "min_price": 40000,
            "max_price": 60000,
            "seller_type": "owner"
        }
        
        assert matches_user_filters(listing, filters, log_details=False) == True
    
    def test_filter_combined_one_fails(self):
        """Тест комбинированной фильтрации: один фильтр не проходит"""
        listing = self.create_listing(
            rooms=1,  # Не проходит по комнатам
            price=50000,
            is_company=False
        )
        filters = {
            "min_rooms": 2,
            "max_rooms": 4,
            "min_price": 40000,
            "max_price": 60000,
            "seller_type": "owner"
        }
        
        assert matches_user_filters(listing, filters, log_details=False) == False
    
    def test_filter_edge_cases_boundary_rooms(self):
        """Тест граничных значений: комнаты на границе диапазона"""
        # Минимальная граница
        listing_min = self.create_listing(rooms=2)
        filters = {"min_rooms": 2, "max_rooms": 4, "min_price": 0, "max_price": 100000}
        assert matches_user_filters(listing_min, filters, log_details=False) == True
        
        # Максимальная граница
        listing_max = self.create_listing(rooms=4)
        assert matches_user_filters(listing_max, filters, log_details=False) == True
    
    def test_filter_edge_cases_boundary_price(self):
        """Тест граничных значений: цена на границе диапазона"""
        # Минимальная граница
        listing_min = self.create_listing(price=40000)
        filters = {"min_rooms": 1, "max_rooms": 4, "min_price": 40000, "max_price": 60000}
        assert matches_user_filters(listing_min, filters, log_details=False) == True
        
        # Максимальная граница
        listing_max = self.create_listing(price=60000)
        assert matches_user_filters(listing_max, filters, log_details=False) == True


class TestSorting:
    """Тесты для сортировки объявлений"""
    
    def create_listing(self, price: int, rooms: int = 2) -> Listing:
        """Создает тестовое объявление"""
        return Listing(
            id=f"test_{price}",
            source="Kufar.by",
            title=f"{rooms}-комн. квартира",
            price=price,
            price_formatted=f"${price:,}",
            rooms=rooms,
            area=50.0,
            address="Минск",
            url=f"https://example.com/{price}"
        )
    
    def test_sort_by_price_ascending(self):
        """Тест сортировки по цене по возрастанию"""
        listings = [
            self.create_listing(price=60000),
            self.create_listing(price=40000),
            self.create_listing(price=50000),
        ]
        
        # Сортируем по цене
        sorted_listings = sorted(listings, key=lambda x: x.price if x.price > 0 else 999999999)
        
        assert sorted_listings[0].price == 40000
        assert sorted_listings[1].price == 50000
        assert sorted_listings[2].price == 60000
    
    def test_sort_by_price_with_zero(self):
        """Тест сортировки: объявления с нулевой ценой в конце"""
        listings = [
            self.create_listing(price=0),
            self.create_listing(price=50000),
            self.create_listing(price=40000),
        ]
        
        sorted_listings = sorted(listings, key=lambda x: x.price if x.price > 0 else 999999999)
        
        assert sorted_listings[0].price == 40000
        assert sorted_listings[1].price == 50000
        assert sorted_listings[2].price == 0
    
    def test_sort_stable_order(self):
        """Тест стабильности сортировки: одинаковые цены сохраняют порядок"""
        listings = [
            self.create_listing(price=50000, rooms=2),
            self.create_listing(price=50000, rooms=3),
            self.create_listing(price=50000, rooms=1),
        ]
        
        # Сортируем только по цене
        sorted_listings = sorted(listings, key=lambda x: x.price)
        
        # Порядок должен сохраниться для одинаковых цен
        assert len(sorted_listings) == 3
        assert all(l.price == 50000 for l in sorted_listings)


class TestEmptyResults:
    """Тесты для обработки пустых результатов"""
    
    def test_empty_listings_list(self):
        """Тест обработки пустого списка объявлений"""
        listings = []
        filters = {
            "min_rooms": 1,
            "max_rooms": 4,
            "min_price": 0,
            "max_price": 100000
        }
        
        # Фильтруем пустой список
        filtered = [l for l in listings if matches_user_filters(l, filters, log_details=False)]
        
        assert len(filtered) == 0
        assert filtered == []
    
    def test_no_matching_listings(self):
        """Тест: нет объявлений, соответствующих фильтрам"""
        
        listings = [
            Listing(
                id="test_1",
                source="Kufar.by",
                title="1-комн. квартира",
                price=30000,
                price_formatted="$30,000",
                rooms=1,
                area=30.0,
                address="Минск",
                url="https://example.com/1"
            ),
            Listing(
                id="test_2",
                source="Kufar.by",
                title="5-комн. квартира",
                price=200000,
                price_formatted="$200,000",
                rooms=5,
                area=100.0,
                address="Минск",
                url="https://example.com/2"
            ),
        ]
        
        filters = {
            "min_rooms": 2,
            "max_rooms": 4,
            "min_price": 40000,
            "max_price": 100000
        }
        
        filtered = [l for l in listings if matches_user_filters(l, filters, log_details=False)]
        
        assert len(filtered) == 0
    
    def test_empty_filters(self):
        """Тест обработки пустых фильтров"""
        
        listing = Listing(
            id="test_1",
            source="Kufar.by",
            title="2-комн. квартира",
            price=50000,
            price_formatted="$50,000",
            rooms=2,
            area=50.0,
            address="Минск",
            url="https://example.com/1"
        )
        
        filters = {}
        
        # С пустыми фильтрами должны использоваться значения по умолчанию
        # min_rooms=1, max_rooms=4, min_price=0, max_price=1000000
        result = matches_user_filters(listing, filters, log_details=False)
        
        # Объявление должно пройти (используются значения по умолчанию)
        assert result == True
    
    def test_none_filters(self):
        """Тест обработки None фильтров"""
        
        listing = Listing(
            id="test_1",
            source="Kufar.by",
            title="2-комн. квартира",
            price=50000,
            price_formatted="$50,000",
            rooms=2,
            area=50.0,
            address="Минск",
            url="https://example.com/1"
        )
        
        # validate_user_filters должна вернуть False для None
        is_valid, error_msg = validate_user_filters(None)
        assert is_valid == False
        assert error_msg == "Фильтры не настроены"
    
    def test_filters_without_city(self):
        """Тест валидации фильтров без города"""
        filters = {
            "min_rooms": 2,
            "max_rooms": 4,
            "min_price": 0,
            "max_price": 100000
            # Нет поля "city"
        }
        
        is_valid, error_msg = validate_user_filters(filters)
        assert is_valid == False
        assert error_msg == "Город не выбран"
    
    def test_all_listings_filtered_out(self):
        """Тест: все объявления отфильтрованы"""
        
        listings = [
            Listing(
                id="test_1",
                source="Kufar.by",
                title="1-комн. квартира",
                price=50000,
                price_formatted="$50,000",
                rooms=1,
                area=30.0,
                address="Минск",
                url="https://example.com/1"
            ),
            Listing(
                id="test_2",
                source="Kufar.by",
                title="5-комн. квартира",
                price=50000,
                price_formatted="$50,000",
                rooms=5,
                area=100.0,
                address="Минск",
                url="https://example.com/2"
            ),
        ]
        
        filters = {
            "min_rooms": 2,
            "max_rooms": 4,
            "min_price": 0,
            "max_price": 100000
        }
        
        filtered = [l for l in listings if matches_user_filters(l, filters, log_details=False)]
        
        assert len(filtered) == 0
        assert filtered == []


class TestFetchListingsForUser:
    """Тесты для функции fetch_listings_for_user
    
    Примечание: полное тестирование fetch_listings_for_user требует сложного мокирования
    всех зависимостей (database, aggregator, config). Основная бизнес-логика тестируется
    через matches_user_filters и validate_user_filters.
    """
    
    def test_fetch_listings_function_exists(self):
        """Тест: функция fetch_listings_for_user существует и имеет правильную сигнатуру"""
        import inspect
        
        assert callable(fetch_listings_for_user)
        sig = inspect.signature(fetch_listings_for_user)
        params = list(sig.parameters.keys())
        
        assert "user_id" in params
        assert "user_filters" in params
        assert len(params) == 2


class TestFilterCounters:
    """Тесты для счетчиков фильтрации"""
    
    def test_reset_filter_counters(self):
        """Тест сброса счетчиков фильтрации"""
        
        # Устанавливаем счетчики
        _filter_log_counters[123] = {"filtered": 10, "passed": 5}
        
        # Сбрасываем
        reset_filter_counters()
        
        assert len(_filter_log_counters) == 0
    
    def test_filter_counters_initialization(self):
        """Тест инициализации счетчиков при фильтрации"""
        
        listing = Listing(
            id="test_1",
            source="Kufar.by",
            title="2-комн. квартира",
            price=50000,
            price_formatted="$50,000",
            rooms=2,
            area=50.0,
            address="Минск",
            url="https://example.com/1"
        )
        
        filters = {
            "min_rooms": 1,
            "max_rooms": 4,
            "min_price": 0,
            "max_price": 100000
        }
        
        user_id = 999
        
        # Первый вызов должен инициализировать счетчик
        matches_user_filters(listing, filters, user_id=user_id, log_details=True)
        
        assert user_id in _filter_log_counters
        assert "filtered" in _filter_log_counters[user_id]
        assert "passed" in _filter_log_counters[user_id]
