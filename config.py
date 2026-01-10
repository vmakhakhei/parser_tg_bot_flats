"""
Конфигурация бота для мониторинга объявлений о квартирах
"""
import os
from dotenv import load_dotenv

load_dotenv()

# Telegram настройки
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CHANNEL_ID = os.getenv("CHANNEL_ID", "")

# Интервал проверки в минутах (12 часов = 720 минут)
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "720"))

# Настройки фильтров по умолчанию
DEFAULT_FILTERS = {
    "city": os.getenv("DEFAULT_CITY", "барановичи"),
    "region": "brestskaya-oblast",
    "min_rooms": int(os.getenv("DEFAULT_MIN_ROOMS", "1")),
    "max_rooms": int(os.getenv("DEFAULT_MAX_ROOMS", "4")),
    "min_price": int(os.getenv("DEFAULT_MIN_PRICE", "0")),
    "max_price": int(os.getenv("DEFAULT_MAX_PRICE", "100000")),
}

# Количество фотографий
MAX_PHOTOS = int(os.getenv("MAX_PHOTOS", "3"))

# Kufar API настройки
KUFAR_BASE_URL = "https://api.kufar.by/search-api/v2/search/rendered-paginated"
KUFAR_LISTING_URL = "https://www.kufar.by/item/"

# Города Брестской области с ID для Kufar
CITIES = {
    "барановичи": "1",
    "брест": "2", 
    "пинск": "3",
    "кобрин": "4",
    "береза": "5",
}

# База данных
DATABASE_PATH = "apartments.db"

# User Agent для запросов
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

