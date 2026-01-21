"""
Конфигурация бота для мониторинга объявлений о квартирах
"""
import os
from dotenv import load_dotenv

load_dotenv()

# Telegram настройки
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# Интервал проверки в минутах (30 минут по умолчанию)
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "30"))

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

# Turso Database настройки (для кэширования объявлений)
TURSO_DB_URL = os.getenv("TURSO_DB_URL", "")
TURSO_AUTH_TOKEN = os.getenv("TURSO_AUTH_TOKEN", "")
USE_TURSO_CACHE = os.getenv("USE_TURSO_CACHE", "true").lower() == "true"

# Источники объявлений по умолчанию
DEFAULT_SOURCES = ["kufar", "etagi"]

# Группировка по продавцу/агентству для уменьшения дублей
GROUP_BY_VENDOR_FOR_ADDRESS = os.getenv("GROUP_BY_VENDOR_FOR_ADDRESS", "true").lower() == "true"

# User Agent для запросов
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# Администраторы бота (ID из Telegram)
# Формат: ADMIN_TELEGRAM_IDS=714797710,123456789 (через запятую)
# По умолчанию: 714797710
ADMIN_TELEGRAM_IDS = {
    int(x)
    for x in os.getenv("ADMIN_TELEGRAM_IDS", "714797710").split(",")
    if x.strip().isdigit()
}

# Импортируем константы из constants/constants.py
try:
    from constants.constants import (
        MAX_GROUPS_IN_SUMMARY,
        MAX_LISTINGS_PER_GROUP_PREVIEW,
        DELIVERY_MODE_BRIEF,
        DELIVERY_MODE_FULL,
        DELIVERY_MODE_DEFAULT,
    )
except ImportError:
    # Fallback для обратной совместимости
    MAX_GROUPS_IN_SUMMARY = 5
    MAX_LISTINGS_PER_GROUP_PREVIEW = 5
    DELIVERY_MODE_BRIEF = "brief"
    DELIVERY_MODE_FULL = "full"
    DELIVERY_MODE_DEFAULT = "brief"

