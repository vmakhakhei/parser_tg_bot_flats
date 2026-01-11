"""
Базовый класс для всех парсеров недвижимости
"""
import aiohttp
import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from fake_useragent import UserAgent
import hashlib


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
        self.session: Optional[aiohttp.ClientSession] = None
        self.ua = UserAgent()
        
    async def __aenter__(self):
        await self.start_session()
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close_session()
        
    async def start_session(self):
        """Создает HTTP сессию"""
        if self.session is None or self.session.closed:
            headers = self._get_headers()
            timeout = aiohttp.ClientTimeout(total=30)
            self.session = aiohttp.ClientSession(headers=headers, timeout=timeout)
    
    async def close_session(self):
        """Закрывает HTTP сессию"""
        if self.session and not self.session.closed:
            await self.session.close()
            
    def _get_headers(self) -> Dict[str, str]:
        """Возвращает заголовки для запросов"""
        return {
            "User-Agent": self.ua.random,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Cache-Control": "max-age=0",
        }
    
    async def _fetch_html(self, url: str) -> Optional[str]:
        """Получает HTML страницы"""
        await self.start_session()
        try:
            async with self.session.get(url) as response:
                if response.status == 200:
                    return await response.text()
                else:
                    print(f"[{self.SOURCE_NAME}] Ошибка {response.status} для {url}")
                    return None
        except asyncio.TimeoutError:
            print(f"[{self.SOURCE_NAME}] Таймаут для {url}")
            return None
        except Exception as e:
            print(f"[{self.SOURCE_NAME}] Ошибка: {e}")
            return None
    
    async def _fetch_json(self, url: str) -> Optional[Dict]:
        """Получает JSON данные"""
        await self.start_session()
        try:
            headers = self._get_headers()
            headers["Accept"] = "application/json, text/plain, */*"
            async with self.session.get(url, headers=headers) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    print(f"[{self.SOURCE_NAME}] Ошибка {response.status} для {url}")
                    return None
        except asyncio.TimeoutError:
            print(f"[{self.SOURCE_NAME}] Таймаут для {url}")
            return None
        except Exception as e:
            print(f"[{self.SOURCE_NAME}] Ошибка: {e}")
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

