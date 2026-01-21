"""
Упрощенный мастер настройки фильтров - один экран с кнопками
"""
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from database_turso import get_user_filters_turso, set_user_filters_turso, ensure_user_filters

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


def build_kb(uid: int):
    """Строит клавиатуру для быстрой настройки фильтров"""
    kb = InlineKeyboardMarkup(row_width=3)

    kb.add(
        InlineKeyboardButton("1", callback_data=f"filters|{uid}|rooms|1"),
        InlineKeyboardButton("2", callback_data=f"filters|{uid}|rooms|2"),
        InlineKeyboardButton("3", callback_data=f"filters|{uid}|rooms|3"),
        InlineKeyboardButton("4+", callback_data=f"filters|{uid}|rooms|4+"),
        InlineKeyboardButton("Любые", callback_data=f"filters|{uid}|rooms|any"),
    )

    kb.add(
        InlineKeyboardButton("0–30k", callback_data=f"filters|{uid}|price|0-30000"),
        InlineKeyboardButton("30–50k", callback_data=f"filters|{uid}|price|30000-50000"),
        InlineKeyboardButton("50–80k", callback_data=f"filters|{uid}|price|50000-80000"),
        InlineKeyboardButton("80k+", callback_data=f"filters|{uid}|price|80000-99999999"),
        InlineKeyboardButton("Любая", callback_data=f"filters|{uid}|price|any"),
    )

    kb.add(
        InlineKeyboardButton("Все", callback_data=f"filters|{uid}|seller|all"),
        InlineKeyboardButton("Только собственники", callback_data=f"filters|{uid}|seller|owners"),
    )

    kb.add(InlineKeyboardButton("✅ Готово", callback_data=f"filters|{uid}|done|1"))
    return kb


@router.callback_query(F.data.startswith("filters|"))
async def filters_cb(callback: CallbackQuery):
    """Обработчик callback для быстрой настройки фильтров"""
    parts = callback.data.split("|", 3)
    if len(parts) < 4:
        await callback.answer("Ошибка обработки запроса")
        return
    
    _, uid, action, value = parts
    telegram_id = int(uid)

    # Гарантируем наличие фильтров
    await ensure_user_filters(telegram_id)
    f = await get_user_filters_turso(telegram_id)
    
    if not f:
        await callback.answer("Ошибка загрузки фильтров")
        return

    if action == "rooms":
        if value == "any":
            f["min_rooms"], f["max_rooms"] = 0, 99
        elif value == "4+":
            f["min_rooms"], f["max_rooms"] = 4, 99
        else:
            r = int(value)
            f["min_rooms"], f["max_rooms"] = r, r

    elif action == "price":
        if value == "any":
            f["min_price"], f["max_price"] = 0, 99999999
        else:
            a, b = value.split("-")
            f["min_price"], f["max_price"] = int(a), int(b)

    elif action == "seller":
        # Нормализуем значение
        if value == "owners":
            value = "owner"
        f["seller_type"] = value

    elif action == "done":
        # Сохраняем фильтры
        await set_user_filters_turso(telegram_id, f)
        await callback.message.edit_text(
            "✅ Фильтры сохранены\n\n" + format_filters_summary(f)
        )
        await callback.answer("Сохранено")
        return

    # Сохраняем промежуточные изменения
    await set_user_filters_turso(telegram_id, f)
    await callback.message.edit_text(
        "⚙️ Быстрая настройка фильтров\n\n" + format_filters_summary(f),
        reply_markup=build_kb(telegram_id),
    )
    await callback.answer()
