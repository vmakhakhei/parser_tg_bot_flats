"""
Упрощенный мастер настройки фильтров - один экран с кнопками
БЕЗ FSM, мгновенное сохранение при каждом действии
"""
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from database_turso import get_user_filters_turso, set_user_filters_turso, ensure_user_filters
import logging

logger = logging.getLogger(__name__)
router = Router()


def format_filters_summary(f: dict) -> str:
    """Форматирует сводку фильтров для отображения"""
    city = f.get('city') or 'Не выбран'
    seller_text = {
        'all': 'Все',
        'owner': 'Только собственники',
        'owners': 'Только собственники',
        'company': 'Только агентства'
    }.get(f.get('seller_type', 'all'), 'Все')
    mode_text = 'Кратко' if f.get('delivery_mode', 'brief') == 'brief' else 'Подробно'
    
    return (
        f"📍 Город: {city}\n"
        f"🚪 Комнаты: {f.get('min_rooms', 1)}–{f.get('max_rooms', 4)}\n"
        f"💰 Цена: ${f.get('min_price', 0):,} – ${f.get('max_price', 100000):,}\n"
        f"👤 Продавец: {seller_text}\n"
        f"📡 Режим: {mode_text}"
    )


def build_filters_keyboard(telegram_id: int) -> InlineKeyboardMarkup:
    """Строит клавиатуру для быстрой настройки фильтров"""
    kb = InlineKeyboardMarkup(row_width=3)

    # Комнаты
    kb.add(
        InlineKeyboardButton("1", callback_data=f"filters:{telegram_id}:rooms:1"),
        InlineKeyboardButton("2", callback_data=f"filters:{telegram_id}:rooms:2"),
        InlineKeyboardButton("3", callback_data=f"filters:{telegram_id}:rooms:3"),
        InlineKeyboardButton("4+", callback_data=f"filters:{telegram_id}:rooms:4+"),
        InlineKeyboardButton("Любые", callback_data=f"filters:{telegram_id}:rooms:any"),
    )

    # Цена
    kb.add(
        InlineKeyboardButton("0–30k", callback_data=f"filters:{telegram_id}:price:0-30000"),
        InlineKeyboardButton("30–50k", callback_data=f"filters:{telegram_id}:price:30000-50000"),
        InlineKeyboardButton("50–80k", callback_data=f"filters:{telegram_id}:price:50000-80000"),
        InlineKeyboardButton("80k+", callback_data=f"filters:{telegram_id}:price:80000-99999999"),
        InlineKeyboardButton("Любая", callback_data=f"filters:{telegram_id}:price:any"),
    )

    # Тип продавца
    kb.add(
        InlineKeyboardButton("Все", callback_data=f"filters:{telegram_id}:seller:all"),
        InlineKeyboardButton("Только собственники", callback_data=f"filters:{telegram_id}:seller:owner"),
    )

    # Режим доставки
    kb.add(
        InlineKeyboardButton("📋 Кратко", callback_data=f"filters:{telegram_id}:mode:brief"),
        InlineKeyboardButton("📨 Подробно", callback_data=f"filters:{telegram_id}:mode:full"),
    )

    # Готово
    kb.add(InlineKeyboardButton("✅ Готово", callback_data=f"filters:{telegram_id}:done"))
    
    return kb


async def show_filters_master(callback_or_message, telegram_id: int):
    """Показывает мастер фильтров с текущими значениями"""
    await ensure_user_filters(telegram_id)
    filters = await get_user_filters_turso(telegram_id)
    
    if not filters:
        filters = {
            "city": None,
            "min_rooms": 1,
            "max_rooms": 4,
            "min_price": 0,
            "max_price": 100000,
            "seller_type": "all",
            "delivery_mode": "brief",
        }
    
    text = "⚙️ Быстрая настройка фильтров\n\n" + format_filters_summary(filters)
    keyboard = build_filters_keyboard(telegram_id)
    
    if isinstance(callback_or_message, CallbackQuery):
        await callback_or_message.message.edit_text(text, reply_markup=keyboard)
    else:
        await callback_or_message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data.startswith("filters:"))
async def filters_callback_handler(callback: CallbackQuery):
    """Обработчик callback для быстрой настройки фильтров"""
    try:
        # Формат: filters:telegram_id:action:value
        parts = callback.data.split(":", 3)
        if len(parts) < 3:
            await callback.answer("Ошибка обработки запроса")
            return
        
        _, telegram_id_str, action = parts[:3]
        value = parts[3] if len(parts) > 3 else None
        
        telegram_id = int(telegram_id_str)
        
        # Проверяем, что callback от правильного пользователя
        if callback.from_user.id != telegram_id:
            await callback.answer("⛔ Это не ваши фильтры")
            return
        
        # Гарантируем наличие фильтров
        await ensure_user_filters(telegram_id)
        filters = await get_user_filters_turso(telegram_id)
        
        if not filters:
            await callback.answer("Ошибка загрузки фильтров")
            return
        
        # Обрабатываем действие
        if action == "rooms":
            if value == "any":
                filters["min_rooms"], filters["max_rooms"] = 0, 99
            elif value == "4+":
                filters["min_rooms"], filters["max_rooms"] = 4, 99
            else:
                r = int(value)
                filters["min_rooms"], filters["max_rooms"] = r, r
        
        elif action == "price":
            if value == "any":
                filters["min_price"], filters["max_price"] = 0, 99999999
            else:
                a, b = value.split("-")
                filters["min_price"], filters["max_price"] = int(a), int(b)
        
        elif action == "seller":
            filters["seller_type"] = value if value else "all"
        
        elif action == "mode":
            filters["delivery_mode"] = value if value else "brief"
        
        elif action == "done":
            # Финальное сохранение
            await set_user_filters_turso(telegram_id, filters)
            await callback.message.edit_text(
                "✅ Фильтры сохранены\n\n" + format_filters_summary(filters)
            )
            await callback.answer("Сохранено")
            return
        
        # Мгновенное сохранение при каждом действии
        await set_user_filters_turso(telegram_id, filters)
        
        # Перерисовываем экран
        await show_filters_master(callback, telegram_id)
        await callback.answer()
        
    except Exception as e:
        logger.exception(f"[FILTER_QUICK] Error handling callback {callback.data}: {e}")
        await callback.answer("Произошла ошибка")
