# Location Service Documentation

## Обзор

Location Service обеспечивает работу с локациями через Kufar autocomplete API с кэшированием, валидацией и fallback механизмами.

## Архитектура

### Компоненты

1. **`services/location_service.py`** - Основной сервис для работы с локациями
2. **Таблица `locations_cache`** - Кэш локаций в Turso DB
3. **Поле `city_json`** в `user_filters` - Хранение location dict как JSON

### Поток работы

```
Пользователь вводит город
    ↓
validate_city_input()
    ↓
search_locations() → проверка кэша → запрос к Kufar API → сохранение в кэш
    ↓
Результат: not_found | ok (auto) | multiple | too_many
    ↓
Сохранение в user_filters.city_json
    ↓
Использование в парсере Kufar через slug/id
```

## API

### `search_locations(query: str) -> List[Dict]`

Ищет локации через Kufar autocomplete API с кэшированием.

**Параметры:**
- `query`: Поисковый запрос (название города)

**Возвращает:**
- Список нормализованных локаций с полями: `id`, `name`, `region`, `type`, `slug`, `lat`, `lng`, `raw`

**Особенности:**
- Проверяет кэш перед запросом к API
- Использует retry с экспоненциальным backoff (3 попытки)
- Таймаут 5 секунд
- Сохраняет результаты в кэш с TTL 30 дней

### `validate_city_input(user_input: str) -> Dict`

Валидирует ввод города пользователем.

**Возвращает:**
- `{"status": "not_found"}` - город не найден
- `{"status": "ok", "location": {...}, "auto": True}` - один результат, автоматически выбран
- `{"status": "multiple", "choices": [...]}` - 2-5 результатов, нужен выбор
- `{"status": "too_many"}` - больше 5 результатов, нужен уточненный запрос

### `get_location_by_id(location_id: str) -> Dict | None`

Получает локацию по ID из кэша.

## Кэширование

### Таблица `locations_cache`

```sql
CREATE TABLE locations_cache (
    id TEXT PRIMARY KEY,
    name TEXT,
    region TEXT,
    type TEXT,
    slug TEXT,
    lat REAL,
    lng REAL,
    raw_json TEXT,
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**TTL:** 30 дней (конфиг `LOC_CACHE_TTL_DAYS`)

## Интеграция с парсером

### Kufar Scraper

Парсер Kufar использует location dict для формирования запроса:

1. Если `city` - это dict и есть `slug` → используется `slug` напрямую
2. Если нет `slug` → используется fallback на имя города через маппинг
3. Логируется `[LOC_FALLBACK_SEARCH]` при использовании fallback

**Конфигурация:**
- `KUFAR_USE_SLUG_FOR_SEARCH = True` - использовать slug из location
- `KUFAR_SEARCH_RADIUS_METERS = 10000` - радиус поиска (если используется координаты)

## Миграция существующих пользователей

### Скрипт `tools/migrate_cities.py`

Автоматически мигрирует пользователей с `city_string` на `city_json`:

1. Находит всех пользователей с `city_string` но без `city_json`
2. Вызывает `search_locations(city_string)`
3. Если 1 результат → автоматически сохраняет `city_json`
4. Если несколько → добавляет в CSV для ручной проверки

**Запуск:**
```bash
python3 tools/migrate_cities.py
```

**Результат:**
- Статистика миграции в логах
- CSV файл `city_migration_review_YYYYMMDD_HHMMSS.csv` с пользователями для проверки

## Fallback механизмы

### OpenStreetMap Nominatim

Если Kufar API недоступен и `ENABLE_OSM_FALLBACK = True`:

- Используется Nominatim API
- Конвертируется формат OSM в нормализованный формат location
- Логируется `[LOC_FALLBACK_OSM]`

**Конфигурация:**
- `ENABLE_OSM_FALLBACK = False` (по умолчанию)

## Логирование

Все операции логируются с префиксами:

- `[LOC_SEARCH]` - поиск локаций
- `[LOC_CACHE_HIT]` - попадание в кэш
- `[LOC_CACHE_MISS]` - промах кэша
- `[LOC_SAVE]` - сохранение в кэш
- `[LOC_VALIDATE]` - валидация ввода
- `[LOC_USER_SELECT]` - выбор пользователем
- `[LOC_FALLBACK_OSM]` - использование OSM fallback
- `[LOC_FALLBACK_SEARCH]` - использование fallback маппинга в парсере
- `[LOC_PARSER]` - использование location в парсере
- `[MIGRATE]` - миграция пользователей

## Конфигурация

Константы в `config.py`:

```python
KUFAR_AUTOCOMPLETE_URL = "https://api.kufar.by/search-api/v1/autocomplete/location"
LOCATION_SERVICE_TIMEOUT = 5
ENABLE_OSM_FALLBACK = False
KUFAR_USE_SLUG_FOR_SEARCH = True
KUFAR_SEARCH_RADIUS_METERS = 10000
```

Константы в `constants/constants.py`:

```python
LOC_CACHE_TTL_DAYS = 30
```

## Тестирование

### Unit тесты

```bash
pytest tools/tests/test_location_service.py -v
```

Тесты покрывают:
- Нормализацию локаций
- Поиск локаций (успех, кэш, ошибки)
- Валидацию ввода (все статусы)
- Получение по ID

### Integration тест

```bash
bash tools/test_city_flow.sh
```

Эмулирует полный flow: ввод города → валидация → сохранение → использование в парсере.

## Обратная совместимость

Система поддерживает обратную совместимость:

1. Старые пользователи с `city_string` продолжают работать
2. При чтении фильтров: если есть `city_json` → используется он, иначе `city` (строка)
3. Парсер поддерживает оба формата (str и dict)
4. Миграция автоматически конвертирует старые данные

## Безопасность

- Все запросы к внешним API имеют таймауты
- Retry механизм защищает от временных сбоев
- Кэширование снижает нагрузку на API
- Fallback механизмы обеспечивают работу при недоступности основного API
- Валидация ввода предотвращает некорректные данные
