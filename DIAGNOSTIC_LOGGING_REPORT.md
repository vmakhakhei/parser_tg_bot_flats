# Отчет о диагностическом логировании

## Дата выполнения
2026-01-22 00:35:00

## Выполненные изменения

### 1. Константы логов ✅
**Файл:** `constants/constants.py`

Добавлены константы:
- `LOG_FILTER_SAVE = "[FILTER_SAVE]"`
- `LOG_FILTER_LOAD = "[FILTER_LOAD]"`
- `LOG_FILTER_VERIFY = "[FILTER_VERIFY]"`
- `LOG_USER_SEARCH = "[USER_SEARCH]"`
- `LOG_KUFAR_REQ = "[KUFAREQ]"`
- `LOG_KUFAR_RESP = "[KUFARESP]"`
- `LOG_KUFAR_LOOKUP = "[KUFAR_LOOKUP]"`

### 2. Верификация сохранения города ✅
**Файл:** `bot/handlers/start.py`

**Изменения:**
- Добавлено логирование `LOG_FILTER_SAVE` перед сохранением
- Добавлено логирование `LOG_FILTER_VERIFY` после сохранения с проверкой
- Проверка соответствия сохраненного города ожидаемому значению
- Сообщение об ошибке пользователю при несоответствии

**Пример логов:**
```
[FILTER_SAVE] user=123456 saving city_raw='Полоцк' city_norm='полоцк'
[FILTER_VERIFY] user=123456 after_save={'city': {...}, 'min_rooms': 1, ...}
```

### 3. Логирование в pipeline поиска ✅
**Файл:** `bot/services/search_service.py`

**Изменения:**
- Добавлен `LOG_USER_SEARCH` в начале обработки каждого пользователя
- Проверка наличия city с логированием и skip при отсутствии
- Передача `user_id` в aggregator для контекста в логах

**Пример логов:**
```
[USER_SEARCH] user=123456 filters={'city': {...}, 'min_rooms': 1, ...}
[FILTER_BLOCK] search skipped: user=123456 no city or filters
```

### 4. Логирование запросов к Kufar ✅
**Файл:** `scrapers/kufar.py`

**Изменения:**
- Добавлен параметр `user_id` в `fetch_listings()` для логирования
- `LOG_KUFAR_REQ` - логирование параметров запроса перед отправкой
- `LOG_KUFAR_RESP` - логирование статуса и количества результатов

**Пример логов:**
```
[KUFAREQ] user=123456 city=Полоцк params={'cat': '1010', 'gtsy': '...', ...}
[KUFARESP] user=123456 city=Полоцк status=200 count=15 total=45
[KUFARESP] user=123456 city=Полоцк status=empty
```

### 5. Lookup города для Kufar ✅
**Файл:** `scrapers/kufar.py`

**Функции:**
- `lookup_kufar_location_async()` - асинхронный lookup через Kufar API
- `lookup_kufar_location()` - синхронная обертка для CLI

**Особенности:**
- Пробует два endpoint:
  - `https://api.kufar.by/search-api/v1/autocomplete/location`
  - `https://www.kufar.by/api/search/locations`
- Кэширование результатов в таблице `kufar_city_cache`
- Логирование всех этапов lookup

**Пример логов:**
```
[KUFAR_LOOKUP] query=Полоцк cache_miss=True
[KUFAR_LOOKUP] query=Полоцк url=... params={'q': 'Полоцк'}
[KUFARESP] lookup status=200 text_len=1234 url=...
[KUFAR_LOOKUP] query=Полоцк result={...}
```

### 6. Таблица кэша Kufar ✅
**Файл:** `database_turso.py`

**Таблица:** `kufar_city_cache`
```sql
CREATE TABLE kufar_city_cache (
    city_normalized TEXT PRIMARY KEY,
    payload TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Функции:**
- `get_kufar_city_cache(city_normalized)` - получение из кэша
- `set_kufar_city_cache(city_normalized, payload)` - сохранение в кэш

### 7. Админ-команда ✅
**Файл:** `bot/handlers/admin.py`

**Команда:** `/admin_kufar_city_lookup <город>`

**Функциональность:**
- Вызывает `lookup_kufar_location_async()`
- Показывает результат пользователю
- Сохраняет в кэш
- Доступна только админам

**Пример использования:**
```
/admin_kufar_city_lookup Полоцк
```

### 8. CLI скрипт ✅
**Файл:** `tools/kufar_city_lookup.py`

**Использование:**
```bash
python3 tools/kufar_city_lookup.py "Полоцк"
```

**Функциональность:**
- Проверка кэша
- Выполнение lookup через API
- Сохранение в кэш
- Вывод JSON результата

### 9. Unit тесты ✅
**Файл:** `tests/test_filters_save.py`

**Тесты:**
- `test_filters_save_and_load()` - проверка сохранения и загрузки фильтров
- `test_filters_save_city_dict()` - проверка сохранения города как location dict

## Измененные файлы

1. `constants/constants.py` - добавлены константы логов
2. `bot/handlers/start.py` - верификация сохранения города
3. `bot/services/search_service.py` - логирование в pipeline поиска
4. `scrapers/kufar.py` - логирование запросов и lookup функция
5. `scrapers/aggregator.py` - передача user_id в fetch_listings
6. `database_turso.py` - таблица кэша и функции работы с кэшем
7. `bot/handlers/admin.py` - админ-команда для lookup
8. `tools/kufar_city_lookup.py` - CLI скрипт для тестирования
9. `tests/test_filters_save.py` - unit тесты для проверки сохранения

## Коммиты

1. `0233858` - feat: add diagnostic logging for city filters and Kufar API
2. `5d90899` - refactor: use log constants in database_turso
3. `51f806d` - fix: complete LOG_FILTER_LOAD migration in get_user_filters_turso
4. `3660a52` - fix: improve CLI script error handling and async usage

## SQL-проверки (после деплоя)

```sql
-- Проверить сохранённые фильтры для пользователя
SELECT telegram_id, city, city_json, min_price, max_price 
FROM user_filters 
WHERE telegram_id = 714797710;

-- Проверить кэш города
SELECT * FROM kufar_city_cache WHERE city_normalized = 'полоцк';
```

## Тестирование

### 1. Telegram flow
```
/start → выбрать город Полоцк (или отправить текст Полоцк)
```

**Ожидаемые логи:**
```
[FILTER_SAVE] user=... saving city_raw='Полоцк' city_norm='полоцк'
[FILTER_VERIFY] user=... after_save={...}
```

### 2. Админ-команда
```
/admin_kufar_city_lookup Полоцк
```

**Ожидаемые логи:**
```
[KUFAR_LOOKUP] admin command city=Полоцк
[KUFAR_LOOKUP] query=Полоцк cache_miss=True
[KUFAR_LOOKUP] query=Полоцк url=... params={...}
[KUFARESP] lookup status=200 ...
```

### 3. Debug run
```
/debug run
```

**Ожидаемые логи:**
```
[USER_SEARCH] user=... filters={...}
[KUFAREQ] user=... city=... params={...}
[KUFARESP] user=... city=... status=... count=...
```

### 4. CLI скрипт
```bash
python3 tools/kufar_city_lookup.py "Полоцк"
```

**Ожидаемый вывод:**
- Проверка кэша
- Выполнение lookup
- JSON результат
- Статус кэша

## Статус

✅ **Все изменения выполнены и закоммичены**
✅ **Деплой в main ветку выполнен**
✅ **DEPLOY_STATUS.md обновлен локально (не закоммичен)**

## Следующие шаги

1. Перезапустить сервис на платформе деплоя
2. Выполнить тесты из раздела "Тестирование"
3. Проверить логи на наличие всех тегов: `[FILTER_SAVE]`, `[FILTER_VERIFY]`, `[USER_SEARCH]`, `[KUFAREQ]`, `[KUFARESP]`, `[KUFAR_LOOKUP]`
4. Выполнить SQL-проверки для проверки сохраненных данных
