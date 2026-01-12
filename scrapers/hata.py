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
    """Парсер объявлений с hata.by"""
    
    SOURCE_NAME = "hata"
    BASE_URL = "https://www.hata.by"
    
    async def _get_city_ckod_dynamic(self, city: str) -> Optional[str]:
        """Динамически определяет код ckod для города через поиск на hata.by"""
        try:
            import aiohttp
            city_lower = city.lower().strip()
            
            # Метод 1: Пробуем найти через поиск на сайте
            # Отправляем запрос на поиск с названием города
            search_url = f"{self.BASE_URL}/search/"
            
            async with aiohttp.ClientSession() as session:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Referer": f"{self.BASE_URL}/",
                }
                
                # Пробуем POST запрос с параметрами поиска
                data = aiohttp.FormData({
                    "s_do": "sale",
                    "s_what": "flat",
                    "s_where": city,  # Название города
                })
                
                async with session.post(search_url, data=data, headers=headers, allow_redirects=True, timeout=10) as resp:
                    if resp.status == 200:
                        final_url = str(resp.url)
                        html = await resp.text()
                        
                        # Ищем ckod в URL редиректа
                        ckod_match = re.search(r'ckod=(\d+)', final_url)
                        if ckod_match:
                            ckod = ckod_match.group(1)
                            log_info("hata", f"✅ Найден код города '{city}' через поиск: {ckod}")
                            return ckod
                        
                        # Ищем ckod в HTML страницы результатов
                        ckod_matches = re.findall(r'ckod["\']?\s*[:=]\s*["\']?(\d+)', html, re.IGNORECASE)
                        if ckod_matches:
                            # Берем первый найденный код
                            ckod = ckod_matches[0]
                            log_info("hata", f"✅ Найден код города '{city}' в HTML результатов: {ckod}")
                            return ckod
                
                # Метод 2: Пробуем найти через главную страницу
                main_url = f"{self.BASE_URL}/"
                async with session.get(main_url, headers=headers, timeout=10) as resp:
                    if resp.status == 200:
                        html = await resp.text()
                        
                        # Ищем все ссылки с ckod и проверяем контекст
                        all_ckod_matches = re.finditer(r'ckod["\']?\s*[:=]\s*["\']?(\d+)', html, re.IGNORECASE)
                        for match in all_ckod_matches:
                            ckod = match.group(1)
                            # Проверяем контекст вокруг кода (300 символов до и после)
                            start = max(0, match.start() - 300)
                            end = min(len(html), match.end() + 300)
                            context = html[start:end].lower()
                            
                            # Проверяем, есть ли название города в контексте
                            if city_lower in context:
                                log_info("hata", f"✅ Найден код города '{city}' на главной странице: {ckod}")
                                return ckod
        except Exception as e:
            log_warning("hata", f"Ошибка при динамическом определении кода города '{city}': {e}")
        
        return None
    
    def _get_city_ckod(self, city: str) -> Optional[str]:
        """Преобразует название города в код ckod для Hata API (из кэша/маппинга)"""
        city_lower = city.lower().strip()
        
        # Маппинг городов на коды ckod (коды городов в системе Hata)
        # Известные коды (подтвержденные):
        # - Минск: 5000000000 (из URL: https://www.hata.by/search/~s_do=sale~s_what=flat~currency=840~rooms=1~ctype=ckod~ckod=5000000000/page/1/)
        # - Барановичи: 1410000000 (из URL: https://baranovichi.hata.by/search/~s_do=sale~s_what=flat~currency=840~rooms=1~ctype=ckod~ckod=1410000000/page/1/)
        city_mapping = {
            "барановичи": "1410000000",
            "брест": "1010000000",  # Предположительно (будет уточнено через динамический поиск при первом использовании)
            "минск": "5000000000",
            "гомель": "3010000000",  # Предположительно
            "гродно": "4010000000",  # Предположительно
            "витебск": "2010000000",  # Предположительно
            "могилев": "6010000000",  # Предположительно
            "могилёв": "6010000000",
        }
        
        # Если город найден в маппинге, используем его
        if city_lower in city_mapping:
            return city_mapping[city_lower]
        
        # Если город не найден в маппинге, возвращаем None (будет использован динамический поиск)
        return None
    
    async def fetch_listings(
        self,
        city: str = "барановичи",
        min_rooms: int = 1,
        max_rooms: int = 4,
        min_price: int = 0,
        max_price: int = 100000,
    ) -> List[Listing]:
        """Получает список объявлений"""
        
        # Получаем код города (сначала из маппинга, если нет - пробуем динамически)
        ckod = self._get_city_ckod(city)
        
        # Если код не найден в маппинге, пробуем определить динамически
        if not ckod:
            log_info("hata", f"Код города '{city}' не найден в маппинге, пробую определить динамически через поиск на hata.by...")
            ckod = await self._get_city_ckod_dynamic(city)
            
            if not ckod:
                log_error("hata", f"❌ Не удалось определить код города '{city}' на hata.by. Город может отсутствовать в базе данных сайта.")
                # Не используем fallback - возвращаем пустой список, чтобы пользователь знал, что город не найден
                return []
            else:
                log_info("hata", f"✅ Динамически определен код города '{city}': {ckod}")
        
        # Формируем URL в формате Hata: ~s_do=sale~s_what=flat~currency=840~rooms=1~ctype=ckod~ckod=XXXXX
        # Важно: rooms указывается как массив, но в URL это просто число
        # Формируем параметры для комнат (если указан диапазон, берем минимальное значение для URL)
        rooms_param = min_rooms if min_rooms > 0 else 1
        
        # Формируем URL с параметрами в формате Hata (~ как разделитель)
        # Формат: https://www.hata.by/search/~s_do=sale~s_what=flat~currency=840~rooms=1~ctype=ckod~ckod=XXXXX/page/1/
        url = f"{self.BASE_URL}/search/~s_do=sale~s_what=flat~currency=840~rooms={rooms_param}~ctype=ckod~ckod={ckod}/page/1/"
        
        log_info("hata", f"Загружаю страницу для города '{city}' (ckod={ckod}): {url}")
        
        html = await self._fetch_html(url)
        if not html:
            log_warning("hata", f"Не удалось загрузить страницу для города '{city}': {url}")
            return []
        
        # Проверяем, что на странице есть результаты (не пустая страница)
        if "не найдено" in html.lower() or "нет результатов" in html.lower():
            log_warning("hata", f"Для города '{city}' (ckod={ckod}) не найдено объявлений")
            return []
        
        return self._parse_html(html, city, min_rooms, max_rooms, min_price, max_price, self.BASE_URL)
    
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
                # Используем переданный base_url или fallback на self.BASE_URL
                url_base = base_url if base_url else self.BASE_URL
                full_url = f"{url_base}{href}"
            
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
            listing = self._parse_listing_from_link(link, listing_id, full_url, city)
            if listing:
                if self._matches_filters(listing, min_rooms, max_rooms, min_price, max_price):
                    listings.append(listing)
                else:
                    log_info("hata", f"Не прошёл фильтр: {listing.rooms}к, ${listing.price}")
        
        log_info("hata", f"После фильтрации: {len(listings)} объявлений")
        return listings
    
    def _parse_listing_from_link(self, link, listing_id: str, url: str, city: str = "барановичи") -> Optional[Listing]:
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
            
            # Адрес из заголовка: "X-комнатная квартира на ул. YYY ZZ, [Город]"
            # Используем переданный город вместо жестко заданного "Барановичи"
            address = city.title() if city else "Барановичи"
            # Ищем адрес в формате "на ул. YYY ZZ, [Город]" или "на ул. YYY ZZ"
            addr_patterns = [
                rf'на\s+(ул\.\s*[^,]+(?:\s*\d+[^,]*)?),?\s*{city}',  # С указанием города
                r'на\s+(ул\.\s*[^,]+(?:\s*\d+[^,]*)?)',  # Без указания города
            ]
            for pattern in addr_patterns:
                addr_match = re.search(pattern, title, re.I)
                if addr_match:
                    street = addr_match.group(1)
                    address = f"{street}, {city.title()}" if city else f"{street}, Барановичи"
                    break
            
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
