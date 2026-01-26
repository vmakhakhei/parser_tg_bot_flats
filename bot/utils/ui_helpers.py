"""
Вспомогательные функции для UI
"""
from typing import Optional
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardMarkup


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
            *[builder.button(text=t, callback_data=c) for t, c in nav_buttons]
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
