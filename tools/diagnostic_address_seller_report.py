# tools/diagnostic_address_seller_report.py
import sys
import json
from pathlib import Path

# Добавляем родительскую директорию в path для импорта
sys.path.insert(0, str(Path(__file__).parent.parent))

from database_turso import get_turso_connection
from config import TURSO_DB_URL, TURSO_AUTH_TOKEN

def run(address_substring):
    """Выполняет диагностический запрос по адресу"""
    conn = get_turso_connection()
    if not conn:
        print("❌ Не удалось подключиться к БД")
        print("Проверьте TURSO_DB_URL и TURSO_AUTH_TOKEN в .env")
        return

    try:
        cur = conn.cursor()

        print(f"\n{'='*60}")
        print(f"Top vendors for address LIKE '%{address_substring}%'")
        print(f"{'='*60}\n")
        
        cur.execute("""
            SELECT COALESCE(json_extract(raw_json, '$.agency'), json_extract(raw_json, '$.seller'), 'UNKNOWN') AS vendor,
                   COUNT(*) AS cnt
            FROM apartments
            WHERE address LIKE ?
            GROUP BY vendor
            ORDER BY cnt DESC
        """, (f'%{address_substring}%',))
        
        results = cur.fetchall()
        if not results:
            print("⚠️  Объявления не найдены")
        else:
            for row in results:
                vendor, cnt = row
                print(f"  {vendor}: {cnt} объявлений")

        print(f"\n{'='*60}")
        print(f"Full list for address LIKE '%{address_substring}%'")
        print(f"{'='*60}\n")
        
        cur.execute("""
            SELECT ad_id, source, url, created_at,
                   json_extract(raw_json, '$.agency') AS agency,
                   json_extract(raw_json, '$.seller') AS seller,
                   price_usd
            FROM apartments
            WHERE address LIKE ?
            ORDER BY created_at DESC
        """, (f'%{address_substring}%',))
        
        results = cur.fetchall()
        if not results:
            print("⚠️  Объявления не найдены")
        else:
            print(f"Найдено объявлений: {len(results)}\n")
            for r in results:
                ad_id, source, url, created_at, agency, seller, price_usd = r
                vendor = agency or seller or "UNKNOWN"
                print(f"  ad_id: {ad_id}")
                print(f"    source: {source}")
                print(f"    vendor: {vendor}")
                print(f"    price: ${price_usd:,}" if price_usd else "    price: не указана")
                print(f"    created_at: {created_at}")
                print(f"    url: {url}")
                print()
        
    except Exception as e:
        print(f"❌ Ошибка при выполнении запроса: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if conn:
            conn.close()

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python tools/diagnostic_address_seller_report.py 'Николы Теслы ул'")
        print("\nПример:")
        print("  python tools/diagnostic_address_seller_report.py 'Николы Теслы ул'")
        sys.exit(1)
    
    address = sys.argv[1]
    run(address)
