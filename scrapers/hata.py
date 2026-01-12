"""
Парсер для Hata.by (baranovichi.hata.by)
"""
import re
import sys
import os
from typing import List, Optional
from bs4 import BeautifulSoup

# Добавляем родительскую директорию в path для импорта
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scrapers.base import BaseScraper, Listing

# Импортируем error_logger если доступен
try:
    from error_logger import log_error, log_warning, log_info
except ImportError:
    def log_error(source, message, exception=None):
        print(f"[ERROR] [{source}] {message}: {exception}")
    def log_warning(source, message):
        print(f"[WARN] [{source}] {message}")
    def log_info(source, message):
        print(f"[INFO] [{source}] {message}")


class HataScraper(BaseScraper):
    """Парсер объявлений с baranovichi.hata.by"""
    
    SOURCE_NAME = "hata"
    BASE_URL = "https://baranovichi.hata.by"  # По умолчанию для Барановичей
    
    def _get_city_url(self, city: str) -> str:
        """Преобразует название города в URL для Hata"""
        city_lower = city.lower().strip()
        
        # Маппинг городов на поддомены Hata
        city_mapping = {
            "барановичи": "baranovichi.hata.by",
            "брест": "brest.hata.by",
            "минск": "minsk.hata.by",
            "гомель": "gomel.hata.by",
            "гродно": "grodno.hata.by",
            "витебск": "vitebsk.hata.by",
            "могилев": "mogilev.hata.by",
            "могилёв": "mogilev.hata.by",
        }
        
        # Если город найден в маппинге, используем его
        if city_lower in city_mapping:
            return f"https://{city_mapping[city_lower]}"
        
        # По умолчанию используем Барановичи
        log_warning("hata", f"Город '{city}' не найден в маппинге, используем Барановичи")
        return f"https://{city_mapping['барановичи']}"
    
    async def fetch_listings(
        self,
        city: str = "барановичи",
        min_rooms: int = 1,
        max_rooms: int = 4,
        min_price: int = 0,
        max_price: int = 100000,
    ) -> List[Listing]:
        """Получает список объявлений"""
        
        # Получаем URL для города
        base_url = self._get_city_url(city)
        url = f"{base_url}/sale-flat/"
        
        log_info("hata", f"Загружаю страницу для города '{city}': {url}")
        
        html = await self._fetch_html(url)
        if not html:
            return []
        
        return self._parse_html(html, city, min_rooms, max_rooms, min_price, max_price, base_url)
    
    def _parse_html(
        self, 
        html: str,
        city: str,
        min_rooms: int,
        max_rooms: int,
        min_price: int,
        max_price: int,
        base_url: str = None
    ) -> List[Listing]:
        """Парсит HTML страницу витрины"""
        soup = BeautifulSoup(html, 'lxml')
        listings = []
        seen_ids = set()
        
        # Ищем все ссылки с текстом "комнатная квартира"
        title_links = soup.find_all('a', string=re.compile(r'\d+-комнатн', re.I))
        
        log_info("hata", f"Найдено ссылок с заголовками: {len(title_links)}")
        
        for link in title_links:
            href = link.get('href', '')
            
            # Проверяем что это ссылка на объявление
            id_match = re.search(r'/object/(\d+)', href)
            if not id_match:
                continue
            
            object_id = id_match.group(1)
            listing_id = f"hata_{object_id}"
            
            # Пропускаем дубликаты
            if listing_id in seen_ids:
                continue
            seen_ids.add(listing_id)
            
            # Формируем полный URL
            if href.startswith('http'):
                full_url = href
            else:
                full_url = f"{self.BASE_URL}{href}"
            
            # Текст заголовка
            link_text = link.get_text(strip=True)
            
            # Проверяем что объявление НЕ из другого города (Брест, Минск и т.д.)
            # Используем переданный параметр city вместо жестко заданного "барановичи"
            city_lower = city.lower().strip()
            other_cities = ['брест', 'минск', 'гродно', 'витебск', 'могилев', 'могилёв', 'гомель', 'пинск', 'кобрин', 'береза']
            # Убираем текущий город из списка других городов
            other_cities_filtered = [c for c in other_cities if c != city_lower]
            is_other_city = any(other_city in link_text.lower() for other_city in other_cities_filtered) and city_lower not in link_text.lower()
            
            if is_other_city:
                log_warning("hata", f"Пропускаем из другого города: {link_text[:60]}")
                continue
            
            # Парсим детали из ссылки и контекста
            listing = self._parse_listing_from_link(link, listing_id, full_url)
            if listing:
                if self._matches_filters(listing, min_rooms, max_rooms, min_price, max_price):
                    listings.append(listing)
                else:
                    log_info("hata", f"Не прошёл фильтр: {listing.rooms}к, ${listing.price}")
        
        log_info("hata", f"После фильтрации: {len(listings)} объявлений")
        return listings
    
    def _parse_listing_from_link(self, link, listing_id: str, url: str) -> Optional[Listing]:
        """Парсит объявление из ссылки и окружающего контекста"""
        try:
            title = link.get_text(strip=True)
            
            # Поднимаемся к контейнеру объявления
            container = link
            for _ in range(15):
                parent = container.find_parent()
                if not parent:
                    break
                container = parent
                # Проверяем что контейнер содержит цену и площадь
                text = container.get_text()
                if '$' in text and 'м' in text:
                    break
            
            text = container.get_text(separator=' ', strip=True)
            
            # Пропускаем рекламу
            if any(x in text.lower() for x in ['акция', 'новостройк', 'жк ', 'жилой комплекс', 'gastello', 'residence']):
                return None
            
            # Комнаты
            rooms = 0
            rooms_match = re.search(r'(\d+)\s*-?\s*комн', title, re.I)
            if rooms_match:
                rooms = int(rooms_match.group(1))
            
            # Адрес из заголовка: "X-комнатная квартира на ул. YYY ZZ, Барановичи"
            address = "Барановичи"
            addr_match = re.search(r'на\s+(ул\.\s*[^,]+(?:\s*\d+[^,]*)?),?\s*Барановичи', title, re.I)
            if addr_match:
                address = f"{addr_match.group(1)}, Барановичи"
            
            # Цена - ищем в разных форматах
            price = 0
            price_usd = 0
            price_byn = 0
            currency = "USD"
            
            # Сначала ищем в долларах "XX XXX $" или "$XX XXX"
            price_usd_match = re.search(r'(\d[\d\s]*\d)\s*\$', text)
            if not price_usd_match:
                price_usd_match = re.search(r'\$\s*(\d[\d\s]*\d)', text)
            
            if price_usd_match:
                price_str = price_usd_match.group(1).replace(' ', '').replace('\xa0', '')
                try:
                    price_usd = int(price_str)
                    price = price_usd
                    currency = "USD"
                except:
                    pass
            
            # Ищем в рублях "XX XXX BYN" или "XX XXX р." или "XX XXX руб"
            price_byn_match = re.search(r'(\d[\d\s]*\d)\s*(?:BYN|р\.?|руб)', text, re.I)
            if price_byn_match:
                price_str = price_byn_match.group(1).replace(' ', '').replace('\xa0', '')
                try:
                    price_byn = int(price_str)
                    # Если нет USD цены, используем BYN
                    if not price_usd:
                        price = price_byn
                        currency = "BYN"
                except:
                    pass
            
            # Площадь - ищем формат "XX.X /YY.Y/Z.Z м²"
            area = 0.0
            area_match = re.search(r'(\d+(?:[.,]\d+)?)\s*/\d+(?:[.,]\d+)?/\d+(?:[.,]\d+)?\s*м', text)
            if area_match:
                area = float(area_match.group(1).replace(',', '.'))
            else:
                # Простой формат "XX м²"
                area_match = re.search(r'(\d+(?:[.,]\d+)?)\s*м[²2]', text)
                if area_match:
                    area = float(area_match.group(1).replace(',', '.'))
            
            # Этаж - ищем формат "X этаж (Y этажей)"
            floor = ""
            floor_match = re.search(r'(\d+)\s*этаж\s*\((\d+)\s*этаж', text)
            if floor_match:
                floor = f"{floor_match.group(1)}/{floor_match.group(2)}"
            
            # Год постройки - улучшенный парсинг
            year_built = ""
            # Ищем различные варианты: "1967 г.", "год постройки: 1967", "построен в 1967", "1967 год"
            year_patterns = [
                r'год\s+постройки[:\s]+(\d{4})',
                r'построен\s+в\s+(\d{4})',
                r'(\d{4})\s+г\.',
                r'(\d{4})\s+год',
                r'год[:\s]+(\d{4})'
            ]
            for pattern in year_patterns:
                year_match = re.search(pattern, text, re.IGNORECASE)
                if year_match:
                    try:
                        year_int = int(year_match.group(1))
                        # Проверяем разумность года (1900-2025)
                        if 1900 <= year_int <= 2025:
                            year_built = str(year_int)
                            break
                    except:
                        pass
            
            # Дата создания объявления
            created_at = ""
            # Ищем дату в тексте: "сегодня", "вчера", "X дней назад", "DD.MM.YYYY"
            date_patterns = [
                r'сегодня',
                r'вчера',
                r'(\d+)\s+дн[яей]\s+назад',
                r'(\d{1,2})\.(\d{1,2})\.(\d{4})',  # DD.MM.YYYY
                r'(\d{4})-(\d{1,2})-(\d{1,2})',  # YYYY-MM-DD
            ]
            for pattern in date_patterns:
                date_match = re.search(pattern, text, re.IGNORECASE)
                if date_match:
                    if pattern == r'сегодня':
                        from datetime import datetime
                        created_at = datetime.now().strftime("%Y-%m-%d")
                        break
                    elif pattern == r'вчера':
                        from datetime import datetime, timedelta
                        created_at = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
                        break
                    elif pattern == r'(\d+)\s+дн[яей]\s+назад':
                        from datetime import datetime, timedelta
                        days_ago = int(date_match.group(1))
                        created_at = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")
                        break
                    elif pattern == r'(\d{1,2})\.(\d{1,2})\.(\d{4})':
                        day, month, year = date_match.groups()
                        created_at = f"{year}-{month.zfill(2)}-{day.zfill(2)}"
                        break
                    elif pattern == r'(\d{4})-(\d{1,2})-(\d{1,2})':
                        created_at = date_match.group(0)
                        break
            
            # Фото - ищем img в контейнере
            photos = []
            for img in container.find_all('img', src=True)[:10]:
                img_src = img.get('src') or img.get('data-src') or img.get('data-lazy')
                if not img_src:
                    continue
                
                # Пропускаем служебные картинки
                skip_patterns = ['sprite', 'icon', 'logo', 'no-photo', 'placeholder', 
                               'spacer', 'blank', 'pixel', 'tracking', '.svg', '#']
                if any(p in img_src.lower() for p in skip_patterns):
                    continue
                
                # Нормализуем URL
                if not img_src.startswith('http'):
                    if img_src.startswith('//'):
                        img_src = f"https:{img_src}"
                    else:
                        img_src = f"https://baranovichi.hata.by{img_src}"
                
                # Hata.by использует формат /img/XXXXX/YYYxZZZ/
                if '/img/' in img_src or 'hata.by' in img_src:
                    # Заменяем превью на большое фото
                    img_src = re.sub(r'/\d+x\d+/', '/800x600/', img_src)
                    if img_src not in photos:
                        photos.append(img_src)
                        if len(photos) >= 3:
                            break
            
            # Форматирование цены в зависимости от валюты
            if currency == "USD":
                price_formatted = f"${price:,}".replace(",", " ") if price else "Цена не указана"
            else:
                price_formatted = f"{price:,} BYN".replace(",", " ") if price else "Цена не указана"
            
            # Добавляем цену в другой валюте если есть обе
            if price_usd and price_byn:
                if currency == "USD":
                    price_formatted += f" ({price_byn:,} BYN)".replace(",", " ")
                else:
                    price_formatted += f" (${price_usd:,})".replace(",", " ")
            
            # Формируем финальный заголовок
            display_title = title if title else f"{rooms}-комн. квартира, {area} м²"
            
            return Listing(
                id=listing_id,
                source="Hata.by",
                title=display_title,
                price=price,
                price_formatted=price_formatted,
                rooms=rooms,
                area=area,
                floor=floor,
                address=address,
                photos=photos,
                url=url,
                currency=currency,
                price_usd=price_usd,
                price_byn=price_byn,
                year_built=year_built,
                created_at=created_at,
            )
            
        except Exception as e:
            log_error("hata", f"Ошибка парсинга объявления", e)
            return None
    
    def _matches_filters(
        self, 
        listing: Listing, 
        min_rooms: int,
        max_rooms: int,
        min_price: int,
        max_price: int
    ) -> bool:
        """Проверяет соответствие фильтрам"""
        if listing.rooms > 0 and (listing.rooms < min_rooms or listing.rooms > max_rooms):
            return False
        if listing.price > 0 and (listing.price < min_price or listing.price > max_price):
            return False
        return True
