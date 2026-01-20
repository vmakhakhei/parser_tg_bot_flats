-- 1. Топ адресов с > N объявлений
SELECT address, COUNT(*) AS cnt
FROM apartments
GROUP BY address
HAVING cnt > 3
ORDER BY cnt DESC
LIMIT 200;

-- 2. Для конкретного адреса: список объявлений с продавцом/agency и временем
SELECT ad_id, source, title, url, address, created_at, json_extract(raw_json, '$.seller') AS seller, json_extract(raw_json, '$.agency') AS agency, price_usd
FROM apartments
WHERE address LIKE '%Николы Теслы ул%'
ORDER BY created_at DESC;

-- 3. Группировка по (address, seller) — сколько объявлений у каждого продавца/агентства на адресе
SELECT
  COALESCE(json_extract(raw_json, '$.agency'), json_extract(raw_json, '$.seller'), 'UNKNOWN') AS vendor,
  COUNT(*) AS cnt
FROM apartments
WHERE address LIKE '%Николы Теслы ул%'
GROUP BY vendor
ORDER BY cnt DESC;

-- 4. Показать ad_id с хешем первых 3 фото (в raw_json) — для быстрой проверки одинаковых фото
SELECT ad_id,
       json_extract(raw_json, '$.photos[0]') AS photo0,
       json_extract(raw_json, '$.photos[1]') AS photo1,
       json_extract(raw_json, '$.photos[2]') AS photo2
FROM apartments
WHERE address LIKE '%Николы Теслы ул%';
