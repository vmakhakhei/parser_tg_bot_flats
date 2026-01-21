# Диагностический блок для анализа проблемы с адресами

Этот набор файлов поможет определить, является ли проблема багом парсинга или реальными одинаковыми адресами.

## Файлы

1. **diagnostic_queries.sql** - SQL запросы для диагностики базы данных
2. **save_kufar_raw_response.py** - Скрипт для сохранения сырых ответов API Kufar

## Использование

### 1. Проверка схемы таблицы apartments

Выполните первый запрос из `diagnostic_queries.sql`:

```sql
SELECT sql FROM sqlite_master WHERE type='table' AND name='apartments';
```

**Цель:** Убедиться, что столбцы называются так, как мы думаем (ad_id, source, address, created_at), и что есть уникальный индекс на (source, ad_id).

### 2. Проверка топ-адресов

Выполните второй запрос:

```sql
SELECT address, COUNT(*) AS cnt
FROM apartments
GROUP BY address
ORDER BY cnt DESC
LIMIT 50;
```

**Цель:** Увидеть, действительно ли одна строка адреса повторяется сотни раз.

### 3. Проверка уникальности ad_id при одинаковом адресе

Выполните третий запрос, заменив `<PROBLEM_ADDRESS>` на один из адресов из результата шага 2:

```sql
SELECT ad_id, title, url, created_at, source
FROM apartments
WHERE address = '<PROBLEM_ADDRESS>'
ORDER BY created_at DESC
LIMIT 200;
```

**Цель:** Проверить, разные ли ad_id у строк с одинаковым адресом. 
- Если ad_id одинаковые — это одна запись, но может быть дубль в sent list
- Если ad_id разные — это множество разных объявлений, у которых совпадает адрес

### 4. Проверка отправленных объявлений

Выполните четвертый запрос:

```sql
SELECT 
    s.user_id, 
    a.ad_id, 
    a.address, 
    a.title, 
    s.sent_at,
    a.source
FROM sent_ads s
JOIN apartments a ON s.ad_external_id = a.ad_id
WHERE a.address = '<PROBLEM_ADDRESS>'
ORDER BY s.sent_at DESC
LIMIT 200;
```

**Цель:** Увидеть, отправлялись ли разным юзерам/одному юзеру разные ad_id с тем же адресом.

### 5. Проверка дубликатов ad_id

```sql
SELECT ad_id, COUNT(*) as cnt
FROM apartments
GROUP BY ad_id
HAVING cnt > 1
ORDER BY cnt DESC
LIMIT 50;
```

**Цель:** Убедиться, что нет дублей по ad_id в БД (если есть — INSERT OR IGNORE/unique индекс не сработал).

### 6. Сохранение сырых ответов API

Запустите скрипт для сохранения сырых ответов API:

```bash
python save_kufar_raw_response.py барановичи 3
```

Параметры:
- Первый аргумент: город (по умолчанию "барановичи")
- Второй аргумент: количество страниц для парсинга (по умолчанию 3)

Скрипт создаст файл `kufar_raw_run_YYYYMMDD_HHMMSS.json` с:
- `metadata` - информация о запросе
- `raw_api_responses` - массив сырых JSON ответов от API (каждый содержит поле 'ads')
- `parsed_listings` - массив распарсенных объявлений

**Цель:** Проверить, какие поля приходят у каждого объявления (особенно: id, location, address, place, coords, description).

## Интерпретация результатов

### Если в шаге 2 видно много одинаковых адресов:

1. **Проверьте шаг 3:** Если у одинаковых адресов разные ad_id — это нормально (много объявлений по одному адресу)
2. **Проверьте шаг 5:** Если есть дубликаты ad_id — проблема с уникальным индексом
3. **Проверьте шаг 6:** Если в raw JSON разные адреса, но парсер их нормализует одинаково — проблема парсинга

### Если в шаге 3 видно одинаковые ad_id:

- Проверьте шаг 5 — возможно, уникальный индекс не работает
- Проверьте логи создания индекса

### Если в raw JSON (шаг 6) адреса разные, но в БД одинаковые:

- Проблема в парсере (файл `scrapers/kufar.py`, метод `_parse_ad`)
- Проверьте логику извлечения адреса из API ответа

## Структура таблицы apartments

Согласно коду в `database_turso.py`:

- `ad_id TEXT PRIMARY KEY` - ID объявления (формат: "kufar_1048044245")
- `source TEXT NOT NULL` - Источник ("kufar", "onliner", etc.)
- `address TEXT` - Адрес объявления
- `created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP` - Дата создания записи
- Уникальный индекс: `idx_apartments_source_ad_id` на `(source, ad_id)`

## Структура таблицы sent_ads

Согласно коду в `database.py`:

- `user_id TEXT NOT NULL` - ID пользователя
- `ad_external_id TEXT NOT NULL` - Внешний ID объявления (listing.id)
- `sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP` - Время отправки
- Уникальный индекс: `idx_sent_user_ad` на `(user_id, ad_external_id)`
