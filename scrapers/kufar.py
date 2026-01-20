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

# Конфигурационные константы для оптимизации парсинга
MAX_PAGES_PER_RUN = 2  # Жёсткий предохранитель - максимум страниц за один запуск
STOP_ON_OLD_THRESHOLD = 5  # Сколько подряд старых объявлений считаем сигналом остановки


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
        
        last_known_ad_id = None
        
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
        consecutive_old_ads = 0  # Счётчик подряд идущих старых объявлений
        stop_parsing = False  # Флаг остановки парсинга
        new_ads_count = 0  # Счётчик новых объявлений
        
        while current_page <= max_pages:
            # Жёсткий лимит страниц (предохранитель)
            if current_page > MAX_PAGES_PER_RUN:
                log_info("kufar", f"Достигнут лимит страниц ({MAX_PAGES_PER_RUN}), останавливаю парсинг")
                break
            # Формируем URL с токеном пагинации
            if next_token:
                url = f"{base_url}&cursor={next_token}"
            else:
                url = base_url
            
            log_info("kufar", f"Запрос API для города '{city}', страница {current_page}: {url}")
            
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
            
            if not json_data:
                log_warning("kufar", f"API вернул пустой ответ для города '{city}' на странице {current_page}")
                break
            
            # Парсим объявления с текущей страницы с проверкой на старые
            page_listings, page_stop_parsing, page_new_count, consecutive_old_ads = await self._parse_api_response_with_stop_check(
                json_data, min_rooms, max_rooms, min_price, max_price, city, last_known_ad_id, consecutive_old_ads
            )
            all_listings.extend(page_listings)
            new_ads_count += page_new_count
            
            if page_stop_parsing:
                stop_parsing = True
                log_info("kufar", f"Остановка парсинга на странице {current_page}")
                break
            
            # Получаем токен для следующей страницы
            pagination = json_data.get("pagination") or {}
            pages = pagination.get("pages") or []
            
            # Ищем токен следующей страницы
            next_token = None
            for page in pages:
                if page and isinstance(page, dict) and page.get("label") == "next":
                    next_token = page.get("token")
                    break
            
            # Если нет следующей страницы, выходим
            if not next_token:
                log_info("kufar", f"Достигнута последняя страница ({current_page})")
                break
            
            current_page += 1
        
        total_found = json_data.get("total", 0) if json_data else 0
        log_info("kufar", f"Парсинг завершён: страниц={current_page}, всего загружено={len(all_listings)} (всего на сайте: {total_found})")
        
        return all_listings
    
    async def fetch_listings_with_raw_json(
        self,
        city: str = "барановичи",
        min_rooms: int = 1,
        max_rooms: int = 4,
        min_price: int = 0,
        max_price: int = 100000,
        max_pages: int = 10,
    ) -> tuple[List[Listing], List[dict]]:
        """
        Получает список объявлений через API с пагинацией и возвращает также raw JSON ответы
        
        Returns:
            tuple: (listings, raw_api_responses)
            - listings: List[Listing] - список объектов Listing
            - raw_api_responses: List[dict] - список raw JSON ответов от API (каждый содержит поле "ads")
        """
        log_info("kufar", f"Фильтры: комнаты {min_rooms}-{max_rooms}, цена ${min_price}-${max_price}")
        
        # Получаем gtsy параметр для города
        gtsy_param = self._get_city_gtsy(city)
        
        # Базовые параметры запроса
        base_url = (
            f"{self.API_URL}"
            f"?cat=1010"
            f"&cur=USD"
            f"&gtsy={gtsy_param}"
            f"&lang=ru"
            f"&typ=sell"
            f"&sort=lst.d"
            f"&size=30"
        )
        
        # Добавляем фильтры по комнатам
        if min_rooms > 0 and max_rooms > 0:
            rooms_list = list(range(min_rooms, max_rooms + 1))
            if rooms_list:
                rooms_param = ",".join(map(str, rooms_list))
                base_url += f"&rms=v.or:{rooms_param}"
        
        # Добавляем фильтр по цене
        if min_price > 0 or max_price < 1000000:
            price_min = max(0, min_price)
            price_max = min(1000000, max_price)
            if price_max > price_min:
                base_url += f"&prc=r:{price_min},{price_max}"
        
        all_listings = []
        raw_api_responses = []  # Сохраняем raw JSON ответы
        current_page = 1
        next_token = None
        
        while current_page <= max_pages:
            # Формируем URL с токеном пагинации
            if next_token:
                url = f"{base_url}&cursor={next_token}"
            else:
                url = base_url
            
            log_info("kufar", f"Запрос API для города '{city}', страница {current_page}: {url}")
            
            # Получаем JSON
            json_data = await self._fetch_json(url)
            
            if not json_data:
                log_warning("kufar", f"API вернул пустой ответ для города '{city}' на странице {current_page}")
                break
            
            # Сохраняем raw JSON ответ
            raw_api_responses.append(json_data.copy())
            
            # Логируем полный ответ API для диагностики
            ads_count = len(json_data.get("ads", []))
            total_count = json_data.get("total", 0)
            log_info("kufar", f"API ответ: найдено объявлений на странице: {ads_count}, всего на сайте: {total_count}")
            
            # Парсим объявления с текущей страницы (без проверки на старые для raw_json версии)
            page_listings, _, _, _ = await self._parse_api_response_with_stop_check(
                json_data, min_rooms, max_rooms, min_price, max_price, city, None, 0
            )
            all_listings.extend(page_listings)
            
            # Получаем токен для следующей страницы
            pagination = json_data.get("pagination") or {}
            pages = pagination.get("pages") or []
            
            # Ищем токен следующей страницы
            next_token = None
            for page in pages:
                if page and isinstance(page, dict) and page.get("label") == "next":
                    next_token = page.get("token")
                    break
            
            # Если нет следующей страницы, выходим
            if not next_token:
                log_info("kufar", f"Достигнута последняя страница ({current_page})")
                break
            
            current_page += 1
        
        total_found = json_data.get("total", 0) if json_data else 0
        log_info("kufar", f"Загружено {len(all_listings)} объявлений с {current_page} страниц (всего на сайте: {total_found})")
        
        return all_listings, raw_api_responses
    
    async def _fetch_json(self, url: str) -> Optional[dict]:
        """
        Получает JSON от API через унифицированный HTTP-клиент
        
        Использует базовый метод с дополнительными заголовками для Kufar API
        """
        # Дополнительные заголовки для Kufar API
        headers = {
            "Referer": "https://re.kufar.by/",
            "Origin": "https://re.kufar.by",
        }
        
        # Используем базовый метод с retry и унифицированным клиентом
        data = await super()._fetch_json(url, headers=headers)
        
        # Логируем статус ответа для диагностики
        if data is not None and (not data or not data.get("ads")):
            log_warning("kufar", f"API вернул пустой ответ или без поля 'ads'")
            log_warning("kufar", f"Полный URL: {url}")
            if data:
                log_warning("kufar", f"Структура ответа: {list(data.keys())}")
        
        return data
    
    async def _parse_api_response_with_stop_check(
        self, 
        data: dict,
        min_rooms: int,
        max_rooms: int,
        min_price: int,
        max_price: int,
        city: str = "Минск",
        last_known_ad_id: Optional[str] = None,
        consecutive_old_ads: int = 0
    ) -> tuple[List[Listing], bool, int, int]:
        """
        Парсит ответ API с проверкой на старые объявления для остановки парсинга
        
        Args:
            consecutive_old_ads: текущее количество подряд идущих старых объявлений (накапливается между страницами)
        
        Returns:
            tuple: (listings, stop_parsing, new_ads_count, consecutive_old_ads)
            - listings: список новых объявлений
            - stop_parsing: флаг остановки парсинга
            - new_ads_count: количество новых объявлений
            - consecutive_old_ads: обновленное количество подряд идущих старых объявлений
        """
        listings = []
        stop_parsing = False
        new_ads_count = 0
        
        # Защита от None
        if not data:
            log_warning("kufar", "Получен пустой ответ от API")
            return [], False, 0, consecutive_old_ads
        
        # Данные в ads
        ads = data.get("ads", []) or []
        
        parsed_count = 0
        filtered_out_rooms = 0
        filtered_out_price = 0
        logged_samples = 0  # Для логирования первых отфильтрованных объявлений
        
        # Импортируем функцию проверки существования объявления
        try:
            from database_turso import ad_exists
        except ImportError:
            log_warning("kufar", "Не удалось импортировать ad_exists, проверка старых объявлений отключена")
            ad_exists = None
        
        for ad in ads:
            if not ad:
                continue
            
            # Извлекаем external_id (ad_id из API)
            external_id = None
            ad_id_raw = ad.get("ad_id")
            if ad_id_raw:
                external_id = f"kufar_{ad_id_raw}"
            
            # Проверка на последнее известное объявление
            if last_known_ad_id and external_id == last_known_ad_id:
                log_info("kufar", f"Обнаружено последнее известное объявление ({external_id}) — останавливаю парсинг")
                stop_parsing = True
                break
            
            # Проверка существования объявления в БД
            is_old = False
            if ad_exists and external_id:
                is_old = await ad_exists(source="kufar", ad_id=external_id)
            
            if is_old:
                consecutive_old_ads += 1
                # Проверка порога старых объявлений подряд (после увеличения счётчика)
                if consecutive_old_ads >= STOP_ON_OLD_THRESHOLD:
                    log_info("kufar", f"достигнут порог старых объявлений, останавливаюсь")
                    stop_parsing = True
                    break
                # Пропускаем старое объявление
                continue
            else:
                consecutive_old_ads = 0  # Сбрасываем счётчик при новом объявлении
            
            # Парсим объявление
            listing = self._parse_ad(ad, city)
            if listing:
                parsed_count += 1
                if self._matches_filters(listing, min_rooms, max_rooms, min_price, max_price):
                    listings.append(listing)
                    new_ads_count += 1
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
        
        return listings, stop_parsing, new_ads_count, consecutive_old_ads
    
    async def _parse_api_response(
        self, 
        data: dict,
        min_rooms: int,
        max_rooms: int,
        min_price: int,
        max_price: int,
        city: str = "Минск"
    ) -> List[Listing]:
        """Парсит ответ API (метод для совместимости, вызывает _parse_api_response_with_stop_check)"""
        listings, _, _, _ = await self._parse_api_response_with_stop_check(
            data, min_rooms, max_rooms, min_price, max_price, city, None, 0
        )
        return listings
    
    def _extract_fields_from_description(self, description: str, area: float = 0.0) -> dict:
        """Извлекает поля из описания объявления"""
        if not description:
            return {}
        
        desc_lower = description.lower()
        extracted = {}
        
        # Балкон/лоджия
        if any(word in desc_lower for word in ["балкон", "лоджия", "с балконом", "есть балкон", "балкон есть"]):
            extracted["balcony"] = "Есть"
        elif any(phrase in desc_lower for phrase in ["без балкона", "нет балкона", "балкон отсутствует"]):
            extracted["balcony"] = "Нет"
        
        # Тип санузла
        if "раздельный" in desc_lower and ("санузел" in desc_lower or "туалет" in desc_lower):
            extracted["bathroom"] = "Раздельный"
        elif "совмещенный" in desc_lower and ("санузел" in desc_lower or "туалет" in desc_lower):
            extracted["bathroom"] = "Совмещенный"
        
        # Тип дома
        if "кирпичный" in desc_lower:
            extracted["house_type"] = "Кирпичный"
        elif "панельный" in desc_lower:
            extracted["house_type"] = "Панельный"
        elif "монолитный" in desc_lower:
            extracted["house_type"] = "Монолитный"
        elif "блочный" in desc_lower:
            extracted["house_type"] = "Блочный"
        elif "деревянный" in desc_lower:
            extracted["house_type"] = "Деревянный"
        
        # Состояние ремонта
        if any(word in desc_lower for word in ["евроремонт", "евро ремонт", "капитальный ремонт", "новый ремонт", "свежий ремонт", "современный ремонт"]):
            extracted["renovation_state"] = "отличное"
        elif any(word in desc_lower for word in ["хороший ремонт", "качественный ремонт", "хорошее состояние"]):
            extracted["renovation_state"] = "хорошее"
        elif any(word in desc_lower for word in ["требует ремонта", "нужен ремонт", "под ремонт", "требует косметического ремонта", "нужен косметический ремонт"]):
            extracted["renovation_state"] = "требует ремонта"
        elif any(word in desc_lower for word in ["без ремонта", "старый ремонт", "советский ремонт", "плохое состояние"]):
            extracted["renovation_state"] = "плохое"
        
        # Площадь кухни
        kitchen_match = re.search(r'кухня\s+(\d+[.,]?\d*)\s*м[²2]', desc_lower)
        if not kitchen_match:
            kitchen_match = re.search(r'кухня\s+(\d+[.,]?\d*)', desc_lower)
        if kitchen_match:
            try:
                kitchen_area = float(kitchen_match.group(1).replace(",", "."))
                # Проверяем разумность: кухня не может быть больше общей площади или меньше 3м²
                if 3 <= kitchen_area <= 30 and (area == 0 or kitchen_area <= area):
                    extracted["kitchen_area"] = kitchen_area
            except:
                pass
        
        # Жилая площадь
        living_match = re.search(r'жилая\s+площадь\s+(\d+[.,]?\d*)\s*м[²2]', desc_lower)
        if not living_match:
            living_match = re.search(r'жилая\s+(\d+[.,]?\d*)\s*м[²2]', desc_lower)
        if living_match:
            try:
                living_area = float(living_match.group(1).replace(",", "."))
                # Проверяем разумность: жилая площадь не может быть больше общей площади
                if 10 <= living_area <= 200 and (area == 0 or living_area <= area):
                    extracted["living_area"] = living_area
            except:
                pass
        
        # Этажность дома
        floors_match = re.search(r'(\d+)\s*этажн', desc_lower)
        if not floors_match:
            floors_match = re.search(r'(\d+)\s*эт\.\s*дом', desc_lower)
        if not floors_match:
            floors_match = re.search(r'дом\s+(\d+)\s*этаж', desc_lower)
        if not floors_match:
            floors_match = re.search(r'(\d+)\s*этаж', desc_lower)
        if floors_match:
            try:
                total_floors = int(floors_match.group(1))
                if 1 <= total_floors <= 30:  # Разумные пределы
                    extracted["total_floors"] = str(total_floors)
            except:
                pass
        
        return extracted
    
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
            
            # Этажность дома (отдельно от этажа)
            total_floors = ""
            if floors_val:
                total_floors = str(floors_val)
            
            # Балкон/лоджия
            balcony = ""
            balcony_val = params.get("balcony", "")
            if balcony_val:
                # "1" = есть, "2" = нет
                if str(balcony_val) == "1":
                    balcony = "Есть"
                elif str(balcony_val) == "2":
                    balcony = "Нет"
                else:
                    balcony = str(balcony_val)
            
            # Санузел
            bathroom = ""
            bathroom_val = params.get("bathroom", "")
            if bathroom_val:
                # "1" = раздельный, "2" = совмещенный
                bathroom_text = params.get("bathroom_text", "")
                if bathroom_text:
                    bathroom = bathroom_text
                elif str(bathroom_val) == "1":
                    bathroom = "Раздельный"
                elif str(bathroom_val) == "2":
                    bathroom = "Совмещенный"
                else:
                    bathroom = str(bathroom_val)
            
            # Тип дома
            house_type = ""
            house_type_val = params.get("house_type", "")
            if house_type_val:
                house_type_text = params.get("house_type_text", "")
                if house_type_text:
                    house_type = house_type_text
                else:
                    # Маппинг числовых значений на текстовые
                    house_type_map = {
                        "1": "Панельный",
                        "2": "Блочный",
                        "3": "Кирпичный",
                        "4": "Монолитный",
                        "5": "Деревянный"
                    }
                    house_type = house_type_map.get(str(house_type_val), str(house_type_val))
            
            # Состояние ремонта
            renovation_state = ""
            condition_val = params.get("condition", "")
            flat_repair_val = params.get("flat_repair", "")
            if flat_repair_val:
                flat_repair_text = params.get("flat_repair_text", "")
                if flat_repair_text:
                    renovation_state = flat_repair_text
                else:
                    renovation_state = str(flat_repair_val)
            elif condition_val:
                condition_text = params.get("condition_text", "")
                if condition_text:
                    renovation_state = condition_text
                else:
                    renovation_state = str(condition_val)
            
            # Площадь кухни
            kitchen_area = 0.0
            kitchen_val = params.get("size_kitchen", "")
            if kitchen_val:
                try:
                    kitchen_area = float(str(kitchen_val).replace(",", "."))
                except:
                    pass
            
            # Жилая площадь
            living_area = 0.0
            living_val = params.get("size_living_space", "")
            if living_val:
                try:
                    living_area = float(str(living_val).replace(",", "."))
                except:
                    pass
            
            # Описание
            description = ""
            body_short = ad.get("body_short", "")
            body = ad.get("body", "")
            if body_short:
                description = body_short.strip()
            elif body:
                description = body.strip()
            
            # Извлекаем недостающие поля из описания
            if description:
                extracted_fields = self._extract_fields_from_description(description, area)
                
                # Заполняем только пустые поля (приоритет у API параметров)
                if not balcony and extracted_fields.get("balcony"):
                    balcony = extracted_fields["balcony"]
                    log_info("kufar", f"Извлечено из описания: балкон={balcony}")
                
                if not bathroom and extracted_fields.get("bathroom"):
                    bathroom = extracted_fields["bathroom"]
                    log_info("kufar", f"Извлечено из описания: санузел={bathroom}")
                
                if not house_type and extracted_fields.get("house_type"):
                    house_type = extracted_fields["house_type"]
                    log_info("kufar", f"Извлечено из описания: тип дома={house_type}")
                
                if not renovation_state and extracted_fields.get("renovation_state"):
                    renovation_state = extracted_fields["renovation_state"]
                    log_info("kufar", f"Извлечено из описания: состояние ремонта={renovation_state}")
                
                if kitchen_area == 0.0 and extracted_fields.get("kitchen_area"):
                    kitchen_area = extracted_fields["kitchen_area"]
                    log_info("kufar", f"Извлечено из описания: площадь кухни={kitchen_area}")
                
                if living_area == 0.0 and extracted_fields.get("living_area"):
                    living_area = extracted_fields["living_area"]
                    log_info("kufar", f"Извлечено из описания: жилая площадь={living_area}")
                
                if not total_floors and extracted_fields.get("total_floors"):
                    total_floors = extracted_fields["total_floors"]
                    # Обновляем floor если нужно
                    if floor and "/" not in str(floor):
                        floor = f"{floor}/{total_floors}"
                    log_info("kufar", f"Извлечено из описания: этажность={total_floors}")
            
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
            
            # Валидация через DTO перед созданием полного Listing
            dto = self.validate_listing_data(
                title=title,
                price=price,
                url=ad_link,
                location=address,
                source="Kufar.by"
            )
            
            if not dto:
                # Данные не прошли валидацию
                log_warning("kufar", f"Объявление не прошло валидацию DTO: title='{title[:50]}...', price={price}, url={ad_link[:50]}...")
                return None
            
            # Создаем полный Listing объект из валидного DTO
            return Listing(
                id=listing_id,
                source=dto.source,
                title=dto.title,
                price=dto.price,
                price_formatted=price_formatted,
                rooms=rooms if rooms else 0,
                area=area if area else 0.0,
                floor=floor,
                address=dto.location,
                photos=photos,
                url=dto.url,
                currency=currency,
                price_usd=price_usd,
                price_byn=price_byn,
                year_built=year_built,
                created_at=created_at,
                is_company=is_company,
                description=description,
                balcony=balcony,
                bathroom=bathroom,
                total_floors=total_floors,
                house_type=house_type,
                renovation_state=renovation_state,
                kitchen_area=kitchen_area,
                living_area=living_area,
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
