"""
Вспомогательные функции для UI
"""
from typing import List, Tuple, Optional
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def _dedupe_items(items: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """
    Удаляет дубликаты кнопок по callback_data.
    
    Args:
        items: Список кортежей (text, callback_data)
    
    Returns:
        Список без дубликатов (сохраняется порядок первого вхождения)
    """
    seen = set()
    out = []
    for text, cb in items:
        if cb in seen:
            continue
        seen.add(cb)
        out.append((text, cb))
    return out


def build_keyboard(
    items: List[Tuple[str, str]],
    columns: int = 1,
    back_button: Optional[Tuple[str, str]] = None
) -> InlineKeyboardMarkup:
    """
    Безопасное построение клавиатуры с дедупликацией и группировкой кнопок.
    
    Args:
        items: Список кортежей (text, callback_data)
        columns: Количество кнопок в строке (по умолчанию 1)
        back_button: Опциональная кнопка "Назад" в формате (text, callback_data)
    
    Returns:
        InlineKeyboardMarkup с кнопками, сгруппированными по columns
    """
    # Удаляем дубликаты
    items = _dedupe_items(items)
    
    # Строим клавиатуру используя InlineKeyboardBuilder для правильной группировки
    builder = InlineKeyboardBuilder()
    for text, cb in items:
        builder.button(text=text, callback_data=cb)
    
    # Группируем кнопки по columns в строку
    builder.adjust(columns)
    
    # Добавляем кнопку "Назад" если указана (в отдельной строке)
    if back_button:
        builder.button(text=back_button[0], callback_data=back_button[1])
        builder.adjust(1)  # Кнопка "Назад" всегда в отдельной строке
    
    return builder.as_markup()


def normalize_city_for_ui(filters: dict) -> str:
    """
    Нормализует название города из фильтров для отображения в UI.
    
    Args:
        filters: Словарь фильтров пользователя
    
    Returns:
        Строка с названием города для отображения
    """
    import logging
    logger = logging.getLogger(__name__)
    
    # Приоритет 1: city_display (явное поле для отображения)
    city_display = filters.get("city_display")
    if city_display:
        logger.debug(f"[CITY_UI_RENDER] city_display={city_display}")
        return str(city_display)
    
    # Приоритет 2: city как dict (location object)
    city_data = filters.get("city")
    if isinstance(city_data, dict):
        display = city_data.get("display") or city_data.get("name") or city_data.get("label_ru")
        if display:
            logger.debug(f"[CITY_UI_RENDER] city_display={display} (from dict)")
            return str(display)
        logger.debug(f"[CITY_UI_RENDER] city_display=Не выбран (dict without display)")
        return "Не выбран"
    
    # Приоритет 3: city как строка
    if city_data and isinstance(city_data, str):
        logger.debug(f"[CITY_UI_RENDER] city_display={city_data}")
        return city_data
    
    # Нет города
    logger.debug(f"[CITY_UI_RENDER] city_display=Не выбран (no city)")
    return "Не выбран"


def get_contextual_hint(screen_name: str) -> str:
    """
    Возвращает контекстную подсказку для экрана.
    
    Args:
        screen_name: Имя экрана (main_menu, filters_master, city_selection, actions_menu)
    
    Returns:
        Строка с подсказкой
    """
    hints = {
        "main_menu": "💡 Бот проверяет объявления каждые 12 часов автоматически",
        "filters_master": "💡 Настройте параметры поиска. Можно изменить в любой момент.",
        "city_selection": "💡 Выберите город или введите название вручную",
        "actions_menu": "💡 Выберите действие или подождите следующей автоматической проверки",
        "more_menu": "💡 Дополнительные функции и информация",
    }
    return hints.get(screen_name, "")


def build_paginated_keyboard(
    items: list[tuple[str, str]],
    page: int = 0,
    per_page: int = 5,
    callback_prefix: str = "item",
    back_callback: Optional[str] = None
) -> InlineKeyboardMarkup:
    """
    Строит клавиатуру с пагинацией.
    
    Args:
        items: Список кортежей (text, callback_value)
        page: Номер страницы (начиная с 0)
        per_page: Количество элементов на странице
        callback_prefix: Префикс для callback_data
        back_callback: Callback для кнопки "Назад" (если None, не добавляется)
    
    Returns:
        InlineKeyboardMarkup с пагинацией
    """
    builder = InlineKeyboardBuilder()
    
    start = page * per_page
    end = start + per_page
    page_items = items[start:end]
    
    # Добавляем элементы текущей страницы
    for text, value in page_items:
        builder.button(
            text=text,
            callback_data=f"{callback_prefix}:{value}:page:{page}"
        )
    
    # Навигация
    nav_buttons = []
    if page > 0:
        nav_buttons.append(("◀️ Назад", f"{callback_prefix}_page:{page-1}"))
    if end < len(items):
        nav_buttons.append(("▶️ Далее", f"{callback_prefix}_page:{page+1}"))
    
    # Добавляем навигацию в одну строку, если есть обе кнопки
    if len(nav_buttons) == 2:
        builder.row(
            *[InlineKeyboardButton(text=t, callback_data=c) for t, c in nav_buttons]
        )
    elif len(nav_buttons) == 1:
        builder.button(text=nav_buttons[0][0], callback_data=nav_buttons[0][1])
    
    # Кнопка "Назад" к предыдущему экрану
    if back_callback:
        builder.button(text="◀️ Назад", callback_data=back_callback)
    
    builder.adjust(1)
    return builder.as_markup()


def build_more_menu_keyboard(telegram_id: int) -> InlineKeyboardMarkup:
    """
    Строит клавиатуру меню "Ещё" с редко используемыми функциями.
    
    Args:
        telegram_id: ID пользователя
    
    Returns:
        InlineKeyboardMarkup с меню "Ещё"
    """
    builder = InlineKeyboardBuilder()
    
    builder.button(text="📊 Статистика", callback_data="show_stats")
    builder.button(text="📖 Как работает бот", callback_data="explain_scoring")
    builder.button(text="🔄 Сбросить фильтры", callback_data="reset_filters_confirm")
    builder.button(text="📡 Источники", callback_data="show_sources")
    builder.button(text="◀️ Назад", callback_data="back_to_main")
    
    builder.adjust(1)
    return builder.as_markup()


def build_confirmation_keyboard(
    action: str,
    confirm_callback: str,
    cancel_callback: str
) -> InlineKeyboardMarkup:
    """
    Строит клавиатуру подтверждения действия.
    
    Args:
        action: Описание действия для отображения
        confirm_callback: Callback для подтверждения
        cancel_callback: Callback для отмены
    
    Returns:
        InlineKeyboardMarkup с кнопками подтверждения
    """
    builder = InlineKeyboardBuilder()
    
    builder.button(text="✅ Да", callback_data=confirm_callback)
    builder.button(text="❌ Нет", callback_data=cancel_callback)
    
    builder.adjust(2)
    return builder.as_markup()
