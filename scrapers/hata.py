"""
Парсер для Hata.by (baranovichi.hata.by)
"""
import re
from typing import List, Optional
from bs4 import BeautifulSoup
from scrapers.base import BaseScraper, Listing


class HataScraper(BaseScraper):
    """Парсер объявлений с baranovichi.hata.by"""
    
    SOURCE_NAME = "hata"
    BASE_URL = "https://baranovichi.hata.by"
    
    async def fetch_listings(
        self,
        city: str = "барановичи",
        min_rooms: int = 1,
        max_rooms: int = 4,
        min_price: int = 0,
        max_price: int = 100000,
    ) -> List[Listing]:
        """Получает список объявлений"""
        
        # Используем витрину для получения фото
        url = f"{self.BASE_URL}/sale-flat/"
        
        html = await self._fetch_html(url)
        if not html:
            return []
        
        return self._parse_html(html, min_rooms, max_rooms, min_price, max_price)
    
    def _parse_html(
        self, 
        html: str,
        min_rooms: int,
        max_rooms: int,
        min_price: int,
        max_price: int
    ) -> List[Listing]:
        """Парсит HTML страницу витрины"""
        soup = BeautifulSoup(html, 'lxml')
        listings = []
        
        # На витрине объявления в блоках, которые содержат ссылку на /object/XXXXXX/
        # Ищем все блоки с объявлениями (не рекламу новостроек!)
        for link in soup.find_all('a', href=re.compile(r'/object/\d+/')):
            # Пропускаем если это ссылка внутри рекламного блока
            parent = link.find_parent()
            if parent:
                # Поднимаемся выше чтобы найти контейнер объявления
                container = None
                for _ in range(10):
                    if parent.name == 'div' and parent.find('a', href=re.compile(r'/object/\d+/')):
                        container = parent
                    parent = parent.find_parent()
                    if not parent:
                        break
                
                if container:
                    listing = self._parse_listing_container(container)
                    if listing:
                        if self._matches_filters(listing, min_rooms, max_rooms, min_price, max_price):
                            # Проверяем что это Барановичи
                            if 'барановичи' in listing.address.lower():
                                # Проверяем что не дубликат
                                if not any(l.id == listing.id for l in listings):
                                    listings.append(listing)
        
        print(f"[hata] Найдено: {len(listings)} объявлений")
        return listings
    
    def _parse_listing_container(self, container) -> Optional[Listing]:
        """Парсит контейнер объявления"""
        try:
            # Находим ссылку на объявление
            link = container.find('a', href=re.compile(r'/object/\d+/'))
            if not link:
                return None
            
            url = link.get('href', '')
            if not url.startswith('http'):
                url = f"{self.BASE_URL}{url}"
            
            # ID из URL
            id_match = re.search(r'/object/(\d+)/', url)
            if not id_match:
                return None
            listing_id = f"hata_{id_match.group(1)}"
            
            # Собираем весь текст контейнера
            text = container.get_text(separator=' ', strip=True)
            
            # Проверяем что это не реклама новостройки
            if any(x in text.lower() for x in ['акция', 'новостройк', 'жк ', 'жилой комплекс']):
                return None
            
            # Название/заголовок из ссылки
            title_link = container.find('a', href=re.compile(r'/object/\d+/'), string=re.compile(r'квартир', re.I))
            title = ""
            if title_link:
                title = title_link.get_text(strip=True)
            
            # Комнаты из заголовка или текста
            rooms = 0
            rooms_match = re.search(r'(\d+)\s*-?\s*комн', text, re.I)
            if rooms_match:
                rooms = int(rooms_match.group(1))
            
            # Площадь - ищем формат "XX.X /YY/Z.Z м²" или просто "XX м²"
            area = 0.0
            # Сначала ищем формат с общей/жилой/кухней
            area_match = re.search(r'(\d+[.,]?\d*)\s*/\d+[.,]?\d*/\d+[.,]?\d*\s*м', text)
            if area_match:
                area = float(area_match.group(1).replace(',', '.'))
            else:
                # Простой формат
                area_match = re.search(r'(\d+[.,]?\d*)\s*м[²2]', text)
                if area_match:
                    area = float(area_match.group(1).replace(',', '.'))
            
            # Цена - ищем формат "XX XXX $" 
            price = 0
            price_match = re.search(r'([\d\s]+)\s*\$', text)
            if price_match:
                price_str = price_match.group(1).replace(' ', '').replace('\xa0', '')
                try:
                    price = int(price_str)
                except:
                    pass
            
            # Адрес - ищем строку с "Барановичи" или "ул."
            address = "Барановичи"
            # Ищем полный адрес
            addr_match = re.search(r'г\.\s*Барановичи[^,]*,?\s*ул\.\s*[^,]+(?:\s*д\.\s*\d+)?(?:\s*к\.\s*\w+)?', text)
            if addr_match:
                address = addr_match.group(0).strip()
            else:
                # Ищем только улицу
                street_match = re.search(r'ул\.\s*([^,\d]+)\s*(?:д\.)?\s*(\d+)?', text)
                if street_match:
                    street = street_match.group(1).strip()
                    num = street_match.group(2) or ""
                    address = f"Барановичи, ул. {street} {num}".strip()
            
            # Этаж
            floor = ""
            floor_match = re.search(r'этаж\s*(\d+)\s*/\s*(\d+)|(\d+)\s*этаж\s*\(?(\d+)', text, re.I)
            if floor_match:
                if floor_match.group(1):
                    floor = f"{floor_match.group(1)}/{floor_match.group(2)} этаж"
                else:
                    floor = f"{floor_match.group(3)}/{floor_match.group(4)} этаж"
            else:
                # Другой формат: "(этаж 2 / 4)"
                floor_match2 = re.search(r'\(этаж\s*(\d+)\s*/\s*(\d+)\)', text, re.I)
                if floor_match2:
                    floor = f"{floor_match2.group(1)}/{floor_match2.group(2)} этаж"
            
            # Год постройки
            year = ""
            year_match = re.search(r'(\d{4})\s*(?:год|г\.?\s*п)', text)
            if year_match:
                year = year_match.group(1)
            
            # Фото - ищем img в контейнере
            photos = []
            for img in container.find_all('img', src=True)[:5]:
                img_src = img.get('src') or img.get('data-src') or img.get('data-lazy')
                if img_src:
                    # Пропускаем служебные картинки
                    skip_patterns = ['sprite', 'icon', 'logo', 'no-photo', 'placeholder', 
                                   'spacer', 'blank', 'pixel', 'tracking', '.svg']
                    if any(p in img_src.lower() for p in skip_patterns):
                        continue
                    
                    # Проверяем что это картинка объекта (обычно содержит размер или object)
                    if not img_src.startswith('http'):
                        img_src = f"https://baranovichi.hata.by{img_src}"
                    
                    # Hata.by использует формат /img/XXXXX/YYYxZZZ/
                    if '/img/' in img_src or 'hata.by' in img_src:
                        # Заменяем превью на большое фото
                        img_src = re.sub(r'/\d+x\d+/', '/800x600/', img_src)
                        photos.append(img_src)
                        if len(photos) >= 3:
                            break
            
            # Формируем заголовок
            if not title:
                title = f"{rooms}-комн. квартира" if rooms else "Квартира"
                if area:
                    title += f", {area} м²"
            
            return Listing(
                id=listing_id,
                source="Hata.by",
                title=title,
                price=price,
                price_formatted=f"${price:,}".replace(",", " ") if price else "Цена не указана",
                rooms=rooms,
                area=area,
                floor=floor,
                address=address,
                photos=photos,
                url=url,
            )
            
        except Exception as e:
            print(f"[Hata] Ошибка парсинга: {e}")
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
