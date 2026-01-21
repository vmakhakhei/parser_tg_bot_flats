"""
Константы для системы уведомлений и summary-сообщений
"""

# Максимальное количество групп в summary-сообщении
MAX_GROUPS_IN_SUMMARY = 5

# Максимальное количество объявлений в превью группы
MAX_LISTINGS_PER_GROUP_PREVIEW = 5

# Режимы доставки уведомлений
DELIVERY_MODE_BRIEF = "brief"
DELIVERY_MODE_FULL = "full"
DELIVERY_MODE_DEFAULT = DELIVERY_MODE_BRIEF

# Debug режим для принудительного запуска
DEBUG_FORCE_RUN = False

# Location cache TTL (дни)
LOC_CACHE_TTL_DAYS = 30

# Константы для логирования (префиксы логов)
LOG_FILTER_SAVE = "[FILTER_SAVE]"
LOG_FILTER_LOAD = "[FILTER_LOAD]"
LOG_FILTER_VERIFY = "[FILTER_VERIFY]"
LOG_USER_SEARCH = "[USER_SEARCH]"
LOG_KUFAR_REQ = "[KUFAREQ]"
LOG_KUFAR_RESP = "[KUFARESP]"
LOG_KUFAR_LOOKUP = "[KUFAR_LOOKUP]"
