"""
DTO (Data Transfer Object) для валидации данных парсеров

ListingDTO содержит минимально необходимые поля для валидации
перед созданием полного объекта Listing.
"""
from dataclasses import dataclass
from typing import Optional
import re

# Импортируем error_logger для логирования ошибок валидации
try:
    from error_logger import log_warning, log_error
except ImportError:
    def log_warning(source, message):
        print(f"[WARN] [{source}] {message}")
    def log_error(source, message, exception=None):
        print(f"[ERROR] [{source}] {message}: {exception}")


@dataclass
class ListingDTO:
    """
    DTO для валидации данных объявления
    
    Минимально необходимые поля:
    - title: заголовок объявления
    - price: цена (должна быть положительным числом)
    - url: ссылка на объявление (должна быть валидным URL)
    - location: адрес/локация (может быть пустой строкой, но не None)
    - source: источник данных (название парсера)
    """
    title: str
    price: int
    url: str
    location: str
    source: str
    
    def __post_init__(self):
        """Валидация данных после инициализации"""
        self._validate()
    
    def _validate(self):
        """
        Валидирует поля DTO
        
        Raises:
            ValueError: если данные невалидны
        """
        errors = []
        
        # Валидация title
        if not self.title or not isinstance(self.title, str):
            errors.append("title должен быть непустой строкой")
        elif len(self.title.strip()) == 0:
            errors.append("title не может быть пустой строкой")
        
        # Валидация price
        if not isinstance(self.price, (int, float)):
            errors.append(f"price должен быть числом, получен {type(self.price)}")
        elif self.price < 0:
            errors.append(f"price не может быть отрицательным: {self.price}")
        elif self.price == 0:
            # Нулевая цена может быть валидной (например, "цена договорная")
            # Но логируем предупреждение
            log_warning("dto", f"Объявление с нулевой ценой: {self.title[:50]}...")
        
        # Валидация url
        if not self.url or not isinstance(self.url, str):
            errors.append("url должен быть непустой строкой")
        elif len(self.url.strip()) == 0:
            errors.append("url не может быть пустой строкой")
        elif not self._is_valid_url(self.url):
            errors.append(f"url не является валидным URL: {self.url[:100]}")
        
        # Валидация location
        if self.location is None:
            errors.append("location не может быть None (используйте пустую строку)")
        elif not isinstance(self.location, str):
            errors.append(f"location должен быть строкой, получен {type(self.location)}")
        
        # Валидация source
        if not self.source or not isinstance(self.source, str):
            errors.append("source должен быть непустой строкой")
        elif len(self.source.strip()) == 0:
            errors.append("source не может быть пустой строкой")
        
        # Если есть ошибки, выбрасываем исключение
        if errors:
            error_msg = f"Ошибки валидации ListingDTO: {'; '.join(errors)}"
            log_error("dto", error_msg)
            raise ValueError(error_msg)
    
    @staticmethod
    def _is_valid_url(url: str) -> bool:
        """
        Проверяет, является ли строка валидным URL
        
        Args:
            url: Строка для проверки
        
        Returns:
            True если URL валиден, False иначе
        """
        if not url:
            return False
        
        # Простая проверка URL (http/https)
        url_pattern = re.compile(
            r'^https?://'  # http:// или https://
            r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|'  # домен
            r'localhost|'  # localhost
            r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'  # IP адрес
            r'(?::\d+)?'  # порт (опционально)
            r'(?:/?|[/?]\S+)$',  # путь (опционально)
            re.IGNORECASE
        )
        
        return bool(url_pattern.match(url.strip()))
    
    def to_dict(self) -> dict:
        """
        Конвертирует DTO в словарь
        
        Returns:
            Словарь с данными DTO
        """
        return {
            "title": self.title,
            "price": self.price,
            "url": self.url,
            "location": self.location,
            "source": self.source,
        }
    
    @classmethod
    def from_dict(cls, data: dict, source: str) -> Optional['ListingDTO']:
        """
        Создает ListingDTO из словаря с валидацией
        
        Args:
            data: Словарь с данными
            source: Источник данных (название парсера)
        
        Returns:
            ListingDTO или None если данные невалидны
        """
        try:
            # Извлекаем поля с fallback значениями
            title = data.get("title", "").strip()
            if not title:
                title = data.get("name", "").strip()  # Альтернативное поле
            
            price = data.get("price", 0)
            if isinstance(price, str):
                # Пытаемся распарсить цену из строки
                price_str = re.sub(r'[^\d]', '', price)
                price = int(price_str) if price_str else 0
            
            url = data.get("url", "").strip()
            if not url:
                url = data.get("link", "").strip()  # Альтернативное поле
            
            location = data.get("location", "").strip()
            if not location:
                location = data.get("address", "").strip()  # Альтернативное поле
            
            # Создаем DTO (валидация произойдет в __post_init__)
            return cls(
                title=title,
                price=int(price) if price else 0,
                url=url,
                location=location or "",  # Пустая строка вместо None
                source=source
            )
        except (ValueError, TypeError, KeyError) as e:
            log_error("dto", f"Ошибка создания ListingDTO из словаря: {e}", e)
            return None
    
    def __str__(self) -> str:
        """Строковое представление DTO"""
        return f"ListingDTO(title='{self.title[:50]}...', price={self.price}, source='{self.source}')"
