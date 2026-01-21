-- ============================================================
-- ДИАГНОСТИЧЕСКИЙ БЛОК SQL-ЗАПРОСОВ
-- Цель: определить баг парсинга или реальные одинаковые адреса
-- ============================================================

-- ============================================================
-- 1. Посмотреть схему таблицы apartments
-- ============================================================

-- Для SQLite / Turso: вывести CREATE TABLE
SELECT sql FROM sqlite_master WHERE type='table' AND name='apartments';

-- Для PostgreSQL: \d apartments (выполнить в psql)

-- Цель: убедиться, что столбцы называются так, как мы думаем 
-- (ad_id, source, address, created_at), и что есть уникальный 
-- индекс на (source, ad_id).


-- ============================================================
-- 2. Проверка топ-адресов и их количества (быстрое SQL)
-- ============================================================

-- Топ адресов по количеству объявлений
SELECT address, COUNT(*) AS cnt
FROM apartments
GROUP BY address
ORDER BY cnt DESC
LIMIT 50;

-- Цель: увидеть, действительно ли одна строка адреса повторяется 
-- сотни раз.


-- ============================================================
-- 3. Проверка уникальности ad_id при одинаковом адресе
-- ============================================================

-- Показать ad_id для проблемного адреса 
-- (подставить одно из адресов из результата шага 2)
-- ЗАМЕНИТЕ '<PROBLEM_ADDRESS>' на реальный адрес из шага 2
SELECT ad_id, title, url, created_at, source
FROM apartments
WHERE address = '<PROBLEM_ADDRESS>'
ORDER BY created_at DESC
LIMIT 200;

-- Цель: проверить, разные ли ad_id у строк с одинаковым адресом. 
-- Если ad_id одинаковые — это одна запись, но может быть дубль 
-- в sent list; если ad_id разные — это множество разных объявлений, 
-- у которых совпадает адрес.


-- ============================================================
-- 4. Проверка, какие объявления были отправлены пользователю 
--    (join sent_ads → apartments)
-- ============================================================

-- Показать отправленные объявления для проблемного адреса
-- ЗАМЕНИТЕ '<PROBLEM_ADDRESS>' на реальный адрес из шага 2
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

-- Цель: увидеть, отправлялись ли разным юзерам/одному юзеру 
-- разные ad_id с тем же адресом.


-- ============================================================
-- 5. Проверка, есть ли дубликаты ad_id в таблице apartments
-- ============================================================

SELECT ad_id, COUNT(*) as cnt
FROM apartments
GROUP BY ad_id
HAVING cnt > 1
ORDER BY cnt DESC
LIMIT 50;

-- Цель: убедиться, что у тебя нет дублей по ad_id в БД 
-- (если есть — INSERT OR IGNORE/unique индекс не сработал).


-- ============================================================
-- 6. Дополнительные диагностические запросы
-- ============================================================

-- Проверка уникального индекса на (source, ad_id)
SELECT 
    name, 
    sql 
FROM sqlite_master 
WHERE type='index' 
AND name='idx_apartments_source_ad_id';

-- Статистика по источникам
SELECT 
    source, 
    COUNT(*) as total_ads,
    COUNT(DISTINCT address) as unique_addresses,
    COUNT(*) * 1.0 / COUNT(DISTINCT address) as avg_ads_per_address
FROM apartments
GROUP BY source
ORDER BY total_ads DESC;

-- Проверка адресов с наибольшим количеством разных ad_id
SELECT 
    address,
    COUNT(DISTINCT ad_id) as unique_ad_ids,
    COUNT(*) as total_rows,
    GROUP_CONCAT(DISTINCT source) as sources
FROM apartments
GROUP BY address
HAVING COUNT(DISTINCT ad_id) > 5
ORDER BY unique_ad_ids DESC
LIMIT 20;

-- Проверка, есть ли адреса с одинаковым ad_id (не должно быть)
SELECT 
    ad_id,
    COUNT(DISTINCT address) as unique_addresses,
    GROUP_CONCAT(DISTINCT address) as addresses
FROM apartments
GROUP BY ad_id
HAVING COUNT(DISTINCT address) > 1
ORDER BY unique_addresses DESC
LIMIT 20;
