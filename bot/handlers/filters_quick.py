"""
Упрощенный мастер настройки фильтров - один экран с кнопками
БЕЗ FSM, мгновенное сохранение при каждом действии
"""
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, Message
from database_turso import get_user_filters_turso, set_user_filters_turso, ensure_user_filters
from pydantic import ValidationError
import logging

from bot.utils.ui_helpers import build_keyboard, normalize_city_for_ui

logger = logging.getLogger(__name__)
router = Router()


# Fallback для get_contextual_hint (если функция ещё не реализована)
def get_contextual_hint(key: str) -> str:
    """Заглушка для контекстных подсказок"""
    return ""


def format_filters_summary(f: dict) -> str:
    """Форматирует сводку фильтров для отображения"""
    # Используем единый helper для нормализации города
    city = normalize_city_for_ui(f)
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
        ("📦 Режим", f"filters:{telegram_id}:mode:select"),
        ("✅ Готово", f"filters:{telegram_id}:done"),
    ]
    
    return _build_safe_keyboard(telegram_id, items)


def build_rooms_keyboard(telegram_id: int) -> InlineKeyboardMarkup:
    """Клавиатура для выбора комнат"""
    
    items = [
        ("Студия", f"filters:{telegram_id}:rooms:0"),
        ("1 комн", f"filters:{telegram_id}:rooms:1"),
        ("2 комн", f"filters:{telegram_id}:rooms:2"),
        ("3 комн", f"filters:{telegram_id}:rooms:3"),
        ("3+ комн", f"filters:{telegram_id}:rooms:4+"),
    ]
    
    return build_keyboard(
        items,
        columns=2,
        back_button=("◀️ Назад", f"filters:{telegram_id}:back")
    )


def build_price_keyboard(telegram_id: int) -> InlineKeyboardMarkup:
    """Клавиатура для выбора цены"""
    
    items = [
        ("0–30k", f"filters:{telegram_id}:price:0-30000"),
        ("30–50k", f"filters:{telegram_id}:price:30000-50000"),
        ("50–80k", f"filters:{telegram_id}:price:50000-80000"),
        ("80k+", f"filters:{telegram_id}:price:80000-99999999"),
        ("Любая", f"filters:{telegram_id}:price:any"),
    ]
    
    return build_keyboard(
        items,
        columns=2,
        back_button=("◀️ Назад", f"filters:{telegram_id}:back")
    )


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
    
    # Используем fallback функцию get_contextual_hint (определена в начале файла)
    hint = get_contextual_hint("filters_master")
    
    text = "⚙️ <b>Настройка поиска квартир</b>\n\n" + format_filters_summary(filters) + f"\n\n{hint}"
    keyboard = build_filters_keyboard(telegram_id)
    
    try:
        if isinstance(callback_or_message, CallbackQuery):
            await callback_or_message.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        else:
            await callback_or_message.answer(text, reply_markup=keyboard, parse_mode="HTML")
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


async def show_rooms_selection(callback_or_message, user_id: int):
    """Показывает меню выбора комнат"""
    try:
        text = "🚪 Выберите количество комнат:"
        keyboard = build_rooms_keyboard(user_id)
        if isinstance(callback_or_message, CallbackQuery):
            await callback_or_message.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        else:
            await callback_or_message.answer(text, reply_markup=keyboard, parse_mode="HTML")
    except Exception as e:
        logger.error(f"[FILTER_UI] Failed to show rooms selection user={user_id} error={e}", exc_info=True)
        if isinstance(callback_or_message, CallbackQuery):
            await callback_or_message.answer("Ошибка отображения меню", show_alert=True)
        else:
            await callback_or_message.answer("Ошибка отображения меню")


async def show_price_selection(callback_or_message, user_id: int):
    """Показывает меню выбора цены"""
    try:
        text = "💰 Выберите диапазон цены:"
        keyboard = build_price_keyboard(user_id)
        if isinstance(callback_or_message, CallbackQuery):
            await callback_or_message.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        else:
            await callback_or_message.answer(text, reply_markup=keyboard, parse_mode="HTML")
    except Exception as e:
        logger.error(f"[FILTER_UI] Failed to show price selection user={user_id} error={e}", exc_info=True)
        if isinstance(callback_or_message, CallbackQuery):
            await callback_or_message.answer("Ошибка отображения меню", show_alert=True)
        else:
            await callback_or_message.answer("Ошибка отображения меню")


async def show_seller_selection(callback_or_message, user_id: int):
    """Показывает меню выбора продавца"""
    try:
        text = "👤 Выберите тип продавца:"
        keyboard = build_seller_keyboard(user_id)
        if isinstance(callback_or_message, CallbackQuery):
            await callback_or_message.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        else:
            await callback_or_message.answer(text, reply_markup=keyboard, parse_mode="HTML")
    except Exception as e:
        logger.error(f"[FILTER_UI] Failed to show seller selection user={user_id} error={e}", exc_info=True)
        if isinstance(callback_or_message, CallbackQuery):
            await callback_or_message.answer("Ошибка отображения меню", show_alert=True)
        else:
            await callback_or_message.answer("Ошибка отображения меню")


@router.callback_query(F.data.startswith("filters:"))
async def filters_callback_handler(callback: CallbackQuery):
    """Обработчик callback для быстрой настройки фильтров"""
    # Отвечаем сразу, чтобы предотвратить повторные запросы
    await callback.answer()
    
    try:
        # --- START: robust filters callback handling ---
        # Формат: filters:telegram_id:field:value
        parts = callback.data.split(':')
        
        # Safety: если parts короче ожидаемого — отработать аккуратно
        if len(parts) < 4:
            await callback.answer("Неизвестное действие (некорректные данные).")
            logger.warning(f"[FILTER_QUICK] malformed callback data: {callback.data}")
            return
        
        # Распознаём
        _, user_id_str, field, value = parts[:4]
        
        try:
            user_id = int(user_id_str)
        except (ValueError, TypeError):
            # На всякий случай — лог и прекращаем обработку
            logger.warning(f"[FILTER_QUICK] invalid user id in callback: {user_id_str} data={callback.data}")
            await callback.answer("Ошибка: некорректный идентификатор пользователя.")
            return
        
        # Проверяем, что callback от правильного пользователя
        if callback.from_user.id != user_id:
            logger.warning(f"[FILTER_QUICK] user mismatch: callback_user={callback.from_user.id} expected={user_id}")
            await callback.answer("Это действие доступно только вам.")
            return
        
        # Гарантируем наличие фильтров
        await ensure_user_filters(user_id)
        filters = await get_user_filters_turso(user_id)
        
        if not filters:
            logger.warning(f"[FILTER_QUICK] filters not found for user={user_id}")
            await callback.answer("Фильтры не найдены. Используйте /start для настройки.")
            return
        
        # 1) Handle 'select' control action — открыть меню выбора, не пытаться парсить value
        if value == "select":
            # Вызвать соответствующий экран выбора для поля field
            if field == "rooms":
                await show_rooms_selection(callback, user_id)
                return
            elif field == "price":
                await show_price_selection(callback, user_id)
                return
            elif field == "seller":
                await show_seller_selection(callback, user_id)
                return
            elif field == "mode":
                # Показываем меню выбора режима
                try:
                    await callback.message.edit_text(
                        "📦 Выберите режим доставки:",
                        reply_markup=build_mode_keyboard(user_id),
                        parse_mode="HTML"
                    )
                except Exception as e:
                    logger.error(f"[FILTER_UI] Failed to show mode selection user={user_id} error={e}", exc_info=True)
                    await callback.answer("Ошибка отображения меню", show_alert=True)
                return
            elif field == "city":
                # Запрашиваем город текстом
                hint = get_contextual_hint("city_selection")
                await callback.message.edit_text(
                    f"📍 Введите название города (например: Барановичи):\n\n{hint}",
                    parse_mode="HTML"
                )
                # Устанавливаем флаг awaiting_city для обработки текстового ввода
                filters["awaiting_city"] = 1
                await set_user_filters_turso(user_id, filters)
                logger.info(f"[CITY_INPUT] user={user_id} awaiting_city=True")
                return
            else:
                # Fallback: если нет специальной реализации — просто показать мастер фильтров
                await show_filters_master(callback, user_id)
                return
        
        # 2) Safe parsing for numeric fields / ranges
        try:
            if field == "rooms":
                # value может быть "0".."5" или "studio" (если есть) — пробуем привести к int, иначе логируем
                if value == "any":
                    filters["min_rooms"], filters["max_rooms"] = 0, 99
                elif value == "0":
                    filters["min_rooms"], filters["max_rooms"] = 0, 0
                elif value == "4+":
                    filters["min_rooms"], filters["max_rooms"] = 4, 99
                else:
                    try:
                        rooms = int(value)
                        filters["min_rooms"], filters["max_rooms"] = rooms, rooms
                    except ValueError:
                        # Если значение специфическое, можно обработать отдельно
                        logger.warning(f"[FILTER_QUICK] unexpected rooms value: {value}")
                        await callback.answer("Неверное значение комнат. Попробуйте снова.")
                        return
            
            elif field == "price":
                # value ожидается в виде "min-max". Проверяем заранее.
                if value == "any":
                    filters["min_price"], filters["max_price"] = 0, 99999999
                else:
                    if "-" not in value:
                        logger.warning(f"[FILTER_QUICK] price value missing dash: {value}")
                        await callback.answer("Неверный формат цены. Попробуйте снова.")
                        return
                    a, b = value.split("-", 1)
                    try:
                        min_price = int(a)
                        max_price = int(b)
                        filters["min_price"], filters["max_price"] = min_price, max_price
                    except ValueError:
                        logger.warning(f"[FILTER_QUICK] price parse failed: {value}")
                        await callback.answer("Неверный формат ценового диапазона. Попробуйте снова.")
                        return
            
            elif field == "seller":
                filters["seller_type"] = value if value else "all"
            
            elif field == "mode":
                filters["delivery_mode"] = value if value else "brief"
            
            elif field == "back":
                # Возврат к главному меню
                await show_filters_master(callback, user_id)
                return
            
            elif field == "done":
                # Финальное сохранение
                await set_user_filters_turso(user_id, filters)
                await callback.message.edit_text(
                    "✅ Фильтры сохранены. Я начну искать подходящие квартиры.",
                    parse_mode="HTML"
                )
                return
            
            else:
                logger.warning(f"[FILTER_QUICK] unknown field: {field} value={value}")
                await callback.answer("Неизвестное поле фильтра.")
                return
        
        except Exception as e:
            logger.exception("[FILTER_QUICK] unexpected error while handling filter callback")
            await callback.answer("Ошибка при обработке действия. Попробуйте ещё раз.")
            return
        
        # Мгновенное сохранение при каждом действии
        await set_user_filters_turso(user_id, filters)
        
        # Перерисовываем экран
        await show_filters_master(callback, user_id)
        # --- END: robust filters callback handling ---
        
    except Exception as e:
        logger.exception(f"[FILTER_QUICK] Error handling callback {callback.data}: {e}")
        await callback.answer("Произошла ошибка. Попробуйте ещё раз.", show_alert=True)
