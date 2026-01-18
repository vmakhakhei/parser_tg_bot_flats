#!/bin/bash
# Скрипт для запуска тестов проекта parser_tg_bot_flats

set -e  # Остановка при ошибке

# Цвета для вывода
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}🧪 Запуск тестов для parser_tg_bot_flats${NC}\n"

# Проверяем, установлен ли pytest
if ! command -v pytest &> /dev/null; then
    echo -e "${YELLOW}⚠️  pytest не установлен. Устанавливаю зависимости...${NC}"
    pip install -r requirements.txt
fi

# Проверяем наличие виртуального окружения (рекомендуется)
if [ -z "$VIRTUAL_ENV" ] && [ ! -d "venv" ] && [ ! -d ".venv" ]; then
    echo -e "${YELLOW}⚠️  Рекомендуется использовать виртуальное окружение:${NC}"
    echo -e "   python3 -m venv venv"
    echo -e "   source venv/bin/activate  # Linux/Mac"
    echo -e "   venv\\Scripts\\activate  # Windows"
    echo ""
fi

# Параметры по умолчанию
TEST_PATH="${1:-tests/}"
VERBOSE="${2:--v}"
COVERAGE="${3:-}"

echo -e "${GREEN}📋 Параметры запуска:${NC}"
echo -e "   Путь к тестам: ${TEST_PATH}"
echo -e "   Режим: ${VERBOSE}"
if [ -n "$COVERAGE" ]; then
    echo -e "   Покрытие кода: включено"
fi
echo ""

# Запускаем тесты
if [ -n "$COVERAGE" ]; then
    echo -e "${BLUE}Запуск тестов с покрытием кода...${NC}\n"
    pytest "${TEST_PATH}" ${VERBOSE} --cov=scrapers --cov-report=html --cov-report=term-missing
    echo -e "\n${GREEN}✅ Отчет о покрытии сохранен в htmlcov/index.html${NC}"
else
    echo -e "${BLUE}Запуск тестов...${NC}\n"
    pytest "${TEST_PATH}" ${VERBOSE}
fi

echo -e "\n${GREEN}✅ Тесты завершены!${NC}"
