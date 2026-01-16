"""
Парсер для re.kufar.by через официальный API
"""
import re
import json
import sys
import os
import time
from typing import List, Optional

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

# Вспомогательная функция для debug логирования
def _write_debug_log(data):
    """Записывает debug лог в файл"""
    try:
        import os
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        log_path = os.path.join(base_dir, ".cursor", "debug.log")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(data) + "\n")
    except Exception as e:
        try:
            log_error("kufar", f"Debug log error: {e}")
        except:
            pass


class KufarScraper(BaseScraper):
    """Парсер объявлений с Kufar через API"""
    
    SOURCE_NAME = "kufar"
    BASE_URL = "https://re.kufar.by"
    API_URL = "https://api.kufar.by/search-api/v2/search/rendered-paginated"
    
    def _get_city_gtsy(self, city: str) -> str:
        """Преобразует название города в формат gtsy для Kufar API"""
        city_lower = city.lower().strip()
        
        # Маппинг городов на формат Kufar API (gtsy)
        city_mapping = {
            "барановичи": "country-belarus~province-brestskaja_oblast~locality-baranovichi",
            "брест": "country-belarus~province-brestskaja_oblast~locality-brest",
            "минск": "country-belarus~province-minsk~locality-minsk",
            "гомель": "country-belarus~province-gomelskaja_oblast~locality-gomel",
            "гродно": "country-belarus~province-grodnenskaja_oblast~locality-grodno",
            "витебск": "country-belarus~province-vitebskaja_oblast~locality-vitebsk",
            "могилев": "country-belarus~province-mogilevskaja_oblast~locality-mogilev",
            "могилёв": "country-belarus~province-mogilevskaja_oblast~locality-mogilev",
            "орша": "country-belarus~province-vitebskaja_oblast~locality-orsha",
        }
        
        # Если город найден в маппинге, используем его
        if city_lower in city_mapping:
            return city_mapping[city_lower]
        
        # По умолчанию используем Барановичи
        log_warning("kufar", f"Город '{city}' не найден в маппинге, используем Барановичи")
        return city_mapping["барановичи"]
    
    async def fetch_listings(
        self,
        city: str = "барановичи",
        min_rooms: int = 1,
        max_rooms: int = 4,
        min_price: int = 0,
        max_price: int = 100000,
        max_pages: int = 10,  # Увеличено до 10 страниц для получения большего количества объявлений
    ) -> List[Listing]:
        """Получает список объявлений через API с пагинацией"""
        
        log_info("kufar", f"Фильтры: комнаты {min_rooms}-{max_rooms}, цена ${min_price}-${max_price}")
        
        # Получаем gtsy параметр для города
        gtsy_param = self._get_city_gtsy(city)
        
        # Базовые параметры запроса (работающий формат из браузера)
        base_url = (
            f"{self.API_URL}"
            f"?cat=1010"  # Категория: квартиры
            f"&cur=USD"
            f"&gtsy={gtsy_param}"
            f"&lang=ru"  # Язык интерфейса
            f"&typ=sell"  # Продажа
            f"&sort=lst.d"  # Сортировка по дате (новые первые)
            f"&size=30"  # Количество объявлений на странице
        )
        
        # Добавляем фильтры по комнатам в URL (если указаны)
        # Формат: rms=v.or:1,2 (для 1 или 2 комнат)
        if min_rooms > 0 and max_rooms > 0:
            rooms_list = list(range(min_rooms, max_rooms + 1))
            if rooms_list:
                rooms_param = ",".join(map(str, rooms_list))
                base_url += f"&rms=v.or:{rooms_param}"
        
        # Добавляем фильтр по цене в URL (если указаны)
        # Формат: prc=r:50000,60000 (от 50000 до 60000)
        if min_price > 0 or max_price < 1000000:
            # Используем реальные значения из фильтров
            price_min = max(0, min_price)
            price_max = min(1000000, max_price)
            if price_max > price_min:
                base_url += f"&prc=r:{price_min},{price_max}"
        
        all_listings = []
        current_page = 1
        next_token = None
        
        while current_page <= max_pages:
            # Формируем URL с токеном пагинации
            if next_token:
                url = f"{base_url}&cursor={next_token}"
            else:
                url = base_url
            
            log_info("kufar", f"Запрос API для города '{city}', страница {current_page}: {url}")
            
            # #region agent log
            _write_debug_log({
                "sessionId": "test-session",
                "runId": "run1",
                "hypothesisId": "A",
                "location": "kufar.py:115",
                "message": "Kufar API request",
                "data": {"city": city, "page": current_page, "url": url, "filters": {"min_rooms": min_rooms, "max_rooms": max_rooms, "min_price": min_price, "max_price": max_price}},
                "timestamp": int(time.time() * 1000)
            })
            # #endregion
        
        # Получаем JSON
        json_data = await self._fetch_json(url)
            
            # Логируем полный ответ API для диагностики
            if json_data:
                ads_count = len(json_data.get("ads", []))
                total_count = json_data.get("total", 0)
                log_info("kufar", f"API ответ: найдено объявлений на странице: {ads_count}, всего на сайте: {total_count}")
                if ads_count == 0 and total_count == 0:
                    log_warning("kufar", f"⚠️ API вернул 0 объявлений. Возможно, фильтры слишком строгие или формат параметров неправильный.")
                    log_warning("kufar", f"Проверьте URL: {url}")
            
            # #region agent log
            ads_count = len(json_data.get("ads", [])) if json_data else 0
            total_count = json_data.get("total", 0) if json_data else 0
            _write_debug_log({
                "sessionId": "test-session",
                "runId": "run1",
                "hypothesisId": "A",
                "location": "kufar.py:125",
                "message": "Kufar API response",
                "data": {"city": city, "page": current_page, "has_data": json_data is not None, "ads_count": ads_count, "total_count": total_count},
                "timestamp": int(time.time() * 1000)
            })
            # #endregion
            
        if not json_data:
                log_warning("kufar", f"API вернул пустой ответ для города '{city}' на странице {current_page}")
                break
            
            # Парсим объявления с текущей страницы
            page_listings = self._parse_api_response(json_data, min_rooms, max_rooms, min_price, max_price, city)
            all_listings.extend(page_listings)
            
            # #region agent log
            _write_debug_log({
                "sessionId": "test-session",
                "runId": "run1",
                "hypothesisId": "D",
                "location": "kufar.py:140",
                "message": "Kufar pagination check",
                "data": {"city": city, "page": current_page, "listings_on_page": len(page_listings), "total_listings": len(all_listings)},
                "timestamp": int(time.time() * 1000)
            })
            # #endregion
            
            # Получаем токен для следующей страницы
            pagination = json_data.get("pagination") or {}
            pages = pagination.get("pages") or []
            
            # Ищем токен следующей страницы
            next_token = None
            for page in pages:
                if page and isinstance(page, dict) and page.get("label") == "next":
                    next_token = page.get("token")
                    break
            
            # #region agent log
            _write_debug_log({
                "sessionId": "test-session",
                "runId": "run1",
                "hypothesisId": "D",
                "location": "kufar.py:155",
                "message": "Kufar pagination token",
                "data": {"city": city, "page": current_page, "has_next_token": next_token is not None, "max_pages": max_pages},
                "timestamp": int(time.time() * 1000)
            })
            # #endregion
            
            # Если нет следующей страницы, выходим
            if not next_token:
                log_info("kufar", f"Достигнута последняя страница ({current_page})")
                break
            
            current_page += 1
        
        total_found = json_data.get("total", 0) if json_data else 0
        log_info("kufar", f"Загружено {len(all_listings)} объявлений с {current_page} страниц (всего на сайте: {total_found})")
        
        # #region agent log
        _write_debug_log({
            "sessionId": "test-session",
            "runId": "run1",
            "hypothesisId": "A",
            "location": "kufar.py:170",
            "message": "Kufar fetch complete",
            "data": {"city": city, "total_listings": len(all_listings), "pages_loaded": current_page, "total_on_site": total_found, "filters": {"min_rooms": min_rooms, "max_rooms": max_rooms, "min_price": min_price, "max_price": max_price}},
            "timestamp": int(time.time() * 1000)
        })
        # #endregion
        
        return all_listings
    
    async def _fetch_json(self, url: str) -> Optional[dict]:
        """Получает JSON от API"""
        import aiohttp
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            "Referer": "https://re.kufar.by/",
            "Origin": "https://re.kufar.by",
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=30) as response:
                    if response.status == 200:
                        data = await response.json()
                        # Логируем статус ответа для диагностики
                        if not data or not data.get("ads"):
                            log_warning("kufar", f"API вернул пустой ответ или без поля 'ads'. Статус: {response.status}")
                            log_warning("kufar", f"Полный URL: {url}")
                            if data:
                                log_warning("kufar", f"Структура ответа: {list(data.keys())}")
                        return data
                    else:
                        response_text = await response.text()
                        log_warning("kufar", f"API ответил кодом {response.status}")
                        log_warning("kufar", f"Ответ сервера: {response_text[:500]}")
                        log_warning("kufar", f"URL запроса: {url}")
                        return None
        except Exception as e:
            log_error("kufar", f"Ошибка API запроса", e)
            log_error("kufar", f"URL запроса: {url}")
            return None
    
    def _parse_api_response(
        self, 
        data: dict,
        min_rooms: int,
        max_rooms: int,
        min_price: int,
        max_price: int,
        city: str = "Минск"
    ) -> List[Listing]:
        """Парсит ответ API"""
        listings = []
        
        # Защита от None
        if not data:
            log_warning("kufar", "Получен пустой ответ от API")
            return []
        
        # Данные в ads
        ads = data.get("ads", []) or []
        
        parsed_count = 0
        filtered_out_rooms = 0
        filtered_out_price = 0
        logged_samples = 0  # Для логирования первых отфильтрованных объявлений
        
        for ad in ads:
            if not ad:
                continue
            listing = self._parse_ad(ad, city)
            if listing:
                parsed_count += 1
                if self._matches_filters(listing, min_rooms, max_rooms, min_price, max_price):
                    listings.append(listing)
                else:
                    # Логируем первые 3 отфильтрованных объявления для диагностики
                    if logged_samples < 3:
                        log_info("kufar", f"Отфильтровано: {listing.rooms}к, ${listing.price} (фильтр: {min_rooms}-{max_rooms}к, ${min_price}-${max_price})")
                        logged_samples += 1
                    
                    # Считаем причины отсева
                    if listing.rooms > 0 and (listing.rooms < min_rooms or listing.rooms > max_rooms):
                        filtered_out_rooms += 1
                    elif listing.price > 0 and (listing.price < min_price or listing.price > max_price):
                        filtered_out_price += 1
        
        # Логируем статистику парсинга страницы
        if ads:
            log_info("kufar", f"Страница: {len(ads)} в ответе, {parsed_count} распарсено, "
                     f"{len(listings)} прошло (отсеяно: {filtered_out_rooms} по комнатам, {filtered_out_price} по цене)")
        
        return listings
    
    def _parse_ad(self, ad: dict, city: str = "Минск") -> Optional[Listing]:
        """Парсит одно объявление из API"""
        try:
            # ID
            ad_id = ad.get("ad_id")
            if not ad_id:
                return None
            listing_id = f"kufar_{ad_id}"
            
            # URL
            ad_link = ad.get("ad_link", "")
            if ad_link and not ad_link.startswith("http"):
                ad_link = f"{self.BASE_URL}{ad_link}"
            
            # Дата создания объявления
            created_at = ""
            # Пробуем разные варианты полей даты в API Kufar
            list_time = ad.get("list_time", "")  # Unix timestamp
            if list_time:
                try:
                    from datetime import datetime
                    # Если это timestamp в миллисекундах
                    if len(str(list_time)) > 10:
                        timestamp = int(list_time) / 1000
                    else:
                        timestamp = int(list_time)
                    dt = datetime.fromtimestamp(timestamp)
                    created_at = dt.strftime("%Y-%m-%d")
                except:
                    pass
            
            # Если не нашли timestamp, пробуем текстовые поля
            if not created_at:
                date_text = ad.get("list_time_text", "") or ad.get("created_at", "") or ad.get("date", "")
                if date_text:
                    created_at = date_text
            
            # Получаем параметры объявления
            params = {}
            for param in ad.get("ad_parameters", []):
                params[param.get("p")] = param.get("v")
                params[f"{param.get('p')}_text"] = param.get("vl")
            
            # Комнаты
            rooms = 0
            rooms_val = params.get("rooms", "")
            if rooms_val:
                try:
                    rooms = int(rooms_val)
                except:
                    if "5" in str(rooms_val):
                        rooms = 5
            
            # Площадь
            area = 0.0
            area_val = params.get("size", "")
            if area_val:
                try:
                    area = float(str(area_val).replace(",", "."))
                except:
                    pass
            
            # Этаж
            floor = ""
            floor_val = params.get("floor", "")
            floors_val = params.get("re_number_floors", "")
            # floor может быть списком [1] или числом
            if isinstance(floor_val, list) and floor_val:
                floor_val = floor_val[0]
            if floor_val and floors_val:
                floor = f"{floor_val}/{floors_val}"
            elif floor_val:
                floor = str(floor_val)
            
            # Год постройки
            year_built = ""
            # Пробуем разные варианты параметров года из API Kufar
            year_val = params.get("year", "") or params.get("re_year", "") or params.get("re_build_year", "")
            if year_val:
                try:
                    # Если это число
                    year_int = int(year_val)
                    if 1900 <= year_int <= 2025:  # Проверяем разумность года
                        year_built = str(year_int)
                except:
                    # Пробуем из текстового значения
                    year_text = params.get("year_text", "") or params.get("re_year_text", "") or params.get("re_build_year_text", "")
                    if year_text:
                        # Извлекаем год из текста (например "1985" или "1985 г.")
                        year_match = re.search(r'(\d{4})', str(year_text))
                        if year_match:
                            year_int = int(year_match.group(1))
                            if 1900 <= year_int <= 2025:  # Проверяем разумность года
                                year_built = str(year_int)
            
            # Если не нашли в параметрах, ищем в ad_parameters напрямую
            if not year_built:
                for param in ad.get("ad_parameters", []):
                    param_name = param.get("p", "").lower()
                    # Ищем параметры, связанные с годом постройки
                    if "year" in param_name or "год" in param_name or "постройки" in param_name:
                        param_value = param.get("v", "")
                        param_text = param.get("vl", "")
                        
                        # Пробуем числовое значение
                        if param_value:
                            try:
                                year_int = int(param_value)
                                if 1900 <= year_int <= 2025:
                                    year_built = str(year_int)
                                    break
                            except:
                                pass
                        
                        # Пробуем текстовое значение
                        if not year_built and param_text:
                            year_match = re.search(r'(\d{4})', str(param_text))
                            if year_match:
                                try:
                                    year_int = int(year_match.group(1))
                                    if 1900 <= year_int <= 2025:
                                        year_built = str(year_int)
                                        break
                                except:
                                    pass
            
            # Цена
            price = 0
            price_usd = 0
            price_byn = 0
            currency = "USD"
            
            # Цена в USD (в центах)
            raw_price_usd = ad.get("price_usd", "")
            if raw_price_usd:
                try:
                    price_usd = int(raw_price_usd) // 100
                    price = price_usd
                except:
                    pass
            
            # Цена в BYN (в копейках)
            raw_price_byn = ad.get("price_byn", "")
            if raw_price_byn:
                try:
                    price_byn = int(raw_price_byn) // 100
                    # Если нет USD цены, используем BYN
                    if not price_usd:
                        price = price_byn
                        currency = "BYN"
                except:
                    pass
            
            # Если нет цены, пробуем другие варианты
            if not price:
                for param in ad.get("ad_parameters", []):
                    if "price" in param.get("p", "").lower():
                        try:
                            price = int(param.get("v", 0))
                        except:
                            pass
            
            # Адрес - используем переданный город
            city_name = city.title()
            address = city_name
            
            # Из account_parameters
            for acc_param in ad.get("account_parameters", []):
                if acc_param.get("p") == "address":
                    address = acc_param.get("v", city_name)
                    break
            
            # Или из параметров
            if address == city_name:
                street = params.get("street_text", "") or params.get("street", "")
                house = params.get("house", "")
                if street:
                    address = street
                    if house:
                        address += f", {house}"
                    address += f", {city_name}"
            
            # Фото - пока отключены из-за проблем с URL
            # Kufar использует CDN с токенами, поэтому напрямую ссылки не работают
            # TODO: исследовать правильный формат URL для фото
            photos = []
            # images = ad.get("images", [])
            # for img in images[:3]:
            #     path = img.get("path", "")
            #     if path:
            #         photo_url = f"https://rms.kufar.by/v1/{path}"
            #         photos.append(photo_url)
            
            # Формируем заголовок
            title = f"{rooms}-комн., {area} м²" if rooms and area else "Квартира"
            
            # Форматирование цены в зависимости от валюты
            if currency == "USD":
                price_formatted = f"${price:,}".replace(",", " ") if price else "Цена не указана"
            else:
                price_formatted = f"{price:,} BYN".replace(",", " ") if price else "Цена не указана"
            
            # Добавляем цену в другой валюте если есть
            if price_usd and price_byn:
                if currency == "USD":
                    price_formatted += f" ({price_byn:,} BYN)".replace(",", " ")
                else:
                    price_formatted += f" (${price_usd:,})".replace(",", " ")
            
            # Определяем тип продавца (company_ad = True означает агентство)
            is_company = ad.get("company_ad", False)
            
            return Listing(
                id=listing_id,
                source="Kufar.by",
                title=title,
                price=price,
                price_formatted=price_formatted,
                rooms=rooms if rooms else 0,
                area=area if area else 0.0,
                floor=floor,
                address=address,
                photos=photos,
                url=ad_link,
                currency=currency,
                price_usd=price_usd,
                price_byn=price_byn,
                year_built=year_built,
                created_at=created_at,
                is_company=is_company,
            )
            
        except Exception as e:
            log_error("kufar", f"Ошибка парсинга объявления", e)
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
