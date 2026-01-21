#!/bin/bash
# Integration test для city flow
# Эмулирует: пользователь вводит "Барановичи" -> валидация -> сохранение -> использование в парсере

set -e

echo "🧪 Запуск integration теста city flow..."

# Проверяем, что Python доступен
if ! command -v python3 &> /dev/null; then
    echo "❌ python3 не найден"
    exit 1
fi

# Переходим в корневую директорию проекта
cd "$(dirname "$0")/.."

# Создаем временный скрипт для теста
cat > /tmp/test_city_flow.py << 'EOF'
import asyncio
import sys
import os

# Добавляем путь к проекту
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.location_service import validate_city_input, search_locations

async def test_city_flow():
    """Тестирует полный flow работы с городом"""
    print("1. Тестирую search_locations('Барановичи')...")
    locations = await search_locations("Барановичи")
    
    if not locations:
        print("❌ Не найдено локаций для 'Барановичи'")
        return False
    
    print(f"✅ Найдено {len(locations)} локаций")
    for loc in locations[:3]:
        print(f"   - {loc.get('name')} ({loc.get('region')})")
    
    print("\n2. Тестирую validate_city_input('Барановичи')...")
    validation = await validate_city_input("Барановичи")
    
    print(f"   Статус: {validation['status']}")
    
    if validation['status'] == 'ok':
        location = validation['location']
        print(f"✅ Автоматически выбран: {location.get('name')} (id={location.get('id')})")
        print(f"   Slug: {location.get('slug')}")
        print(f"   Координаты: {location.get('lat')}, {location.get('lng')}")
        return True
    elif validation['status'] == 'multiple':
        print(f"⚠️  Найдено {len(validation['choices'])} вариантов (нужен выбор)")
        return True
    else:
        print(f"❌ Неожиданный статус: {validation['status']}")
        return False

if __name__ == "__main__":
    result = asyncio.run(test_city_flow())
    sys.exit(0 if result else 1)
EOF

# Запускаем тест
python3 /tmp/test_city_flow.py

if [ $? -eq 0 ]; then
    echo "✅ Integration тест пройден успешно"
    rm -f /tmp/test_city_flow.py
    exit 0
else
    echo "❌ Integration тест провален"
    rm -f /tmp/test_city_flow.py
    exit 1
fi
