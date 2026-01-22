"""
Упрощенный мастер настройки фильтров - один экран с кнопками
БЕЗ FSM, мгновенное сохранение при каждом действии
"""
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, Message
from database_turso import get_user_filters_turso, set_user_filters_turso, ensure_user_filters
from pydantic import ValidationError
import logging

logger = logging.getLogger(__name__)
router = Router()


def format_filters_summary(f: dict) -> str:
    """Форматирует сводку фильтров для отображения"""
    city_data = f.get('city')
    # Если city - это dict (location), извлекаем имя
    if isinstance(city_data, dict):
        city = city_data.get('name', 'Не выбран')
    else:
        city = city_data or 'Не выбран'
    min_rooms = f.get('min_rooms', 1)
    max_rooms = f.get('max_rooms', 4)
    rooms_text = f"{min_rooms}–{max_rooms}" if min_rooms != max_rooms else str(min_rooms)
    
    seller_text = {
        'all': 'все',
        'owner': 'только собственники',
        'owners': 'только собственники',
        'company': 'только агентства'
    }.get(f.get('seller_type', 'all'), 'все')
    mode_text = 'кратко' if f.get('delivery_mode', 'brief') == 'brief' else 'подробно'
    
    min_price = f.get('min_price', 0)
    max_price = f.get('max_price', 100000)
    price_text = f"${min_price:,} – ${max_price:,}".replace(",", " ")
    
    return (
        f"📍 Город: {city}\n"
        f"🚪 Комнаты: {rooms_text}\n"
        f"💰 Цена: {price_text}\n"
        f"👤 Продавец: {seller_text}\n"
        f"📦 Режим: {mode_text}"
    )


def _build_safe_keyboard(
    telegram_id: int,
    items: list[tuple[str, str]],
    custom_rows: list[list[InlineKeyboardButton]] | None = None
) -> InlineKeyboardMarkup:
    """
    Безопасное построение клавиатуры с fallback при ошибках валидации.
    
    Args:
        telegram_id: ID пользователя
        items: список (text, callback_data) - используется если custom_rows=None
        custom_rows: кастомная структура строк (опционально)
    
    Returns:
        InlineKeyboardMarkup с защитой от ошибок
    """
    rows: list[list[InlineKeyboardButton]] = []
    
    if custom_rows:
        rows = custom_rows
    else:
        # Формируем строки из items (каждая кнопка в отдельной строке)
        for text, cb in items:
            rows.append([
                InlineKeyboardButton(
                    text=str(text),
                    callback_data=str(cb)
                )
            ])
    
    try:
        keyboard = InlineKeyboardMarkup(inline_keyboard=rows)
    except ValidationError as e:
        logger.error(
            "[FILTER_UI] Keyboard validation error. user=%s rows=%s error=%s",
            telegram_id,
            rows,
            e,
            exc_info=True
        )
        # 🔥 FALLBACK — никогда не падаем
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⚙️ Настроить фильтры",
                        callback_data=f"setup_filters:{telegram_id}"
                    )
                ]
            ]
        )
    
    logger.debug(
        "[FILTER_UI] Keyboard built user=%s rows=%d",
        telegram_id,
        len(rows)
    )
    
    return keyboard


def build_filters_keyboard(telegram_id: int) -> InlineKeyboardMarkup:
    """Строит клавиатуру для быстрой настройки фильтров"""
    items = [
        ("📍 Город", f"filters:{telegram_id}:city:select"),
        ("🚪 Комнаты", f"filters:{telegram_id}:rooms:select"),
        ("💰 Цена", f"filters:{telegram_id}:price:select"),
        ("👤 Продавец", f"filters:{telegram_id}:seller:select"),
        ("📦 Режим доставки", f"filters:{telegram_id}:mode:select"),
        ("✅ Готово", f"filters:{telegram_id}:done"),
    ]
    
    return _build_safe_keyboard(telegram_id, items)


def build_rooms_keyboard(telegram_id: int) -> InlineKeyboardMarkup:
    """Клавиатура для выбора комнат"""
    items = [
        ("1", f"filters:{telegram_id}:rooms:1"),
        ("2", f"filters:{telegram_id}:rooms:2"),
        ("3", f"filters:{telegram_id}:rooms:3"),
        ("4+", f"filters:{telegram_id}:rooms:4+"),
        ("Любые", f"filters:{telegram_id}:rooms:any"),
        ("◀️ Назад", f"filters:{telegram_id}:back"),
    ]
    
    # Первые 3 кнопки в одну строку, остальные по одной
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text=str(items[0][0]), callback_data=str(items[0][1])),
            InlineKeyboardButton(text=str(items[1][0]), callback_data=str(items[1][1])),
            InlineKeyboardButton(text=str(items[2][0]), callback_data=str(items[2][1])),
        ],
        [InlineKeyboardButton(text=str(items[3][0]), callback_data=str(items[3][1]))],
        [InlineKeyboardButton(text=str(items[4][0]), callback_data=str(items[4][1]))],
        [InlineKeyboardButton(text=str(items[5][0]), callback_data=str(items[5][1]))],
    ]
    
    return _build_safe_keyboard(telegram_id, items, custom_rows=rows)


def build_price_keyboard(telegram_id: int) -> InlineKeyboardMarkup:
    """Клавиатура для выбора цены"""
    items = [
        ("0–30k", f"filters:{telegram_id}:price:0-30000"),
        ("30–50k", f"filters:{telegram_id}:price:30000-50000"),
        ("50–80k", f"filters:{telegram_id}:price:50000-80000"),
        ("80k+", f"filters:{telegram_id}:price:80000-99999999"),
        ("Любая", f"filters:{telegram_id}:price:any"),
        ("◀️ Назад", f"filters:{telegram_id}:back"),
    ]
    
    return _build_safe_keyboard(telegram_id, items)


def build_seller_keyboard(telegram_id: int) -> InlineKeyboardMarkup:
    """Клавиатура для выбора продавца"""
    items = [
        ("Все", f"filters:{telegram_id}:seller:all"),
        ("Только собственники", f"filters:{telegram_id}:seller:owner"),
        ("◀️ Назад", f"filters:{telegram_id}:back"),
    ]
    
    return _build_safe_keyboard(telegram_id, items)


def build_mode_keyboard(telegram_id: int) -> InlineKeyboardMarkup:
    """Клавиатура для выбора режима доставки"""
    items = [
        ("🔹 Кратко", f"filters:{telegram_id}:mode:brief"),
        ("🔹 Полностью", f"filters:{telegram_id}:mode:full"),
        ("◀️ Назад", f"filters:{telegram_id}:back"),
    ]
    
    return _build_safe_keyboard(telegram_id, items)


async def show_filters_master(callback_or_message, telegram_id: int):
    """Показывает мастер фильтров с текущими значениями"""
    logger.debug(
        "[FILTER_UI] show_filters_master user=%s",
        telegram_id
    )
    
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
    
    logger.debug(
        "[FILTER_UI] filters items=%s",
        filters
    )
    
    text = "⚙️ Настройка поиска квартир\n\n" + format_filters_summary(filters)
    keyboard = build_filters_keyboard(telegram_id)
    
    try:
        if isinstance(callback_or_message, CallbackQuery):
            await callback_or_message.message.edit_text(text, reply_markup=keyboard)
        else:
            await callback_or_message.answer(text, reply_markup=keyboard)
    except Exception as e:
        logger.error(
            "[FILTER_UI][SEND] Failed to send filters keyboard user=%s error=%s",
            telegram_id,
            e,
            exc_info=True
        )
        # Fallback сообщение
        fallback_text = (
            "⚠️ Не удалось показать настройки.\n"
            "Нажмите /start или используйте кнопку «⚙️ Настроить фильтры»."
        )
        try:
            if isinstance(callback_or_message, CallbackQuery):
                await callback_or_message.message.answer(fallback_text)
            else:
                await callback_or_message.answer(fallback_text)
        except Exception as fallback_error:
            logger.error(
                "[FILTER_UI][SEND] Fallback also failed user=%s error=%s",
                telegram_id,
                fallback_error,
                exc_info=True
            )


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
        
        elif action == "back":
            # Возврат к главному меню
            await show_filters_master(callback, telegram_id)
            await callback.answer()
            return
        
        elif action == "rooms" and value == "select":
            # Показываем меню выбора комнат
            try:
                await callback.message.edit_text(
                    "🚪 Выберите количество комнат:",
                    reply_markup=build_rooms_keyboard(telegram_id)
                )
            except Exception as e:
                logger.error(
                    "[FILTER_UI][SEND] Failed to send rooms keyboard user=%s error=%s",
                    telegram_id,
                    e,
                    exc_info=True
                )
                await callback.answer("Ошибка отображения меню", show_alert=True)
            await callback.answer()
            return
        
        elif action == "price" and value == "select":
            # Показываем меню выбора цены
            try:
                await callback.message.edit_text(
                    "💰 Выберите диапазон цены:",
                    reply_markup=build_price_keyboard(telegram_id)
                )
            except Exception as e:
                logger.error(
                    "[FILTER_UI][SEND] Failed to send price keyboard user=%s error=%s",
                    telegram_id,
                    e,
                    exc_info=True
                )
                await callback.answer("Ошибка отображения меню", show_alert=True)
            await callback.answer()
            return
        
        elif action == "seller" and value == "select":
            # Показываем меню выбора продавца
            try:
                await callback.message.edit_text(
                    "👤 Выберите тип продавца:",
                    reply_markup=build_seller_keyboard(telegram_id)
                )
            except Exception as e:
                logger.error(
                    "[FILTER_UI][SEND] Failed to send seller keyboard user=%s error=%s",
                    telegram_id,
                    e,
                    exc_info=True
                )
                await callback.answer("Ошибка отображения меню", show_alert=True)
            await callback.answer()
            return
        
        elif action == "mode" and value == "select":
            # Показываем меню выбора режима
            try:
                await callback.message.edit_text(
                    "📦 Выберите режим доставки:",
                    reply_markup=build_mode_keyboard(telegram_id)
                )
            except Exception as e:
                logger.error(
                    "[FILTER_UI][SEND] Failed to send mode keyboard user=%s error=%s",
                    telegram_id,
                    e,
                    exc_info=True
                )
                await callback.answer("Ошибка отображения меню", show_alert=True)
            await callback.answer()
            return
        
        elif action == "city" and value == "select":
            # Запрашиваем город текстом
            await callback.message.edit_text(
                "📍 Введите название города (например: Барановичи):\n\n"
                "Или используйте /start для выбора из списка."
            )
            await callback.answer()
            # Устанавливаем флаг awaiting_city для обработки текстового ввода
            filters["awaiting_city"] = 1
            await set_user_filters_turso(telegram_id, filters)
            logger.info(f"[CITY_INPUT] user={telegram_id} awaiting_city=True")
            return
        
        elif action == "done":
            # Финальное сохранение
            await set_user_filters_turso(telegram_id, filters)
            await callback.message.edit_text(
                "✅ Фильтры сохранены. Я начну искать подходящие квартиры."
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
