"""
ИИ-оценщик квартир - бесплатные варианты интеграции
"""
import os
import sys
import asyncio
import aiohttp
import json
import re
from typing import Optional, Dict, Any, List
from bs4 import BeautifulSoup
from scrapers.base import Listing

# Добавляем путь для импорта error_logger
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from error_logger import log_error, log_warning, log_info
except ImportError:
    # Fallback если error_logger недоступен
    def log_error(source, msg, exc=None):
        print(f"[ERROR] [{source}] {msg}: {exc}")
    def log_warning(source, msg):
        print(f"[WARNING] [{source}] {msg}")
    def log_info(source, msg):
        print(f"[INFO] [{source}] {msg}")

# ========== Groq API (РЕКОМЕНДУЕТСЯ) ==========
# Бесплатно: 30 запросов/минуту, очень быстро
# НЕ поддерживает анализ изображений
# Регистрация: https://console.groq.com/
# Получить API ключ: https://console.groq.com/keys

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
GROQ_VISION_MODEL = None  # Vision модель недоступна
GROQ_FALLBACK_MODEL = "mixtral-8x7b-32768"


# ========== ВАРИАНТ 3: Hugging Face Inference API ==========
# Бесплатно: ограниченное количество запросов
# Регистрация: https://huggingface.co/
# Получить токен: https://huggingface.co/settings/tokens

HF_API_KEY = os.getenv("HF_API_KEY", "")
HF_API_URL = "https://api-inference.huggingface.co/models/meta-llama/Meta-Llama-3-8B-Instruct"


# ========== ВАРИАНТ 4: Ollama (локально) ==========
# Полностью бесплатно, но нужно запускать локально
# Установка: https://ollama.ai/
# Запуск: ollama run llama3

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")


class AIValuator:
    """ИИ-оценщик квартир"""
    
    def __init__(self, provider: str = "groq"):
        """
        Инициализация оценщика
        
        Args:
            provider: "groq", "huggingface", "ollama"
        """
        self.provider = provider.lower()
        self.session: Optional[aiohttp.ClientSession] = None
    
    async def start_session(self):
        """Создает HTTP сессию"""
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
    
    async def close_session(self):
        """Закрывает HTTP сессию"""
        if self.session and not self.session.closed:
            await self.session.close()
    
    async def __aenter__(self):
        await self.start_session()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close_session()
    
    def _prepare_prompt(self, listing: Listing, inspection: Optional[Dict[str, Any]] = None) -> str:
        """Подготавливает промпт для ИИ"""
        # Определяем текущую цену в USD
        current_price_usd = listing.price_usd if listing.price_usd else (
            int(listing.price_byn / 2.95) if listing.price_byn else (
                int(listing.price / 2.95) if listing.currency == "BYN" else listing.price
            )
        )
        
        # Определяем цену за м² в USD
        price_per_sqm_usd = 0
        if listing.price_per_sqm > 0:
            # Если цена за м² указана в основной валюте
            if listing.currency == "USD":
                price_per_sqm_usd = listing.price_per_sqm
            elif listing.currency == "BYN":
                price_per_sqm_usd = int(listing.price_per_sqm / 2.95)
        elif listing.area > 0 and current_price_usd > 0:
            # Вычисляем цену за м² из общей цены
            price_per_sqm_usd = int(current_price_usd / listing.area)
        
        price_per_sqm_text = f"{price_per_sqm_usd} USD/м²" if price_per_sqm_usd > 0 else "не указана"
        
        # Анализируем этаж для оценки
        floor_info = ""
        floor_bonus = 0
        if listing.floor:
            try:
                floor_parts = listing.floor.split("/")
                if len(floor_parts) == 2:
                    floor_num = int(floor_parts[0])
                    total_floors = int(floor_parts[1])
                    if floor_num == 1:
                        floor_info = " (первый этаж - минус 5-10%)"
                        floor_bonus = -5
                    elif floor_num == total_floors:
                        floor_info = " (последний этаж - минус 3-5%)"
                        floor_bonus = -3
                    elif 2 <= floor_num <= total_floors - 1:
                        floor_info = " (средний этаж - оптимально)"
                        floor_bonus = 0
            except:
                pass
        
        # Анализируем год постройки
        year_info = ""
        year_bonus = 0
        if listing.year_built:
            try:
                year = int(listing.year_built)
                if year >= 2010:
                    year_info = " (новая постройка +5-10%)"
                    year_bonus = 5
                elif year >= 2000:
                    year_info = " (современная постройка)"
                    year_bonus = 0
                elif year >= 1980:
                    year_info = " (старая постройка -3-5%)"
                    year_bonus = -3
                else:
                    year_info = " (очень старая постройка -5-10%)"
                    year_bonus = -5
            except:
                pass
        
        # Анализируем описание
        description_text = listing.description.strip() if listing.description else ""
        description_analysis = ""
        renovation_state = "не указано"
        renovation_bonus = 0
        
        if description_text:
            desc_lower = description_text.lower()
            # Определяем состояние ремонта из описания
            if any(word in desc_lower for word in ['евроремонт', 'евро ремонт', 'капитальный ремонт', 'новый ремонт', 'свежий ремонт']):
                renovation_state = "отличное (евроремонт)"
                renovation_bonus = 10
            elif any(word in desc_lower for word in ['хороший ремонт', 'качественный ремонт', 'современный ремонт']):
                renovation_state = "хорошее"
                renovation_bonus = 5
            elif any(word in desc_lower for word in ['требует ремонта', 'нужен ремонт', 'под ремонт', 'требует косметического ремонта']):
                renovation_state = "требует ремонта"
                renovation_bonus = -10
            elif any(word in desc_lower for word in ['без ремонта', 'старый ремонт', 'советский ремонт']):
                renovation_state = "плохое"
                renovation_bonus = -15
            else:
                renovation_state = "среднее"
                renovation_bonus = 0
            
            # Извлекаем ключевые особенности из описания
            features = []
            if 'балкон' in desc_lower or 'лоджия' in desc_lower:
                features.append("балкон/лоджия")
            if 'парковка' in desc_lower or 'гараж' in desc_lower:
                features.append("парковка")
            if 'лифт' in desc_lower:
                features.append("лифт")
            if 'охрана' in desc_lower or 'консьерж' in desc_lower:
                features.append("охрана")
            if 'школа' in desc_lower or 'сад' in desc_lower:
                features.append("рядом школа/сад")
            
            if features:
                description_analysis = f"\n- Особенности: {', '.join(features)}"
        
        # Определяем район по адресу (используем данные инспекции если есть)
        address_to_check = listing.address.lower()
        if inspection and inspection.get("address_details"):
            address_to_check = inspection["address_details"].lower()
        if inspection and inspection.get("is_center"):
            # Если инспекция определила центр, используем это
            address_to_check = "центр " + address_to_check
        
        address_lower = address_to_check
        district = "не определен"
        district_prices = {}
        
        if inspection and inspection.get("is_center"):
            district = "Центр"
            district_prices = {
                "1-комн": (24000, 28500, 650, 840),
                "2-комн": (29000, 43500, 650, 840),
                "3-комн": (35000, 50000, 650, 840),
                "тип": "Сталинки, кирпичные дома 60-70х",
                "характеристики": "Престижный район, развитая инфраструктура, старый фонд"
            }
        elif any(word in address_lower for word in ['советская', 'брестская', 'центр', 'центральная', 'ленина', 'мир', 'кирова', 'комсомольская', 'пионерская', 'горького', 'пушкина', 'машерова']):
            district = "Центр"
            district_prices = {
                "1-комн": (24000, 28500, 650, 840),
                "2-комн": (29000, 43500, 650, 840),
                "3-комн": (35000, 50000, 650, 840),
                "тип": "Сталинки, кирпичные дома 60-70х",
                "характеристики": "Престижный район, развитая инфраструктура, старый фонд"
            }
        elif any(word in address_lower for word in ['боровки', 'волошина', 'марфицкого', 'машерова']):
            district = "Боровки"
            district_prices = {
                "1-комн": (26000, 39900, 510, 620),
                "2-комн": (35000, 46500, 510, 620),
                "3-комн": (40000, 55000, 510, 620),
                "тип": "Современные панели (после 2010 г.)",
                "характеристики": "Современное градостроительство, новые лифты, энергоэффективность"
            }
        elif any(word in address_lower for word in ['наконечникова', 'жукова', 'северный', 'промышленная']):
            district = "Северный"
            district_prices = {
                "1-комн": (19000, 23500, 480, 580),
                "2-комн": (25000, 38000, 480, 580),
                "3-комн": (30000, 42000, 480, 580),
                "тип": "Панели 80-х, малосемейки",
                "характеристики": "Высокая плотность застройки, доступные цены"
            }
        elif any(word in address_lower for word in ['коммунистическая', 'южный']):
            district = "Южный"
            district_prices = {
                "1-комн": (18500, 24000, 450, 550),
                "2-комн": (24000, 33000, 450, 550),
                "3-комн": (28000, 40000, 450, 550),
                "тип": "Панели и кирпич 70-80х",
                "характеристики": "Доступные цены, стабильный район"
            }
        elif any(word in address_lower for word in ['тельмана', 'энтузиастов', 'восточный']):
            district = "Восточный"
            district_prices = {
                "1-комн": (20000, 26000, 470, 590),
                "2-комн": (26500, 36000, 470, 590),
                "3-комн": (32000, 45000, 470, 590),
                "тип": "Панели 80-х, улучшенные планировки",
                "характеристики": "Изолированные комнаты, кухни от 7 м², высокая ликвидность"
            }
        elif any(word in address_lower for word in ['космонавтов', 'текстильный']):
            district = "Текстильный"
            district_prices = {
                "1-комн": (17500, 21000, 440, 510),
                "2-комн": (22000, 28000, 440, 510),
                "3-комн": (26000, 35000, 440, 510),
                "тип": "Хрущевки, малоэтажный кирпич",
                "характеристики": "Самые доступные цены, старый фонд"
            }
        
        # Определяем ценовой диапазон для данного типа квартиры
        room_key = f"{listing.rooms}-комн" if listing.rooms > 0 else "1-комн"
        if room_key not in district_prices:
            room_key = "2-комн" if listing.rooms >= 2 else "1-комн"
        
        district_price_info = ""
        if district != "не определен" and district_prices:
            min_price, max_price, min_sqm, max_sqm = district_prices.get(room_key, (0, 0, 0, 0))[:4]
            if min_price > 0:
                district_price_info = f"\n- Район: {district} ({district_prices.get('тип', '')})\n"
                district_price_info += f"  Средние цены в районе: ${min_price:,}-${max_price:,} (${min_sqm}-${max_sqm}/м²)"
        
        # Формируем строку с ценой для промпта - явно указываем валюту
        price_info = f"${current_price_usd:,} USD"
        if listing.currency == "BYN" and listing.price_byn:
            price_info += f" (оригинальная цена: {listing.price_byn:,} BYN)"
        
        # Определяем город из адреса
        city_from_address = "Беларусь"
        address_lower = listing.address.lower()
        if "минск" in address_lower:
            city_from_address = "Минск"
        elif "барановичи" in address_lower:
            city_from_address = "Барановичи"
        elif "брест" in address_lower:
            city_from_address = "Брест"
        elif "гомель" in address_lower:
            city_from_address = "Гомель"
        elif "гродно" in address_lower:
            city_from_address = "Гродно"
        elif "витебск" in address_lower:
            city_from_address = "Витебск"
        elif "могилев" in address_lower or "могилёв" in address_lower:
            city_from_address = "Могилёв"
        
        # Проверка на долю/комнату в названии и описании
        title_text = listing.title.lower() if listing.title else ""
        description_lower = description_text.lower() if description_text else ""
        combined_text = f"{title_text} {description_lower}"
        
        is_share_or_room = False
        share_warning = ""
        share_keywords = ["доля", "часть квартиры", "1/2", "1/3", "1/4", "половина", "комната в", "комната в коммуналке", "комната в общежитии", "не целая", "часть собственности"]
        
        for keyword in share_keywords:
            if keyword in combined_text:
                is_share_or_room = True
                if "доля" in combined_text or "1/2" in combined_text or "1/3" in combined_text or "1/4" in combined_text:
                    share_warning = "\n⚠️ ВНИМАНИЕ: Это ДОЛЯ в квартире, а не целая квартира! Цена должна быть значительно ниже рынка целых квартир."
                elif "комната" in combined_text:
                    share_warning = "\n⚠️ ВНИМАНИЕ: Это КОМНАТА в квартире/коммуналке, а не целая квартира! Цена должна быть значительно ниже рынка целых квартир."
                break
        
        prompt = f"""Оценщик недвижимости {city_from_address}, Беларусь. Оцени квартиру:

ОБЪЕКТ: {listing.rooms}к, {listing.area}м², этаж {listing.floor or '?'}{floor_info}, год {listing.year_built or '?'}{year_info}
Адрес: {listing.address}{district_price_info}
Цена: {price_info}, {price_per_sqm_text}
Ремонт: {renovation_state}
{f'Название: {listing.title[:100]}' if listing.title else ''}
{f'Описание: {description_text[:300]}' if description_text else ''}
{share_warning}

РЫНОК 2025 Беларусь ($/м²): Минск $1200-2500, обл.центры $600-1200, районные $400-800
Корректировки: 1й/последний этаж -5%, до 1980г -7%, евроремонт +10%, требует ремонта -10%
{'- ДОЛЯ/КОМНАТА: цена должна быть на 40-60% ниже целой квартиры!' if is_share_or_room else ''}

ЗАДАЧА: Определи справедливую цену для {city_from_address}, сравни с рынком города, дай рекомендации.

JSON ответ:
{{"fair_price_usd": число, "is_overpriced": true/false, "assessment": "оценка 2-3 предложения", "renovation_state": "отличное/хорошее/среднее/требует ремонта/плохое", "recommendations": "что проверить, стоит ли покупать", "value_score": 1-10}}"""
        return prompt
    
    async def _inspect_listing_page(self, listing: Listing) -> Dict[str, Any]:
        """Инспектирует страницу объявления для получения детальной информации"""
        inspection_data = {
            "full_description": "",
            "all_photos": [],
            "detailed_info": {},
            "address_details": "",
            "is_center": False
        }
        
        try:
            log_info("ai_inspect", f"Инспектирую страницу: {listing.url}")
            
            # Загружаем HTML страницы
            async with self.session.get(listing.url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    soup = BeautifulSoup(html, 'lxml')
                    
                    # Извлекаем полное описание
                    description_selectors = [
                        'div.description', 'div[class*="description"]', 
                        'div[class*="text"]', 'div.content', 'p.description',
                        'div[itemprop="description"]', 'meta[property="og:description"]'
                    ]
                    for selector in description_selectors:
                        desc_elem = soup.select_one(selector)
                        if desc_elem:
                            inspection_data["full_description"] = desc_elem.get_text(strip=True)
                            if len(inspection_data["full_description"]) > 100:
                                break
                    
                    # Если не нашли через селекторы, ищем в мета-тегах
                    if not inspection_data["full_description"]:
                        meta_desc = soup.find('meta', property='og:description')
                        if meta_desc:
                            inspection_data["full_description"] = meta_desc.get('content', '')
                    
                    # Извлекаем фото объявления (только из галереи/фото объявления)
                    photo_urls = set()
                    
                    # Стратегия 1: Ищем контейнеры галереи/фото объявления
                    gallery_selectors = [
                        'div[class*="gallery"]', 'div[class*="photos"]', 'div[class*="images"]',
                        'div[class*="slider"]', 'div[class*="carousel"]', 'div[class*="photo"]',
                        'section[class*="gallery"]', 'section[class*="photos"]',
                        '[data-gallery]', '[data-photos]', '[id*="gallery"]', '[id*="photos"]'
                    ]
                    
                    gallery_container = None
                    for selector in gallery_selectors:
                        gallery_container = soup.select_one(selector)
                        if gallery_container:
                            log_info("ai_inspect", f"Найден контейнер галереи: {selector}")
                            break
                    
                    # Стратегия 2: Если не нашли галерею, ищем по специфичным паттернам URL
                    if gallery_container:
                        # Ищем фото только в контейнере галереи
                        img_tags = gallery_container.find_all('img', src=True)
                    else:
                        # Если галереи нет, ищем все img, но с очень строгой фильтрацией
                        img_tags = soup.find_all('img', src=True)
                    
                    for img in img_tags:
                        img_src = img.get('src') or img.get('data-src') or img.get('data-lazy') or img.get('data-original')
                        if not img_src:
                            continue
                        
                        img_src_lower = img_src.lower()
                        
                        # Строгая фильтрация служебных изображений
                        skip_patterns = [
                            'logo', 'icon', 'sprite', 'placeholder', 'no-photo', 'blank',
                            'footer', 'header', 'arrow', 'button', 'badge', 'pay', 'mastercard',
                            'visa', 'belkart', 'svg', 'favicon', 'social', 'facebook', 'instagram',
                            'telegram', 'viber', 'whatsapp', 'youtube', 'twitter', 'rewar',  # reward/награда
                            'static/frontend', 'static/img', 'static/images', 'cdn', 'assets'
                        ]
                        
                        # Пропускаем если содержит служебные паттерны
                        if any(p in img_src_lower for p in skip_patterns):
                            continue
                        
                        # Для Kufar: только фото из gallery или list_thumbs (но не маленькие превью)
                        if 'kufar.by' in img_src_lower:
                            if 'gallery' in img_src_lower or 'list_thumbs_2x' in img_src_lower:
                                # Пропускаем очень маленькие превью
                                if '120x100' in img_src_lower or 'thumb' in img_src_lower:
                                    if 'list_thumbs_2x' not in img_src_lower:  # list_thumbs_2x - это нормальные фото
                                        continue
                            elif 'content.kufar.by' in img_src_lower:
                                # Пропускаем статический контент (логотипы, иконки)
                                continue
                            else:
                                # Пропускаем другие изображения с kufar.by
                                continue
                        
                        # Для Etagi: только фото объявлений
                        if 'etagi.com' in img_src_lower:
                            if 'realty' not in img_src_lower and 'object' not in img_src_lower:
                                continue
                        
                        # Пропускаем очень маленькие изображения (меньше 200px)
                        if re.search(r'/\d+x\d+/', img_src_lower):
                            size_match = re.search(r'/(\d+)x(\d+)/', img_src_lower)
                            if size_match:
                                width = int(size_match.group(1))
                                height = int(size_match.group(2))
                                if width < 200 or height < 200:
                                    continue
                        
                        # Нормализуем URL
                        if not img_src.startswith('http'):
                            if img_src.startswith('//'):
                                img_src = f"https:{img_src}"
                            else:
                                base_url = '/'.join(listing.url.split('/')[:3])
                                img_src = f"{base_url}{img_src}"
                        
                        photo_urls.add(img_src)
                    
                    # Сортируем фото по размеру (приоритет большим) и берем до 10
                    photo_list = list(photo_urls)
                    # Сортируем: сначала большие фото (gallery, list_thumbs_2x), потом остальные
                    photo_list.sort(key=lambda x: (
                        0 if 'gallery' in x.lower() or 'list_thumbs_2x' in x.lower() else 1,
                        -len(x)  # Длиннее URL обычно = больше фото
                    ))
                    inspection_data["all_photos"] = photo_list[:10]  # Максимум 10 фото
                    
                    if len(photo_list) > 0:
                        log_info("ai_inspect", f"Найдено {len(photo_list)} фото объявления (первые 3: {[p[:50] for p in photo_list[:3]]})")
                    else:
                        log_warning("ai_inspect", "Фото объявления не найдены, возможно неправильные селекторы")
                    
                    # Извлекаем детальную информацию
                    # Ищем таблицы с параметрами
                    for table in soup.find_all(['table', 'dl', 'ul', 'div']):
                        text = table.get_text()
                        if any(keyword in text.lower() for keyword in ['площадь', 'этаж', 'год', 'комнат', 'постройки', 'строительства']):
                            inspection_data["detailed_info"]["params_table"] = text[:500]
                    
                    # Используем год постройки из API (если есть)
                    # Не ищем год на странице - используем только данные из API
                    if listing.year_built:
                        inspection_data["detailed_info"]["year_built"] = listing.year_built
                        log_info("ai_inspect", f"Использую год постройки из API: {listing.year_built}")
                    
                    # Анализируем адрес для определения центра и района
                    address_text = listing.address.lower()
                    
                    # Ключевые слова для центра Барановичей
                    center_keywords = [
                        'советская', 'брестская', 'центральная', 'ленина', 'мир', 'кирова',
                        'комсомольская', 'пионерская', 'горького', 'пушкина', 'машерова'
                    ]
                    inspection_data["is_center"] = any(keyword in address_text for keyword in center_keywords)
                    
                    # Извлекаем дополнительные детали адреса из страницы
                    address_selectors = [
                        'div[class*="address"]', 'div[class*="location"]', 'div[class*="street"]',
                        'span[class*="address"]', 'p[class*="address"]', '[itemprop="address"]'
                    ]
                    for selector in address_selectors:
                        addr_elem = soup.select_one(selector)
                        if addr_elem:
                            addr_text = addr_elem.get_text(strip=True)
                            if len(addr_text) > len(listing.address):
                                inspection_data["address_details"] = addr_text
                                # Обновляем is_center на основе детального адреса
                                if not inspection_data["is_center"]:
                                    inspection_data["is_center"] = any(keyword in addr_text.lower() for keyword in center_keywords)
                                break
                    
                    # Если не нашли через селекторы, используем исходный адрес
                    if not inspection_data["address_details"]:
                        inspection_data["address_details"] = listing.address
                    
                    log_info("ai_inspect", f"Найдено: {len(inspection_data['all_photos'])} фото, описание: {len(inspection_data['full_description'])} символов")
                    
        except Exception as e:
            log_warning("ai_inspect", f"Ошибка инспекции страницы {listing.url}: {e}")
        
        return inspection_data
    
    async def _download_image_base64(self, image_url: str) -> Optional[str]:
        """Загружает изображение и конвертирует в base64"""
        try:
            async with self.session.get(image_url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    import base64
                    image_data = await resp.read()
                    base64_image = base64.b64encode(image_data).decode('utf-8')
                    # Определяем тип изображения
                    content_type = resp.headers.get('Content-Type', 'image/jpeg')
                    return f"data:{content_type};base64,{base64_image}"
        except Exception as e:
            log_warning("ai_photo", f"Ошибка загрузки фото {image_url}: {e}")
        return None
    
    async def valuate_groq(self, listing: Listing) -> Optional[Dict[str, Any]]:
        """Оценка через Groq API с поддержкой инспекции страницы и анализа фото"""
        if not GROQ_API_KEY:
            return None
        
        # ИНСПЕКЦИЯ СТРАНИЦЫ ОБЪЯВЛЕНИЯ
        log_info("ai_inspect", f"Начинаю инспекцию объявления: {listing.url}")
        inspection = await self._inspect_listing_page(listing)
        
        # Используем полное описание если есть
        full_description = inspection.get("full_description", "")
        if full_description and len(full_description) > len(listing.description):
            listing.description = full_description
        
        # Используем все фото со страницы если их больше
        all_photos = inspection.get("all_photos", [])
        if len(all_photos) > len(listing.photos):
            listing.photos = all_photos[:10]  # Максимум 10 фото
        
        # Обновляем информацию о центре
        is_center = inspection.get("is_center", False)
        address_details = inspection.get("address_details", "")
        
        prompt = self._prepare_prompt(listing, inspection)
        
        # Информация о фото (Groq не поддерживает vision в текущем формате)
        photos_count = len(listing.photos) if listing.photos else 0
        
        # Добавляем текстовый промпт с информацией об инспекции
        photo_prompt = ""
        if photos_count > 0:
            photo_prompt = f"\n\nИНФОРМАЦИЯ О ФОТОГРАФИЯХ:\n"
            photo_prompt += f"На объявлении размещено {photos_count} фотографий квартиры. "
            photo_prompt += "Используй описание выше для оценки состояния ремонта и качества квартиры. "
            photo_prompt += "Если в описании упоминается состояние ремонта, мебель, техника - учитывай это при оценке.\n"
        
        # Информация об инспекции
        inspection_info = ""
        if inspection.get("full_description"):
            inspection_info += f"\n\nПОЛНОЕ ОПИСАНИЕ С САЙТА:\n{inspection['full_description'][:800]}\n"
        if address_details:
            inspection_info += f"\nДЕТАЛИ АДРЕСА: {address_details}\n"
        if is_center:
            inspection_info += "\nРАСПОЛОЖЕНИЕ: Центральный район города (престижный, развитая инфраструктура)\n"
        
        # Используем обычную модель (vision временно отключена)
        model_to_use = GROQ_MODEL
        
        payload = {
            "messages": [
                {
                    "role": "system", 
                    "content": """Ты профессиональный оценщик недвижимости Беларуси с 10+ летним опытом.
Ты даешь КОНКРЕТНЫЕ оценки с практическими советами для покупателя.

ВАЖНО:
- Определяй город и район по адресу
- Учитывай рыночные цены для данного города (Минск дороже, районные города дешевле)
- Дай КОНКРЕТНЫЕ рекомендации специфичные для этой квартиры

Отвечай ТОЛЬКО валидным JSON:
{
    "fair_price_usd": число,
    "is_overpriced": true/false,
    "assessment": "оценка 2-3 предложения с фактами",
    "renovation_state": "отличное/хорошее/среднее/требует ремонта/плохое",
    "recommendations": "советы покупателю",
    "value_score": число от 1 до 10
}"""
                },
                {"role": "user", "content": prompt + photo_prompt + inspection_info}
            ],
            "model": model_to_use,
            "temperature": 0.2,  # Снижена для более точных оценок
            "max_tokens": 800  # Увеличено для детальных ответов с инспекцией
        }
        
        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        }
        
        try:
            timeout = aiohttp.ClientTimeout(total=10)  # 10 секунд таймаут
            async with self.session.post(GROQ_API_URL, json=payload, headers=headers, timeout=timeout) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    content = data["choices"][0]["message"]["content"]
                    log_info("ai_groq", f"Получен ответ от Groq: {content[:100]}...")
                    # Парсим JSON из ответа
                    result = self._parse_ai_response(content)
                    if result:
                        fair_price = result.get('fair_price_usd', 0)
                        has_renovation = 'renovation_state' in result and result.get('renovation_state')
                        has_recommendations = 'recommendations' in result and result.get('recommendations')
                        has_value_score = 'value_score' in result and result.get('value_score', 0) > 0
                        
                        log_info("ai_groq", f"Успешная оценка: ${fair_price:,}")
                        if has_renovation:
                            log_info("ai_groq", f"  - Состояние ремонта: {result.get('renovation_state')}")
                        if has_recommendations:
                            log_info("ai_groq", f"  - Рекомендации: {result.get('recommendations')[:50]}...")
                        if has_value_score:
                            log_info("ai_groq", f"  - Оценка: {result.get('value_score')}/10")
                    return result
                elif resp.status == 400:
                    # Если модель недоступна, пробуем резервную
                    error_text = await resp.text()
                    log_warning("ai_groq", f"Groq API вернул статус {resp.status}: {error_text[:200]}")
                    if "decommissioned" in error_text.lower() or "not found" in error_text.lower():
                        log_info("ai_groq", f"Пробую резервную модель: {GROQ_FALLBACK_MODEL}")
                        payload["model"] = GROQ_FALLBACK_MODEL
                        async with self.session.post(GROQ_API_URL, json=payload, headers=headers, timeout=timeout) as resp2:
                            if resp2.status == 200:
                                data = await resp2.json()
                                content = data["choices"][0]["message"]["content"]
                                return self._parse_ai_response(content)
                elif resp.status == 429:
                    # Rate limit - ждем и пробуем еще раз
                    error_text = await resp.text()
                    log_warning("ai_groq", f"Rate limit достигнут. Жду 5 секунд...")
                    await asyncio.sleep(5)
                    # Пробуем еще раз с меньшим промптом или пропускаем
                    log_info("ai_groq", "Пропускаю ИИ-оценку из-за rate limit")
                    return None
                else:
                    error_text = await resp.text()
                    log_warning("ai_groq", f"Groq API вернул статус {resp.status}: {error_text[:200]}")
        except asyncio.TimeoutError:
            log_warning("ai_groq", "Таймаут запроса к Groq API")
        except Exception as e:
            log_error("ai_groq", f"Ошибка запроса к Groq API", e)
        
        return None
    
    async def valuate_huggingface(self, listing: Listing) -> Optional[Dict[str, Any]]:
        """Оценка через Hugging Face API"""
        if not HF_API_KEY:
            return None
        
        prompt = self._prepare_prompt(listing)
        
        payload = {
            "inputs": prompt,
            "parameters": {
                "max_new_tokens": 300,
                "temperature": 0.3,
                "return_full_text": False
            }
        }
        
        headers = {
            "Authorization": f"Bearer {HF_API_KEY}",
            "Content-Type": "application/json"
        }
        
        try:
            async with self.session.post(HF_API_URL, json=payload, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    content = data[0]["generated_text"]
                    return self._parse_ai_response(content)
        except Exception as e:
            print(f"[AI] HuggingFace ошибка: {e}")
        
        return None
    
        """Получает список доступных моделей Gemini через API"""
        try:
            models_url = f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_API_KEY}"
            async with self.session.get(models_url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    models = []
                    # Список проверенных стабильных моделей (приоритет)
                    stable_models = [
                        "gemini-1.5-flash-latest",
                        "gemini-1.5-pro-latest",
                        "gemini-1.5-flash",
                        "gemini-1.5-pro",
                        "gemini-pro"
                    ]
                    
                    for m in data.get("models", []):
                        name = m.get("name", "")
                        if name and "gemini" in name.lower():
                            # Убираем префикс "models/"
                            model_name = name.replace("models/", "")
                            # Проверяем что модель поддерживает generateContent
                            supported_methods = m.get("supportedGenerationMethods", [])
                            if "generateContent" in supported_methods:
                                # Фильтруем только стабильные модели (исключаем экспериментальные версии 2.0, 2.5)
                                if any(stable in model_name for stable in ["gemini-1.5", "gemini-pro"]) and not any(exp in model_name for exp in ["2.0", "2.5", "exp"]):
                                models.append(model_name)
                    
                    # Сортируем: сначала стабильные модели из списка
                    models_sorted = []
                    for stable_model in stable_models:
                        for model in models:
                            if model == stable_model or model.startswith(stable_model.split("-latest")[0]):
                                if model not in models_sorted:
                                    models_sorted.append(model)
                    
                    # Добавляем остальные модели
                    for model in models:
                        if model not in models_sorted:
                            models_sorted.append(model)
                    
                    if models_sorted:
                        log_info("ai_gemini", f"Найдено доступных моделей: {models_sorted[:5]}")
                    return models_sorted[:5] if models_sorted else []
        except Exception as e:
            log_warning("ai_gemini", f"Не удалось получить список моделей: {e}")
        return []
    
    # Удалено: функция valuate_gemini - Gemini больше не используется
    
    async def valuate_gemini_removed(self, listing: Listing) -> Optional[Dict[str, Any]]:
        """Оценка через Google Gemini API с поддержкой анализа фото"""
        if not GEMINI_API_KEY:
            return None
        
        # ИНСПЕКЦИЯ СТРАНИЦЫ ОБЪЯВЛЕНИЯ
        log_info("ai_inspect", f"Начинаю инспекцию объявления: {listing.url}")
        inspection = await self._inspect_listing_page(listing)
        
        # Используем полное описание если есть
        full_description = inspection.get("full_description", "")
        if full_description and len(full_description) > len(listing.description):
            listing.description = full_description
        
        # Используем все фото со страницы если их больше
        all_photos = inspection.get("all_photos", [])
        if len(all_photos) > len(listing.photos):
            listing.photos = all_photos[:10]  # Максимум 10 фото
        
        # Обновляем информацию о центре
        is_center = inspection.get("is_center", False)
        address_details = inspection.get("address_details", "")
        
        prompt = self._prepare_prompt(listing, inspection)
        
        # Информация об инспекции
        inspection_info = ""
        if inspection.get("full_description"):
            inspection_info += f"\n\nПОЛНОЕ ОПИСАНИЕ С САЙТА:\n{inspection['full_description'][:1000]}\n"
        if address_details:
            inspection_info += f"\nДЕТАЛИ АДРЕСА: {address_details}\n"
        if is_center:
            inspection_info += "\nРАСПОЛОЖЕНИЕ: Центральный район города (престижный, развитая инфраструктура)\n"
        
        # Подготавливаем части сообщения (текст + фото)
        parts = []
        
        # Добавляем текстовый промпт
        final_prompt = prompt + inspection_info
        parts.append({"text": final_prompt})
        
        # Добавляем фото для анализа (максимум 5 фото для детального анализа)
        photos_added = 0
        photos_to_analyze = listing.photos[:5] if listing.photos else []
        
        for photo_url in photos_to_analyze:
            if photos_added >= 5:
                break
            base64_image = await self._download_image_base64(photo_url)
            if base64_image:
                # Извлекаем base64 без data: префикса для Gemini
                if base64_image.startswith("data:"):
                    base64_data = base64_image.split(",", 1)[1]
                else:
                    base64_data = base64_image
                
                parts.append({
                    "inline_data": {
                        "mime_type": "image/jpeg",
                        "data": base64_data
                    }
                })
                photos_added += 1
                log_info("ai_photo", f"Добавлено фото для анализа Gemini: {photo_url[:50]}...")
        
        # Добавляем инструкции по анализу фото
        if photos_added > 0:
            photo_instructions = f"\n\nВНИМАНИЕ: К сообщению прикреплены {photos_added} фото(графий) квартиры с сайта объявления. "
            photo_instructions += "Ты должен ВНИМАТЕЛЬНО проанализировать каждое фото и дать КОНКРЕТНУЮ оценку:\n"
            photo_instructions += "1. Состояние ремонта: отличное/хорошее/среднее/требует ремонта/плохое (опиши ЧТО видно на фото)\n"
            photo_instructions += "2. Качество отделки: опиши состояние стен, пола, потолка, дверей, окон\n"
            photo_instructions += "3. Мебель и техника: что есть, в каком состоянии\n"
            photo_instructions += "4. Общее впечатление: чистота, ухоженность, готовность к проживанию\n"
            photo_instructions += "5. Потенциальные проблемы: что нужно проверить/отремонтировать\n"
            photo_instructions += "Используй эту ВИЗУАЛЬНУЮ информацию для точной оценки стоимости.\n"
            
            # Добавляем инструкции в начало текста
            parts[0]["text"] = photo_instructions + parts[0]["text"]
        
        # Используем одну модель для всего (gemini-1.5-flash/pro поддерживают vision)
        url = f"{GEMINI_API_URL}?key={GEMINI_API_KEY}"
        if photos_added > 0:
            log_info("ai_gemini", f"Использую {GEMINI_MODEL} для анализа {photos_added} фото")
        else:
            log_info("ai_gemini", f"Использую {GEMINI_MODEL} для текстового анализа")
        
        payload = {
            "contents": [{
                "parts": parts
            }],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 800
            }
        }
        
        try:
            timeout = aiohttp.ClientTimeout(total=20)  # 20 секунд для анализа фото
            async with self.session.post(url, json=payload, timeout=timeout) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    content = data["candidates"][0]["content"]["parts"][0]["text"]
                    log_info("ai_gemini", f"Получен ответ от Gemini: {content[:100]}...")
                    result = self._parse_ai_response(content)
                    if result:
                        log_info("ai_gemini", f"Успешная оценка: ${result.get('fair_price_usd', 0):,}")
                        log_info("ai_gemini", f"  - Состояние ремонта: {result.get('renovation_state', 'N/A')}")
                        log_info("ai_gemini", f"  - Рекомендации: {result.get('recommendations', 'N/A')[:50]}...")
                        log_info("ai_gemini", f"  - Оценка: {result.get('value_score', 'N/A')}/10")
                    return result
                elif resp.status == 429:
                    # Rate limit - ждем и возвращаем None (не повторяем автоматически для оценки)
                    error_text = await resp.text()
                    log_warning("ai_gemini", f"Rate limit достигнут (429). Жду 60 секунд перед следующей попыткой...")
                    await asyncio.sleep(60)  # Ждем минуту перед следующей попыткой
                    return None
                elif resp.status == 404:
                    # Если модель не найдена, получаем список доступных моделей и пробуем их
                    error_text = await resp.text()
                    log_warning("ai_gemini", f"Модель {GEMINI_MODEL} не найдена, получаю список доступных моделей")
                    
                    # Получаем список доступных моделей через API
                    available_models = await self._get_available_gemini_models()
                    models_to_try = available_models if available_models else GEMINI_FALLBACK_MODELS
                    
                    log_info("ai_gemini", f"Буду пробовать модели: {models_to_try}")
                    
                    # Пробуем модели из списка
                    for fallback_model in models_to_try:
                        if fallback_model == GEMINI_MODEL:
                            continue  # Пропускаем уже попробованную модель
                        
                        log_info("ai_gemini", f"Пробую модель: {fallback_model}")
                        fallback_url = f"https://generativelanguage.googleapis.com/v1beta/models/{fallback_model}:generateContent?key={GEMINI_API_KEY}"
                        
                        try:
                            async with self.session.post(fallback_url, json=payload, timeout=timeout) as fallback_resp:
                                if fallback_resp.status == 200:
                                    data = await fallback_resp.json()
                                    content = data["candidates"][0]["content"]["parts"][0]["text"]
                                    log_info("ai_gemini", f"✅ Успешно использована модель {fallback_model}: {content[:100]}...")
                                    result = self._parse_ai_response(content)
                                    if result:
                                        log_info("ai_gemini", f"Успешная оценка ({fallback_model}): ${result.get('fair_price_usd', 0):,}")
                                    return result
                                elif fallback_resp.status == 429:
                                    error_text_fallback = await fallback_resp.text()
                                    log_warning("ai_gemini", f"Rate limit (429) для модели {fallback_model}. Жду 60 секунд...")
                                    await asyncio.sleep(60)
                                    continue
                                elif fallback_resp.status != 404:
                                    error_text_fallback = await fallback_resp.text()
                                    log_warning("ai_gemini", f"Модель {fallback_model} вернула статус {fallback_resp.status}: {error_text_fallback[:100]}")
                        except Exception as e:
                            log_warning("ai_gemini", f"Ошибка при попытке использовать {fallback_model}: {e}")
                            continue
                    
                    log_error("ai_gemini", f"Все модели не найдены. Попробованы: {models_to_try}")
                    log_error("ai_gemini", f"Последняя ошибка: {error_text[:200]}")
                else:
                    error_text = await resp.text()
                    log_error("ai_gemini", f"Gemini API вернул статус {resp.status}: {error_text[:200]}")
        except aiohttp.ClientError as e:
            if "Session is closed" in str(e):
                log_warning("ai_gemini", "Сессия закрыта, пересоздаю...")
                await self.close_session()
                await self.start_session()
                # Повторяем запрос один раз
                try:
                    timeout = aiohttp.ClientTimeout(total=20)
                    async with self.session.post(url, json=payload, timeout=timeout) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            content = data["candidates"][0]["content"]["parts"][0]["text"]
                            return self._parse_ai_response(content)
                except Exception as retry_e:
                    log_error("ai_gemini", f"Ошибка при повторной попытке: {retry_e}")
            else:
                log_error("ai_gemini", f"Ошибка клиента: {e}")
        except asyncio.TimeoutError:
            log_error("ai_gemini", "Таймаут запроса к Gemini API (20 сек)")
        except Exception as e:
            log_error("ai_gemini", f"Ошибка запроса к Gemini API", e)
        
        return None
    
    async def valuate_ollama(self, listing: Listing) -> Optional[Dict[str, Any]]:
        """Оценка через Ollama (локально)"""
        prompt = self._prepare_prompt(listing)
        
        payload = {
            "model": "llama3",
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.3
            }
        }
        
        try:
            async with self.session.post(OLLAMA_URL, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    content = data["response"]
                    return self._parse_ai_response(content)
        except Exception as e:
            print(f"[AI] Ollama ошибка: {e}")
        
        return None
    
    def _parse_ai_response(self, content: str) -> Optional[Dict[str, Any]]:
        """Парсит ответ ИИ и извлекает JSON"""
        try:
            # Убираем markdown code blocks если есть
            content = re.sub(r'```json\s*', '', content)
            content = re.sub(r'```\s*', '', content)
            content = content.strip()
            
            # Пытаемся найти JSON объект (может быть многострочным)
            json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', content, re.DOTALL)
            if json_match:
                json_str = json_match.group(0)
                result = json.loads(json_str)
                
                # Валидация результата (новые поля опциональны)
                if "fair_price_usd" in result and isinstance(result["fair_price_usd"], (int, float)):
                    if "is_overpriced" in result and isinstance(result["is_overpriced"], bool):
                        if "assessment" in result and isinstance(result["assessment"], str):
                            # Новые поля опциональны, но если есть - проверяем
                            if "renovation_state" in result and not isinstance(result["renovation_state"], str):
                                result["renovation_state"] = ""
                            if "recommendations" in result and not isinstance(result["recommendations"], str):
                                result["recommendations"] = ""
                            if "value_score" in result and not isinstance(result["value_score"], (int, float)):
                                result["value_score"] = 0
                            return result
                
                log_warning("ai_parser", f"Неполный JSON в ответе: {result}")
            else:
                log_warning("ai_parser", f"JSON не найден в ответе: {content[:200]}")
        except json.JSONDecodeError as e:
            log_error("ai_parser", f"Ошибка парсинга JSON: {content[:200]}", e)
        except Exception as e:
            log_error("ai_parser", f"Ошибка парсинга ответа", e)
        
        return None
    
    async def valuate(self, listing: Listing) -> Optional[Dict[str, Any]]:
        """Оценивает квартиру используя выбранный провайдер"""
        await self.start_session()
        
        if self.provider == "groq":
            return await self.valuate_groq(listing)
        elif self.provider == "huggingface":
            return await self.valuate_huggingface(listing)
        elif self.provider == "ollama":
            return await self.valuate_ollama(listing)
        
        return None
    
    # Удалено: функция _select_best_gemini_detailed - Gemini больше не используется
    
    async def _select_best_gemini_detailed_removed(self, prompt: str, inspected_listings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Выбирает лучшие варианты через Gemini API - Gemini сам просматривает ссылки"""
        if not GEMINI_API_KEY:
            log_error("ai_select", "GEMINI_API_KEY не установлен")
            return []
        
        # Отправляем промпт с ссылками - Gemini может анализировать URL из текста
        # Примечание: Gemini API не поддерживает прямой просмотр веб-страниц через URL,
        # но мы отправляем ссылки в тексте и просим Gemini их проанализировать
        payload = {
            "contents": [{
                "parts": [{"text": prompt}]
            }],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 1500
            }
        }
        
        # Получаем список доступных моделей и используем первую рабочую
        try:
            available_models = await self._get_available_gemini_models()
            models_to_try = available_models if available_models else GEMINI_FALLBACK_MODELS
        except Exception as e:
            log_warning("ai_select", f"Не удалось получить список моделей, использую fallback: {e}")
            models_to_try = GEMINI_FALLBACK_MODELS
        
        log_info("ai_select", f"Отправляю POST запрос к Gemini API")
        log_info("ai_select", f"Размер промпта: {len(prompt)} символов, количество объявлений: {len(inspected_listings)}")
        log_info("ai_select", f"Буду пробовать модели: {models_to_try[:3]}")
        
        # Если промпт слишком большой (>30000 символов), обрезаем его
        if len(prompt) > 30000:
            log_warning("ai_select", f"Промпт слишком большой ({len(prompt)} символов), обрезаю до 30000")
            prompt = prompt[:30000] + "\n\n[Промпт обрезан из-за ограничений размера]"
            # Обновляем payload с обрезанным промптом
            payload = {
                "contents": [{
                    "parts": [{"text": prompt}]
                }],
                "generationConfig": {
                    "temperature": 0.2,
                    "maxOutputTokens": 1500
                }
            }
        
        # Пробуем модели по очереди
        for model in models_to_try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
            log_info("ai_select", f"Пробую модель: {model}")
            
            try:
                timeout = aiohttp.ClientTimeout(total=120)
                log_info("ai_select", "Ожидаю ответ от Gemini API (максимум 120 секунд)...")
                async with self.session.post(url, json=payload, timeout=timeout) as resp:
                    log_info("ai_select", f"Получен ответ от Gemini API. Статус: {resp.status}")
                    if resp.status == 200:
                        data = await resp.json()
                        content = data["candidates"][0]["content"]["parts"][0]["text"]
                        log_info("ai_select", f"✅ Успешно использована модель {model}")
                        log_info("ai_select", f"Ответ от Gemini: {content[:300]}...")
                        
                        # Парсим JSON из ответа
                        selected_with_reasons = self._parse_selection_response_detailed(content, inspected_listings)
                        return selected_with_reasons
                    elif resp.status == 429:
                        # Rate limit - ждем и пробуем следующую модель
                        error_text = await resp.text()
                        log_warning("ai_select", f"Rate limit достигнут (429) для модели {model}. Жду 60 секунд...")
                        await asyncio.sleep(60)  # Ждем минуту
                        continue  # Пробуем следующую модель
                    elif resp.status == 404:
                        log_warning("ai_select", f"Модель {model} не найдена (404), пробую следующую...")
                        continue
                    else:
                        error_text = await resp.text()
                        log_error("ai_select", f"Gemini API вернул статус {resp.status}: {error_text[:200]}")
                        if resp.status not in [404, 429]:
                            break  # Если не 404 или 429, прекращаем попытки
            except aiohttp.ClientError as e:
                if "Session is closed" in str(e):
                    log_warning("ai_select", "Сессия закрыта, пересоздаю...")
                    await self.close_session()
                    await self.start_session()
                    # Повторяем запрос один раз
                    try:
                        timeout = aiohttp.ClientTimeout(total=120)
                        async with self.session.post(url, json=payload, timeout=timeout) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                content = data["candidates"][0]["content"]["parts"][0]["text"]
                                selected_with_reasons = self._parse_selection_response_detailed(content, inspected_listings)
                                return selected_with_reasons
                    except Exception as retry_e:
                        log_error("ai_select", f"Ошибка при повторной попытке: {retry_e}")
                else:
                    log_error("ai_select", f"Ошибка клиента: {e}")
                continue
            except Exception as e:
                log_error("ai_select", f"Ошибка запроса к Gemini API с моделью {model}", e)
                continue
        
        log_error("ai_select", f"Все модели не работают. Попробованы: {models_to_try[:5]}")
        
        return []
    
    async def _select_best_groq_detailed(self, prompt: str, inspected_listings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Выбирает лучшие варианты через Groq API с детальным анализом"""
        if not GROQ_API_KEY:
            return []
        
        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "messages": [
                {"role": "system", "content": "Ты экспертный помощник по анализу рынка недвижимости Беларуси. Анализируй объявления тщательно, сравнивай между собой и выбирай самые выгодные варианты. Отвечай строго в JSON формате."},
                {"role": "user", "content": prompt}
            ],
            "model": GROQ_MODEL,
            "temperature": 0.4,
            "max_tokens": 3000  # Больше токенов для детального анализа
        }
        
        try:
            timeout = aiohttp.ClientTimeout(total=60)
            async with self.session.post(GROQ_API_URL, json=payload, headers=headers, timeout=timeout) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    content = data["choices"][0]["message"]["content"]
                    log_info("ai_select", f"Ответ от Groq: {content[:300]}...")
                    
                    selected_with_reasons = self._parse_selection_response_detailed(content, inspected_listings)
                    return selected_with_reasons
                else:
                    error_text = await resp.text()
                    log_error("ai_select", f"Groq API вернул статус {resp.status}: {error_text[:200]}")
        except Exception as e:
            log_error("ai_select", f"Ошибка запроса к Groq API", e)
        
        return []
    
    def _parse_selection_response_detailed(self, content: str, inspected_listings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Парсит детальный ответ ИИ с описаниями"""
        try:
            # Пробуем найти JSON объект с "selected" массивом
            # Ищем начало JSON (может быть с markdown code blocks)
            json_str = None
            
            # Сначала пробуем найти JSON в markdown блоке (```json ... ```)
            # Используем более точный подход - находим начало и конец блока
            json_markdown_start = content.find('```json')
            if json_markdown_start != -1:
                # Находим начало JSON объекта после ```json
                json_start = content.find('{', json_markdown_start)
                if json_start != -1:
                    # Находим конец markdown блока
                    json_markdown_end = content.find('```', json_start)
                    if json_markdown_end != -1:
                        json_str = content[json_start:json_markdown_end].strip()
                        log_info("ai_select", f"Найден JSON в markdown блоке: {len(json_str)} символов")
            
            # Если не нашли в markdown блоке, пробуем обычный блок
            if not json_str:
                json_block_start = content.find('```')
                if json_block_start != -1:
                    json_start = content.find('{', json_block_start)
                    if json_start != -1:
                        json_block_end = content.find('```', json_start)
                        if json_block_end != -1:
                            json_str = content[json_start:json_block_end].strip()
                            log_info("ai_select", f"Найден JSON в обычном блоке: {len(json_str)} символов")
            
            # Если не нашли через паттерны, пытаемся найти любой JSON объект
            if not json_str:
                # Ищем JSON объект, который содержит "top_offers", "analysis_summary" или "selected"
                brace_count = 0
                start_idx = content.find('{"top_offers"')
                if start_idx == -1:
                    start_idx = content.find('{"analysis_summary"')
                if start_idx == -1:
                    start_idx = content.find('{"selected"')
                if start_idx == -1:
                    # Пробуем найти просто начало JSON объекта
                    start_idx = content.find('{')
                
                if start_idx != -1:
                    json_str = ""
                    for i in range(start_idx, len(content)):
                        char = content[i]
                        json_str += char
                        if char == '{':
                            brace_count += 1
                        elif char == '}':
                            brace_count -= 1
                            if brace_count == 0:
                                break
                    
                    log_info("ai_select", f"Найден JSON объект (длина: {len(json_str)} символов)")
            
            if json_str:
                try:
                    result = json.loads(json_str)
                    
                    # Поддержка нового формата (top_offers) и старого (selected)
                    top_offers = result.get("top_offers", result.get("selected", []))
                    analysis_summary = result.get("analysis_summary", "")
                    best_overall = result.get("best_overall", {})
                    
                    if analysis_summary:
                        log_info("ai_select", f"📊 Сводка: {analysis_summary[:150]}...")
                    
                    if not top_offers:
                        log_warning("ai_select", f"JSON распарсен, но top_offers/selected пуст. JSON: {json_str[:200]}")
                        # Пробуем fallback
                        raise ValueError("top_offers пуст")
                    
                    log_info("ai_select", f"Найдено {len(top_offers)} элементов в JSON ответе")
                    
                    # Формируем результат
                    result_list = []
                    for item in top_offers:
                        # Поддержка обоих форматов ID
                        listing_id = item.get("offer_id", item.get("id", ""))
                        title = item.get("title", "")
                        reason = item.get("reason", "Хорошее соотношение цена-качество")
                        final_score = item.get("final_score", 0)
                        critical_notes = item.get("critical_notes", [])
                        
                        if not listing_id:
                            log_warning("ai_select", f"Пропускаю элемент без ID: {item}")
                            continue
                        
                        # Формируем расширенное описание с оценкой и недостатками
                        extended_reason = reason
                        if final_score > 0:
                            extended_reason = f"⭐ Оценка: {final_score}/10\n\n{reason}"
                        if critical_notes and len(critical_notes) > 0:
                            notes_text = "\n".join([f"⚠️ {note}" for note in critical_notes if note])
                            if notes_text:
                                extended_reason = f"{extended_reason}\n\n{notes_text}"
                        
                        # Находим соответствующее объявление
                        found = False
                        for inspected in inspected_listings:
                            if inspected["listing"].id == listing_id:
                                result_list.append({
                                    "listing": inspected["listing"],
                                    "reason": extended_reason,
                                    "score": final_score,
                                    "title": title
                                })
                                found = True
                                log_info("ai_select", f"✅ Найден вариант: {listing_id} (оценка: {final_score})")
                                break
                        
                        if not found:
                            log_warning("ai_select", f"Не найден listing для ID: {listing_id}")
                    
                    # Сортируем по оценке (если есть)
                    result_list.sort(key=lambda x: x.get("score", 0), reverse=True)
                    
                    # Помечаем лучший вариант
                    if best_overall and result_list:
                        best_id = best_overall.get("offer_id", "")
                        best_advantage = best_overall.get("main_advantage", "")
                        for item in result_list:
                            if item["listing"].id == best_id and best_advantage:
                                item["reason"] = f"🏆 ЛУЧШИЙ ВЫБОР: {best_advantage}\n\n{item['reason']}"
                                break
                    
                    if result_list:
                        log_info("ai_select", f"✅ Успешно распарсено {len(result_list)} вариантов")
                        return result_list
                    else:
                        log_warning("ai_select", f"Не удалось найти ни одного валидного варианта в JSON")
                except json.JSONDecodeError as e:
                    log_error("ai_select", f"Ошибка парсинга JSON: {e}")
                    log_info("ai_select", f"Проблемный JSON (первые 500 символов): {json_str[:500]}")
                    return []
                except ValueError as e:
                    log_error("ai_select", f"Ошибка валидации JSON: {e}")
                    return []
            
            # Если JSON не найден вообще
            log_error("ai_select", f"JSON не найден в ответе. Первые 500 символов: {content[:500]}")
            return []
            
        except json.JSONDecodeError as e:
            log_error("ai_select", f"Ошибка парсинга JSON: {content[:200]}", e)
            return []
        except Exception as e:
            log_error("ai_select", f"Ошибка парсинга ответа", e)
            return []
        
        # Если ничего не найдено - возвращаем пустой список
        log_error("ai_select", "Не удалось распарсить ответ от ИИ")
        return []
    
    # Удалено: функция _select_best_gemini - Gemini больше не используется
    
    async def _select_best_gemini_removed(self, prompt: str, listings: List[Listing]) -> List[str]:
        """Выбирает лучшие варианты через Gemini API"""
        if not GEMINI_API_KEY:
            return []
        
        payload = {
            "contents": [{
                "parts": [{"text": prompt}]
            }],
            "generationConfig": {
                "temperature": 0.3,
                "maxOutputTokens": 1000
            }
        }
        
        url = f"{GEMINI_API_URL}?key={GEMINI_API_KEY}"
        
        try:
            timeout = aiohttp.ClientTimeout(total=30)
            async with self.session.post(url, json=payload, timeout=timeout) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    content = data["candidates"][0]["content"]["parts"][0]["text"]
                    log_info("ai_select", f"Ответ от Gemini: {content[:200]}...")
                    
                    # Парсим JSON из ответа
                    selected_ids = self._parse_selection_response(content, listings)
                    return selected_ids
                else:
                    error_text = await resp.text()
                    log_error("ai_select", f"Gemini API вернул статус {resp.status}: {error_text[:200]}")
        except Exception as e:
            log_error("ai_select", f"Ошибка запроса к Gemini API", e)
        
        return []
    
    async def _select_best_groq(self, prompt: str, listings: List[Listing]) -> List[str]:
        """Выбирает лучшие варианты через Groq API"""
        if not GROQ_API_KEY:
            return []
        
        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "messages": [
                {"role": "system", "content": "Ты эксперт по недвижимости. Отвечай только JSON."},
                {"role": "user", "content": prompt}
            ],
            "model": GROQ_MODEL,
            "temperature": 0.3,
            "max_tokens": 1000
        }
        
        try:
            timeout = aiohttp.ClientTimeout(total=30)
            async with self.session.post(GROQ_API_URL, json=payload, headers=headers, timeout=timeout) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    content = data["choices"][0]["message"]["content"]
                    log_info("ai_select", f"Ответ от Groq: {content[:200]}...")
                    
                    selected_ids = self._parse_selection_response(content, listings)
                    return selected_ids
                else:
                    error_text = await resp.text()
                    log_error("ai_select", f"Groq API вернул статус {resp.status}: {error_text[:200]}")
        except Exception as e:
            log_error("ai_select", f"Ошибка запроса к Groq API", e)
        
        return []
    
    def _parse_selection_response(self, content: str, listings: List[Listing]) -> List[str]:
        """Парсит ответ ИИ и извлекает список ID выбранных объявлений"""
        try:
            # Ищем JSON в ответе
            json_match = re.search(r'\{[^{}]*"selected_ids"[^{}]*\}', content, re.DOTALL)
            if json_match:
                json_str = json_match.group(0)
                result = json.loads(json_str)
                selected_ids = result.get("selected_ids", [])
                
                # Проверяем что все ID существуют в списке
                valid_ids = [lid for lid in selected_ids if any(l.id == lid for l in listings)]
                
                if valid_ids:
                    log_info("ai_select", f"Найдено {len(valid_ids)} валидных ID: {valid_ids}")
                    return valid_ids
                else:
                    log_warning("ai_select", f"Не найдено валидных ID в ответе")
            
            # Fallback: пытаемся найти ID в тексте напрямую
            all_ids = [l.id for l in listings]
            found_ids = []
            for listing_id in all_ids:
                if listing_id in content:
                    found_ids.append(listing_id)
            
            if found_ids:
                log_info("ai_select", f"Найдено ID через fallback: {found_ids[:5]}")
                return found_ids[:5]
            
        except json.JSONDecodeError as e:
            log_error("ai_select", f"Ошибка парсинга JSON: {content[:200]}", e)
        except Exception as e:
            log_error("ai_select", f"Ошибка парсинга ответа", e)
        
        # Если ничего не найдено, возвращаем первые несколько
        log_warning("ai_select", "Не удалось распарсить ответ, возвращаю первые 3")
        return [l.id for l in listings[:3]]


# Глобальный экземпляр
_valuator: Optional[AIValuator] = None


def get_valuator() -> Optional[AIValuator]:
    """Получает глобальный экземпляр оценщика"""
    global _valuator
    
    if _valuator is None:
        # Проверяем явно указанный провайдер через переменную окружения
        forced_provider = os.getenv("AI_PROVIDER", "").lower()
        
        if forced_provider == "groq" and GROQ_API_KEY:
            _valuator = AIValuator("groq")
            log_info("ai", "Использую Groq API (явно указан через AI_PROVIDER)")
        elif forced_provider == "gemini" and GEMINI_API_KEY:
            _valuator = AIValuator("gemini")
            log_info("ai", "Использую Gemini API (явно указан через AI_PROVIDER)")
        elif forced_provider == "huggingface" and HF_API_KEY:
            _valuator = AIValuator("huggingface")
            log_info("ai", "Использую Hugging Face API (явно указан через AI_PROVIDER)")
        elif forced_provider == "ollama" and os.getenv("OLLAMA_URL"):
            _valuator = AIValuator("ollama")
            log_info("ai", "Использую Ollama API (явно указан через AI_PROVIDER)")
        else:
            # Автоматический выбор: только Groq
            if GROQ_API_KEY:
            _valuator = AIValuator("groq")
                log_info("ai", "Использую Groq API (30 запросов/минуту, без суточных лимитов)")
        elif HF_API_KEY:
            _valuator = AIValuator("huggingface")
                log_info("ai", "Использую Hugging Face API")
        elif os.getenv("OLLAMA_URL"):
            _valuator = AIValuator("ollama")
                log_info("ai", "Использую Ollama API")
    return _valuator


async def valuate_listing(listing: Listing) -> Optional[Dict[str, Any]]:
    """Оценивает объявление"""
    valuator = get_valuator()
    if not valuator:
        return None
    
    async with valuator:
        return await valuator.valuate(listing)


async def select_best_listings(
    listings: List[Listing], 
    user_filters: Dict[str, Any],
    max_results: int = 5
) -> List[Dict[str, Any]]:
    """
    ИИ анализирует объявления по ссылкам и выбирает лучшие варианты.
    Возвращает список словарей с listing и описанием почему вариант хороший.
    Использует fallback: сначала Groq, при ошибке - Gemini.
    """
    if not listings:
        return []
        
        # Формируем список для промпта (только базовые данные + ссылки)
        # Исключаем объявления без цены (договорная, 0, None)
    # Лимит объявлений зависит от размера промпта, обычно до 100 OK
    listings_to_inspect = []
    
    for listing in listings[:100]:  # Максимум 100 объявлений для анализа
            # Пропускаем объявления без цены
            if not listing.price or listing.price <= 0:
                log_info("ai_select", f"Пропускаю объявление {listing.id}: цена не указана или равна 0")
                continue
        
        listings_to_inspect.append(listing)
    
    # Инициализируем список для промпта до блока try
    listings_for_prompt = []
    
    # Инспектируем страницы объявлений параллельно для получения полного описания
    # (важно для проверки на долю/комнату)
    log_info("ai_select", f"Инспектирую {len(listings_to_inspect)} страниц объявлений для получения описаний...")
    valuator = AIValuator("groq")  # Создаем временный экземпляр для инспекции
    await valuator.start_session()
    
    try:
        # Инспектируем параллельно (но ограничиваем количество для скорости и размера промпта)
        # Ограничиваем до 20 для уменьшения размера промпта (Groq API лимит ~6000 токенов)
        inspect_limit = min(20, len(listings_to_inspect))  # Максимум 20 объявлений для инспекции
        inspection_tasks = []
        for listing in listings_to_inspect[:inspect_limit]:
            inspection_tasks.append(valuator._inspect_listing_page(listing))
        
        inspections = await asyncio.gather(*inspection_tasks, return_exceptions=True)
        
        # Формируем список с инспекциями
        for i, listing in enumerate(listings_to_inspect[:inspect_limit]):
            inspection = inspections[i] if i < len(inspections) and not isinstance(inspections[i], Exception) else {}
            if isinstance(inspections[i], Exception):
                log_warning("ai_select", f"Ошибка инспекции {listing.id}: {inspections[i]}")
            
            listings_for_prompt.append({
                "listing": listing,
                "inspection": inspection
            })
        
        # Добавляем остальные объявления без инспекции
        for listing in listings_to_inspect[inspect_limit:]:
            listings_for_prompt.append({
                "listing": listing,
                "inspection": {}
            })
        
        log_info("ai_select", f"Инспектировано {inspect_limit} объявлений, всего подготовлено {len(listings_for_prompt)} для анализа")
    except Exception as e:
        log_error("ai_select", f"Ошибка при инспекции объявлений: {e}")
        # Если инспекция не удалась, добавляем объявления без инспекции
        for listing in listings_to_inspect:
            listings_for_prompt.append({
                "listing": listing,
                "inspection": {}
            })
    finally:
        await valuator.close_session()
    
    log_info("ai_select", f"Подготавливаю {len(listings_for_prompt)} объявлений для анализа...")
    
    # Динамический батчинг: разбиваем объявления на батчи и отправляем несколько запросов
    # Размер батча зависит от количества объявлений
    total_listings = len(listings_for_prompt)
    
    # Определяем размер батча (10-15 объявлений на батч для оптимального размера промпта)
    if total_listings <= 15:
        # Если объявлений мало - отправляем одним запросом
        batch_size = total_listings
        batches = [listings_for_prompt]
        else:
        # Разбиваем на батчи по 12 объявлений (оптимально для промпта ~3000-4000 токенов)
        batch_size = 12
        batches = []
        for i in range(0, total_listings, batch_size):
            batches.append(listings_for_prompt[i:i + batch_size])
    
    log_info("ai_select", f"Разбито на {len(batches)} батч(ей) по ~{batch_size} объявлений")
    
    # Список провайдеров для fallback (в порядке приоритета)
    providers_to_try = []
    
    # Если явно указан провайдер, используем только его
    forced_provider = os.getenv("AI_PROVIDER", "").lower()
    if forced_provider:
        if forced_provider == "groq" and GROQ_API_KEY:
            providers_to_try = [("groq", GROQ_API_KEY)]
    else:
        # Используем только Groq
        if GROQ_API_KEY:
            providers_to_try.append(("groq", GROQ_API_KEY))
    
    if not providers_to_try:
        log_warning("ai_select", "ИИ-оценщик недоступен, возвращаю все объявления")
        return [{"listing": l["listing"], "reason": "ИИ недоступен"} for l in listings_for_prompt[:max_results]]
    
    # Обрабатываем каждый батч параллельно
    all_selected_results = []
    
    async def process_batch(batch_listings: List[Dict[str, Any]], batch_num: int) -> List[Dict[str, Any]]:
        """Обрабатывает один батч объявлений"""
        # Формируем промпт для батча
        # Для каждого батча выбираем топ-5 (потом из всех выберем лучшие)
        batch_max_results = min(5, len(batch_listings))
        prompt = _prepare_selection_prompt_detailed(batch_listings, user_filters, batch_max_results)
        prompt_length = len(prompt)
        estimated_tokens = prompt_length // 4
        
        log_info("ai_select", f"Батч {batch_num + 1}/{len(batches)}: {len(batch_listings)} объявлений, промпт ~{estimated_tokens} токенов")
        
        # Пробуем провайдеры по очереди для этого батча
        for provider_name, api_key in providers_to_try:
            try:
                valuator = AIValuator(provider_name)
                await valuator.start_session()
                
                try:
                    if provider_name == "gemini":
                        selected = await valuator._select_best_gemini_detailed(prompt, batch_listings)
                    elif provider_name == "groq":
                        selected = await valuator._select_best_groq_detailed(prompt, batch_listings)
                    else:
                        continue
                    
                    if selected and len(selected) > 0:
                        log_info("ai_select", f"✅ Батч {batch_num + 1}: выбрано {len(selected)} вариантов")
                        await valuator.close_session()
                        return selected
                    else:
                        log_warning("ai_select", f"Батч {batch_num + 1}: провайдер {provider_name} вернул пустой результат")
                
                except Exception as e:
                    log_warning("ai_select", f"Батч {batch_num + 1}: ошибка {provider_name}: {e}")
                finally:
                    await valuator.close_session()
                    
            except Exception as e:
                log_warning("ai_select", f"Батч {batch_num + 1}: не удалось инициализировать {provider_name}: {e}")
                continue
        
        log_warning("ai_select", f"Батч {batch_num + 1}: не удалось получить результат")
        return []
    
    # Обрабатываем батчи параллельно (но с ограничением для избежания rate limit)
    log_info("ai_select", f"Обрабатываю {len(batches)} батч(ей) параллельно...")
    
    # Ограничиваем параллельность до 3 батчей одновременно (чтобы не превысить rate limit Groq)
    semaphore = asyncio.Semaphore(3)
    
    async def process_batch_with_semaphore(batch_listings, batch_num):
        async with semaphore:
            # Небольшая задержка между батчами для избежания rate limit
            if batch_num > 0:
                await asyncio.sleep(1)
            return await process_batch(batch_listings, batch_num)
    
    batch_tasks = [process_batch_with_semaphore(batch, i) for i, batch in enumerate(batches)]
    batch_results = await asyncio.gather(*batch_tasks, return_exceptions=True)
    
    # Собираем все результаты
    for i, result in enumerate(batch_results):
        if isinstance(result, Exception):
            log_error("ai_select", f"Ошибка обработки батча {i + 1}: {result}")
        elif result:
            all_selected_results.extend(result)
    
    log_info("ai_select", f"Всего получено результатов из всех батчей: {len(all_selected_results)}")
    
    if not all_selected_results:
        log_error("ai_select", "Не удалось получить результаты ни из одного батча")
        return []
    
    # Объединяем результаты и выбираем топ-N лучших по final_score
    # Удаляем дубликаты по listing.id
    seen_ids = set()
    unique_results = []
    for result in all_selected_results:
        listing = result.get("listing")
        if listing and listing.id not in seen_ids:
            seen_ids.add(listing.id)
            unique_results.append(result)
    
    # Сортируем по final_score (если есть) или по порядку
    def get_score(result):
        # Ищем final_score в разных местах ответа
        if "score" in result:
            return result["score"]
        if "final_score" in result:
            return result["final_score"]
        # Пробуем извлечь из reason, если там есть оценка
        reason = result.get("reason", "")
        score_match = re.search(r'(\d+\.?\d*)/10', reason)
        if score_match:
            return float(score_match.group(1))
        return 0
    
    unique_results.sort(key=get_score, reverse=True)
    
    log_info("ai_select", f"Выбрано {len(unique_results)} уникальных результатов, возвращаю топ-{max_results}")
    return unique_results[:max_results]


def _prepare_selection_prompt_detailed(
    inspected_listings: List[Dict[str, Any]], 
    user_filters: Dict[str, Any], 
    max_results: int
) -> str:
    """Подготавливает промпт для экспертного анализа объявлений"""
    
    min_price = user_filters.get("min_price", 0)
    max_price = user_filters.get("max_price", 100000)
    min_rooms = user_filters.get("min_rooms", 1)
    max_rooms = user_filters.get("max_rooms", 4)
    city = user_filters.get("city", "Минск").title()
    
    # Формируем список объявлений с деталями
    # Ограничиваем количество для предотвращения ошибки "Request too large"
    # Groq API имеет лимит ~6000 токенов для llama-3.1-8b-instant
    listings_text = []
    listings_to_process = inspected_listings[:20]  # Уменьшено до 20 объявлений для уменьшения размера промпта
    
    for i, item in enumerate(listings_to_process, 1):
        listing = item["listing"]
        
        rooms_text = f"{listing.rooms}-комн." if listing.rooms > 0 else "?"
        area_text = f"{listing.area} м²" if listing.area > 0 else "?"
        
        # Цена в USD
        price_usd = listing.price_usd if listing.price_usd else (
            int(listing.price_byn / 2.95) if listing.price_byn else (
                int(listing.price / 2.95) if listing.currency == "BYN" else listing.price
            )
        )
        price_text = f"${price_usd:,}" if price_usd > 0 else "?"
        
        # Цена за м²
        price_per_sqm = ""
        if listing.area > 0 and price_usd > 0:
            price_per_sqm = f" (${int(price_usd / listing.area)}/м²)"
        
        # Год постройки
        year_info = f", {listing.year_built}г." if listing.year_built else ""
        
        # Адрес
        address_short = listing.address[:50] if listing.address else ""
        
        # Название и описание (важно для проверки на долю/комнату)
        # Сокращаем для уменьшения размера промпта
        title_text = listing.title[:100] if listing.title else ""
        
        # Используем полное описание из инспекции, если доступно
        # Сокращаем до 200 символов для лучшего контекста
        inspection = item.get("inspection", {})
        full_description = inspection.get("full_description", "") if inspection else ""
        description_text = full_description[:200] if full_description else (listing.description[:200] if listing.description else "")
        
        # Добавляем информацию о типе недвижимости из API (если доступно)
        # Это поможет ИИ правильно определить тип жилья
        property_type_hint = ""
        if listing.year_built:
            try:
                year = int(listing.year_built)
                if year >= 2010:
                    property_type_hint = " (НОВОСТРОЙКА - точно целая квартира)"
                elif year >= 2000:
                    property_type_hint = " (новый дом - вероятно целая квартира)"
            except:
                pass
        
        # Формируем информацию об объявлении с названием и описанием
        # ВАЖНО: Описание может содержать информацию о доле/комнате!
        listing_info = f"{i}. ID:{listing.id} | {rooms_text}, {area_text}, {price_text}{price_per_sqm}{year_info}{property_type_hint} | {address_short}"
        if title_text:
            listing_info += f"\n   📌 Название: {title_text}"
        if description_text:
            listing_info += f"\n   📝 Описание: {description_text}"
        else:
            listing_info += f"\n   📝 Описание: [не указано - проверь по ссылке]"
        listing_info += f"\n   🔗 Ссылка: {listing.url}\n"
        listings_text.append(listing_info)
    
    # Оптимизированный промпт (сокращен для уменьшения размера)
    prompt = f"""Эксперт по недвижимости Беларуси. Анализируй объявления и выбирай лучшие.

КРИТЕРИИ: {city}, {min_rooms}-{max_rooms}к, ${min_price:,}-${max_price:,}

⚠️ КРИТИЧЕСКИ ВАЖНО — ПРОВЕРКА НА ДОЛЮ/КОМНАТУ:
Проверь ТОЛЬКО на ЯВНЫЕ признаки:
- "доля в квартире", "1/2 квартиры", "1/3 квартиры", "1/4 квартиры", "половина квартиры"
- "комната в коммуналке", "комната в общежитии", "комната в квартире" (если продается КОМНАТА, а не квартира)
- "часть квартиры", "не целая квартира", "продается комната"

НЕ считай долей/комнатой если:
- В описании просто упоминается слово "комната" в контексте описания квартиры (например, "однокомнатная квартира", "комната в квартире" как описание)
- Это новостройка (год постройки 2010+)
- В названии указано "квартира", "студия", "апартаменты"
- Площадь соответствует полноценной квартире (обычно >20м² для 1к, >30м² для 2к)

Если ТОЧНО найдено → укажи в critical_notes: "⚠️ ДОЛЯ: [детали]" или "⚠️ КОМНАТА: [детали]"
Если доля/комната → снизь final_score на 3-4 балла, НЕ выбирай в топ если есть целые квартиры!

ОЦЕНКА (1-10):
• Цена vs рынок {city} (10=отлично, 1=завышена)
• Год постройки (новее 2010г = бонус)
• Район и инфраструктура
• ЦЕЛАЯ КВАРТИРА (доля/комната = -3-4 балла)

Выбери ТОП-{max_results} лучших (ТОЛЬКО ЦЕЛЫЕ КВАРТИРЫ!)

ОБЪЯВЛЕНИЯ ДЛЯ АНАЛИЗА:
{''.join(listings_text)}

ФОРМАТ ОТВЕТА (строго JSON):
{{
  "analysis_summary": "Краткая сводка: сколько проанализировано, общая ситуация на рынке {city}",
  "top_offers": [
    {{
      "offer_id": "listing_id из объявления",
      "title": "Краткое название (комнаты, площадь, район)",
      "final_score": 8.5,
      "reason": "Почему это хороший вариант (цена за м², сравнение с рынком, год, состояние)",
      "critical_notes": ["⚠️ ОБЯЗАТЕЛЬНО укажи здесь, если это ДОЛЯ или КОМНАТА! Формат: '⚠️ ДОЛЯ: 1/2 в двухкомнатной квартире' или '⚠️ КОМНАТА: комната в коммуналке'. Если целая квартира — укажи другие недостатки или 'Нет критических недостатков'", "Другие недостатки (если есть)"]
    }}
  ],
  "best_overall": {{
    "offer_id": "ID лучшего варианта",
    "title": "Название",
    "main_advantage": "Главное преимущество"
  }}
}}

Важно: Если данных недостаточно для оценки — укажи N/A. Выбери минимум {max_results} вариантов."""
    
    return prompt


def _prepare_selection_prompt(listings: List[Listing], user_filters: Dict[str, Any], max_results: int) -> str:
    """Подготавливает промпт для выбора лучших вариантов"""
    
    min_price = user_filters.get("min_price", 0)
    max_price = user_filters.get("max_price", 100000)
    min_rooms = user_filters.get("min_rooms", 1)
    max_rooms = user_filters.get("max_rooms", 4)
    
    # Формируем список объявлений для анализа
    listings_text = []
    for i, listing in enumerate(listings, 1):
        rooms_text = f"{listing.rooms}-комн." if listing.rooms > 0 else "?"
        area_text = f"{listing.area} м²" if listing.area > 0 else "?"
        price_text = listing.price_formatted
        
        # Рассчитываем цену за м² если возможно (всегда в USD)
        price_per_sqm = ""
        if listing.area > 0:
            # Определяем цену в USD для расчета цены за м²
            price_usd_for_calc = listing.price_usd if listing.price_usd else (
                int(listing.price_byn / 2.95) if listing.price_byn else (
                    int(listing.price / 2.95) if listing.currency == "BYN" else listing.price
                )
            )
            if price_usd_for_calc > 0:
                price_per_sqm_usd = price_usd_for_calc / listing.area
            price_per_sqm = f" (${price_per_sqm_usd:.0f}/м²)"
        
        listing_info = f"""
{i}. ID: {listing.id}
   - Комнаты: {rooms_text}
   - Площадь: {area_text}
   - Цена: {price_text}{price_per_sqm}
   - Адрес: {listing.address}
   - Источник: {listing.source}
   - Ссылка: {listing.url}
"""
        listings_text.append(listing_info)
    
    prompt = f"""Ты - эксперт по недвижимости в Барановичах, Беларусь. 

ЗАДАЧА: Выбери {max_results} лучших вариантов квартир из предложенного списка по критерию "цена-качество".

КРИТЕРИИ ПОЛЬЗОВАТЕЛЯ:
- Комнаты: от {min_rooms} до {max_rooms}
- Цена: от ${min_price:,} до ${max_price:,}
- Город: Барановичи

ЧТО УЧИТЫВАТЬ ПРИ ВЫБОРЕ:
1. Соотношение цена/качество (цена за м²)
2. Расположение (центр предпочтительнее, но не критично)
3. Размер площади (больше - лучше, но в разумных пределах)
4. Количество комнат (соответствие запросу)
5. Общая привлекательность предложения

СПИСОК ОБЪЯВЛЕНИЙ:
{''.join(listings_text)}

ВЕРНИ ОТВЕТ В ФОРМАТЕ JSON:
{{
    "selected_ids": ["id1", "id2", "id3", ...],
    "reasoning": "Краткое объяснение почему выбраны именно эти варианты"
}}

ВАЖНО:
- Верни ТОЛЬКО JSON, без дополнительного текста
- Выбери от 3 до {max_results} лучших вариантов
- Укажи ID в том же формате, как в списке выше
"""
    
    return prompt

