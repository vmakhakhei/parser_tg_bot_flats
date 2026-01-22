#!/bin/bash
# Railway post-deploy script
# Выполняется автоматически после успешного деплоя на Railway
# Если любой шаг завершается с ошибкой (exit code != 0), Railway пометит деплой как failed

set -e  # Exit on error
set -u  # Exit on undefined variable

echo "=========================================="
echo "[POSTDEPLOY] Starting post-deploy checks"
echo "=========================================="

# Переходим в корневую директорию проекта
cd "$(dirname "$0")" || exit 1

# Проверяем наличие Python
if ! command -v python3 &> /dev/null; then
    echo "[POSTDEPLOY][ERROR] python3 not found"
    exit 1
fi

# Шаг 1: Smoke tests
echo ""
echo "[POSTDEPLOY] Step 1: Running smoke tests..."
if python3 tools/postdeploy_smoke.py; then
    echo "[POSTDEPLOY][OK] Smoke tests passed"
else
    echo "[POSTDEPLOY][FAIL] Smoke tests failed"
    exit 1
fi

# Шаг 2: Database health check
echo ""
echo "[POSTDEPLOY] Step 2: Running database health check..."
if python3 tools/db_healthcheck.py; then
    echo "[POSTDEPLOY][OK] Database health check passed"
else
    echo "[POSTDEPLOY][FAIL] Database health check failed"
    exit 1
fi

# Шаг 3: Stale records check (informational only)
echo ""
echo "[POSTDEPLOY] Step 3: Checking stale records..."
if python3 tools/stale_check.py; then
    echo "[POSTDEPLOY][OK] Stale check completed"
else
    echo "[POSTDEPLOY][WARNING] Stale check failed (non-critical)"
    # Не блокируем деплой из-за stale check
fi

echo ""
echo "=========================================="
echo "[POSTDEPLOY][OK] All post-deploy checks completed successfully"
echo "=========================================="

exit 0
