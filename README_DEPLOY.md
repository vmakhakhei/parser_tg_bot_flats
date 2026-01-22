# Deployment Guide

## Автоматический деплой на Railway

Проект настроен для автоматического деплоя при push в ветку `main`. Railway автоматически:
1. Собирает проект
2. Устанавливает зависимости
3. Запускает приложение
4. Выполняет post-deploy проверки

---

## Pre-Deploy проверки

Перед деплоем рекомендуется запустить локальные проверки:

```bash
python3 tools/predeploy_check.py
```

**Проверяет:**
- ✅ Наличие критических переменных окружения (BOT_TOKEN, TURSO_DB_URL, TURSO_AUTH_TOKEN)
- ✅ Существование файла `data/kufar_city_map.json` с минимум 300 городами
- ✅ Подключение к базе данных
- ✅ Наличие всех необходимых таблиц (apartments, sent_ads, city_codes, user_filters)
- ✅ Минимум 300 записей в таблице `city_codes`

**Выход:**
- `exit(0)` - все проверки пройдены
- `exit(1)` - найдены критические проблемы

---

## Post-Deploy проверки (автоматические)

После успешного деплоя Railway автоматически запускает `railway.postdeploy.sh`, который выполняет:

### 1. Smoke Tests (`tools/postdeploy_smoke.py`)
- ✅ Проверка city lookup для городов: Минск, Барановичи, Полоцк
- ✅ Проверка чтения фильтров пользователя
- ✅ Проверка валидации фильтров

### 2. Database Health Check (`tools/db_healthcheck.py`)
- ✅ Подсчет записей в таблицах: apartments, sent_ads, city_codes, user_filters
- ✅ Проверка доступности всех таблиц

### 3. Stale Records Check (`tools/stale_check.py`)
- ⚠️ Информационная проверка orphaned записей в `sent_ads`
- Не блокирует деплой (только предупреждение)

---

## Ручные проверки

### Проверка здоровья БД

```bash
python3 tools/db_healthcheck.py
```

Выводит количество записей во всех таблицах.

### Проверка stale записей

```bash
python3 tools/stale_check.py
```

Показывает количество orphaned записей (read-only, ничего не удаляет).

### Smoke tests

```bash
python3 tools/postdeploy_smoke.py
```

Запускает все smoke tests локально.

---

## Переменные окружения

**Обязательные:**
- `BOT_TOKEN` - токен Telegram бота
- `TURSO_DB_URL` - URL базы данных Turso
- `TURSO_AUTH_TOKEN` - токен аутентификации Turso

**Опциональные:**
- `CHECK_INTERVAL` - интервал проверки в минутах (по умолчанию: 30)
- `USE_TURSO_CACHE` - использовать Turso кэш (по умолчанию: true)
- `ADMIN_TELEGRAM_IDS` - ID администраторов через запятую

---

## Troubleshooting

### Pre-deploy проверки не проходят

1. **Missing environment variables:**
   - Убедитесь, что все переменные окружения установлены в Railway
   - Проверьте `.env` файл локально

2. **City map file not found:**
   - Убедитесь, что файл `data/kufar_city_map.json` существует
   - Запустите `python3 tools/build_city_map_from_candidates.py` если нужно

3. **Database connection failed:**
   - Проверьте `TURSO_DB_URL` и `TURSO_AUTH_TOKEN`
   - Убедитесь, что база данных доступна

### Post-deploy проверки не проходят

1. **Smoke tests failed:**
   - Проверьте логи Railway для деталей
   - Убедитесь, что city lookup работает (проверьте `city_codes` таблицу)

2. **Database health check failed:**
   - Проверьте подключение к базе данных
   - Убедитесь, что все таблицы созданы

---

## Безопасность

- ✅ Все админ-команды защищены через `is_admin()` проверку
- ✅ Скрипты проверки не изменяют данные (read-only)
- ✅ Stale check не удаляет записи автоматически
- ✅ Pre-deploy проверки выполняются локально перед push

---

## Мониторинг после деплоя

После успешного деплоя рекомендуется:

1. Проверить логи Railway на наличие ошибок
2. Убедиться, что бот отвечает на команды
3. Проверить работу city lookup через `/start`
4. Мониторить stale записи через `/admin_check_sync`

---

## Контакты

При проблемах с деплоем:
- Проверьте логи Railway
- Запустите проверки локально
- Обратитесь к администратору проекта
