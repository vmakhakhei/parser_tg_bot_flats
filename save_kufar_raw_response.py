"""
Скрипт для сохранения сырых ответов API Kufar в файл
Использование: python save_kufar_raw_response.py [город] [max_pages]
Пример: python save_kufar_raw_response.py барановичи 3
"""
import sys
import json
import asyncio
from datetime import datetime
from pathlib import Path

# Добавляем путь к проекту
sys.path.insert(0, str(Path(__file__).parent))

from scrapers.kufar import KufarScraper


async def save_kufar_raw_response(
    city: str = "барановичи",
    min_rooms: int = 1,
    max_rooms: int = 4,
    min_price: int = 0,
    max_price: int = 100000,
    max_pages: int = 3
):
    """
    Сохраняет сырые ответы API Kufar в JSON файл
    
    Args:
        city: Город для поиска
        min_rooms: Минимальное количество комнат
        max_rooms: Максимальное количество комнат
        min_price: Минимальная цена
        max_price: Максимальная цена
        max_pages: Максимальное количество страниц для парсинга
    """
    print(f"=" * 60)
    print(f"Сохранение сырых ответов API Kufar")
    print(f"=" * 60)
    print(f"Город: {city}")
    print(f"Комнаты: {min_rooms}-{max_rooms}")
    print(f"Цена: ${min_price}-${max_price}")
    print(f"Максимум страниц: {max_pages}")
    print()
    
    scraper = KufarScraper()
    
    try:
        # Получаем объявления и raw JSON ответы
        print("Запрос к API Kufar...")
        listings, raw_api_responses = await scraper.fetch_listings_with_raw_json(
            city=city,
            min_rooms=min_rooms,
            max_rooms=max_rooms,
            min_price=min_price,
            max_price=max_price,
            max_pages=max_pages
        )
        
        print(f"Получено {len(listings)} объявлений с {len(raw_api_responses)} страниц")
        
        # Формируем имя файла с датой и временем
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"kufar_raw_run_{timestamp}.json"
        filepath = Path(__file__).parent / filename
        
        # Подготавливаем данные для сохранения
        output_data = {
            "metadata": {
                "city": city,
                "min_rooms": min_rooms,
                "max_rooms": max_rooms,
                "min_price": min_price,
                "max_price": max_price,
                "max_pages": max_pages,
                "timestamp": timestamp,
                "total_listings": len(listings),
                "total_pages": len(raw_api_responses)
            },
            "raw_api_responses": raw_api_responses,
            "parsed_listings": [
                {
                    "id": listing.id,
                    "source": listing.source,
                    "title": listing.title,
                    "price": listing.price,
                    "price_usd": listing.price_usd,
                    "price_byn": listing.price_byn,
                    "rooms": listing.rooms,
                    "area": listing.area,
                    "address": listing.address,
                    "url": listing.url,
                    "floor": listing.floor,
                    "created_at": listing.created_at,
                    "is_company": listing.is_company,
                    "description": listing.description[:200] if listing.description else "",  # Первые 200 символов
                }
                for listing in listings
            ]
        }
        
        # Сохраняем в файл
        print(f"Сохранение в файл: {filepath}")
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
        
        print(f"✅ Файл сохранен: {filepath}")
        print()
        print("Структура файла:")
        print(f"  - metadata: информация о запросе")
        print(f"  - raw_api_responses: массив сырых JSON ответов от API (каждый содержит поле 'ads')")
        print(f"  - parsed_listings: массив распарсенных объявлений")
        print()
        print("Для анализа проверьте поля в raw_api_responses:")
        print("  - ads[].ad_id - ID объявления")
        print("  - ads[].location - локация")
        print("  - ads[].address - адрес")
        print("  - ads[].place - место")
        print("  - ads[].coords - координаты")
        print("  - ads[].description - описание")
        print("  - ads[].ad_parameters[] - параметры объявления")
        print("  - ads[].account_parameters[] - параметры аккаунта")
        
        return filepath
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        return None


def main():
    """Точка входа"""
    # Парсим аргументы командной строки
    city = sys.argv[1] if len(sys.argv) > 1 else "барановичи"
    max_pages = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    
    # Запускаем асинхронную функцию
    filepath = asyncio.run(save_kufar_raw_response(
        city=city,
        max_pages=max_pages
    ))
    
    if filepath:
        print(f"\n✅ Готово! Файл: {filepath}")
    else:
        print("\n❌ Не удалось сохранить данные")
        sys.exit(1)


if __name__ == "__main__":
    main()
