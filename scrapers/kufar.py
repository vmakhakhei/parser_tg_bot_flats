"""
Парсер для Kufar.by
"""
from typing import List, Optional
from scrapers.base import BaseScraper, Listing


class KufarScraper(BaseScraper):
    """Парсер объявлений с Kufar.by"""
    
    SOURCE_NAME = "kufar"
    BASE_URL = "https://api.kufar.by/search-api/v2/search/rendered-paginated"
    LISTING_URL = "https://www.kufar.by/item/"
    
    def _build_search_url(
        self,
        city: str = "барановичи",
        min_rooms: int = 1,
        max_rooms: int = 4,
        min_price: int = 0,
        max_price: int = 100000,
    ) -> str:
        """Строит URL для поиска"""
        params = {
            "cat": "1010",  # Квартиры продажа
            "cur": "USD",
            "gtsy": "country-belarus~region-brestskaya~city-baranovichi",
            "lang": "ru",
            "size": "42",
            "sort": "lst.d",
        }
        
        # Количество комнат
        rooms_values = [str(r) for r in range(min_rooms, min(max_rooms + 1, 6))]
        if rooms_values:
            params["rms"] = ",".join(rooms_values)
        
        # Цена
        if min_price > 0 or max_price < 100000:
            params["prc"] = f"r:{min_price},{max_price}"
            
        query_string = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{self.BASE_URL}?{query_string}"
    
    async def fetch_listings(
        self,
        city: str = "барановичи",
        min_rooms: int = 1,
        max_rooms: int = 4,
        min_price: int = 0,
        max_price: int = 100000,
    ) -> List[Listing]:
        """Получает список объявлений"""
        url = self._build_search_url(city, min_rooms, max_rooms, min_price, max_price)
        data = await self._fetch_json(url)
        
        if not data:
            return []
        
        listings = []
        for ad in data.get("ads", []):
            listing = self._parse_ad(ad)
            if listing:
                listings.append(listing)
                
        return listings
    
    def _parse_ad(self, ad: dict) -> Optional[Listing]:
        """Парсит одно объявление"""
        try:
            ad_id = str(ad.get("ad_id", ""))
            if not ad_id:
                return None
            
            title = ad.get("subject", "Квартира")
            
            # Цена
            price_usd = ad.get("price_usd", "0")
            try:
                price = int(price_usd) // 100 if price_usd else 0
            except (ValueError, TypeError):
                price = 0
            
            # Параметры
            params = ad.get("ad_parameters", [])
            rooms = 0
            area = 0.0
            floor = ""
            address = ad.get("address", "Барановичи")
            
            for param in params:
                param_name = param.get("p", "")
                param_value = param.get("v", "")
                param_label = param.get("vl", "")
                
                if param_name == "rooms":
                    try:
                        rooms = int(param_value) if param_value.isdigit() else int(param_label.split()[0])
                    except (ValueError, IndexError):
                        rooms = 1
                elif param_name == "size":
                    try:
                        area = float(param_value)
                    except ValueError:
                        area = 0.0
                elif param_name in ("floor_number", "floors_total"):
                    floor += f"{param_label} "
            
            # Изображения
            images = ad.get("images", [])
            photo_urls = []
            for img in images[:3]:
                if isinstance(img, dict):
                    img_id = img.get("id", "")
                    if img_id:
                        photo_urls.append(f"https://yams.kufar.by/api/v1/kufar-ads/images/{img_id}.jpg?rule=gallery")
                elif isinstance(img, str):
                    photo_urls.append(img)
            
            # URL объявления
            link_name = ad.get("ad_link", "")
            url = f"https://www.kufar.by{link_name}" if link_name else f"{self.LISTING_URL}{ad_id}"
            
            return Listing(
                id=f"kufar_{ad_id}",
                source="Kufar.by",
                title=title,
                price=price,
                price_formatted=f"${price:,}".replace(",", " "),
                rooms=rooms,
                area=area,
                floor=floor.strip(),
                address=address or "Барановичи",
                photos=photo_urls,
                url=url,
            )
            
        except Exception as e:
            print(f"[Kufar] Ошибка парсинга: {e}")
            return None

