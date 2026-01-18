"""
Базовый класс для всех парсеров недвижимости
"""
import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
import hashlib

# Импортируем унифицированный HTTP-клиент
from scrapers.http_client import HTTPClient, get_http_client
# Импортируем DTO для валидации
from scrapers.dto import ListingDTO


@dataclass
class Listing:
    """Объявление о продаже квартиры"""
    id: str
    source: str  # Источник (kufar, realt, domovita, etc.)
    title: str
    price: int  # Цена в основной валюте
    price_formatted: str
    rooms: int
    area: float
    address: str
    url: str
    photos: List[str] = field(default_factory=list)
    floor: str = ""
    description: str = ""
    currency: str = "USD"  # Валюта: USD, BYN
    price_byn: int = 0  # Цена в BYN (если есть)
    price_usd: int = 0  # Цена в USD (если есть)
    price_per_sqm: int = 0  # Цена за м² (в основной валюте)
    price_per_sqm_formatted: str = ""  # Форматированная цена за м²
    year_built: str = ""  # Год постройки
    created_at: str = ""  # Дата создания объявления (формат: YYYY-MM-DD или "сегодня", "вчера", "X дней назад")
    is_company: Optional[bool] = None  # True = агентство, False = собственник, None = неизвестно
    balcony: str = ""  # Наличие балкона/лоджии
    bathroom: str = ""  # Тип санузла (раздельный/совмещенный)
    total_floors: str = ""  # Этажность дома
    house_type: str = ""  # Тип дома (кирпичный/панельный/монолитный)
    renovation_state: str = ""  # Состояние ремонта
    kitchen_area: float = 0.0  # Площадь кухни в м²
    living_area: float = 0.0  # Жилая площадь в м²
    
    def __post_init__(self):
        """Вычисляем цену за м² если не указана"""
        if self.price_per_sqm == 0 and self.price > 0 and self.area > 0:
            self.price_per_sqm = int(self.price / self.area)
            symbol = "$" if self.currency == "USD" else "BYN"
            self.price_per_sqm_formatted = f"{self.price_per_sqm:,} {symbol}/м²".replace(",", " ")
    
    def to_dict(self) -> Dict[str, Any]:
        """Конвертация в словарь"""
        return {
            "id": self.id,
            "source": self.source,
            "title": self.title,
            "price": self.price,
            "price_formatted": self.price_formatted,
            "rooms": self.rooms,
            "area": self.area,
            "address": self.address,
            "url": self.url,
            "photos": self.photos,
            "floor": self.floor,
            "description": self.description,
            "currency": self.currency,
            "price_byn": self.price_byn,
            "price_usd": self.price_usd,
            "price_per_sqm": self.price_per_sqm,
            "year_built": self.year_built,
            "created_at": self.created_at,
            "is_company": self.is_company,
            "balcony": self.balcony,
            "bathroom": self.bathroom,
            "total_floors": self.total_floors,
            "house_type": self.house_type,
            "renovation_state": self.renovation_state,
            "kitchen_area": self.kitchen_area,
            "living_area": self.living_area,
        }
    
    @staticmethod
    def generate_id(source: str, unique_data: str) -> str:
        """Генерирует уникальный ID для объявления"""
        data = f"{source}:{unique_data}"
        return hashlib.md5(data.encode()).hexdigest()[:16]


class BaseScraper(ABC):
    """Базовый класс парсера"""
    
    SOURCE_NAME = "base"
    BASE_URL = ""
    
    def __init__(self):
        """Инициализация парсера с унифицированным HTTP-клиентом"""
        self.http_client: Optional[HTTPClient] = None
        
    async def __aenter__(self):
        """Context manager entry - инициализирует HTTP-клиент"""
        await self.start_session()
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - закрывает HTTP-клиент"""
        await self.close_session()
    
    async def start_session(self):
        """Создает HTTP-клиент"""
        if self.http_client is None:
            self.http_client = get_http_client()
        await self.http_client.start_session()
    
    async def close_session(self):
        """Закрывает HTTP-клиент"""
        if self.http_client:
            await self.http_client.close_session()
    
    async def _fetch_html(self, url: str, headers: Optional[Dict[str, str]] = None) -> Optional[str]:
        """
        Получает HTML страницы через унифицированный HTTP-клиент
        
        Args:
            url: URL для запроса
            headers: Дополнительные заголовки (опционально)
        
        Returns:
            HTML текст или None при ошибке
        """
        await self.start_session()
        return await self.http_client.fetch_html(
            url=url,
            headers=headers,
            source_name=self.SOURCE_NAME
        )
    
    async def _fetch_json(self, url: str, headers: Optional[Dict[str, str]] = None, params: Optional[Dict[str, Any]] = None) -> Optional[Dict]:
        """
        Получает JSON данные через унифицированный HTTP-клиент
        
        Args:
            url: URL для запроса
            headers: Дополнительные заголовки (опционально)
            params: Параметры запроса (опционально)
        
        Returns:
            JSON данные или None при ошибке
        """
        await self.start_session()
        return await self.http_client.fetch_json(
            url=url,
            headers=headers,
            params=params,
            source_name=self.SOURCE_NAME
        )
    
    def validate_listing_data(
        self,
        title: str,
        price: int,
        url: str,
        location: str,
        source: Optional[str] = None
    ) -> Optional[ListingDTO]:
        """
        Валидирует данные объявления через ListingDTO
        
        Args:
            title: Заголовок объявления
            price: Цена
            url: URL объявления
            location: Адрес/локация
            source: Источник (по умолчанию SOURCE_NAME)
        
        Returns:
            ListingDTO если данные валидны, None иначе
        """
        source_name = source or self.SOURCE_NAME
        
        try:
            dto = ListingDTO(
                title=title,
                price=price,
                url=url,
                location=location or "",  # Пустая строка вместо None
                source=source_name
            )
            return dto
        except ValueError as e:
            # Ошибка валидации уже залогирована в ListingDTO
            return None
    
    @abstractmethod
    async def fetch_listings(
        self,
        city: str = "барановичи",
        min_rooms: int = 1,
        max_rooms: int = 4,
        min_price: int = 0,
        max_price: int = 100000,
    ) -> List[Listing]:
        """Получает список объявлений"""
        pass
    
    def _parse_price(self, price_str: str) -> int:
        """Парсит цену из строки"""
        import re
        # Удаляем все кроме цифр
        numbers = re.findall(r'\d+', price_str.replace(" ", "").replace(",", ""))
        if numbers:
            return int("".join(numbers))
        return 0
    
    def _parse_area(self, area_str: str) -> float:
        """Парсит площадь из строки"""
        import re
        match = re.search(r'(\d+[.,]?\d*)', area_str.replace(",", "."))
        if match:
            return float(match.group(1))
        return 0.0
    
    def _parse_rooms(self, rooms_str: str) -> int:
        """Парсит количество комнат"""
        import re
        match = re.search(r'(\d+)', rooms_str)
        if match:
            return int(match.group(1))
        return 0

