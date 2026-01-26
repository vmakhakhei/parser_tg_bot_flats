"""
Обработчики команды /start и настройки фильтров
"""

from typing import Optional
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.enums import ParseMode
from aiogram.utils.keyboard import InlineKeyboardBuilder
from error_logger import log_info

from database import get_user_filters, set_user_filters
from bot.services.search_service import check_new_listings
from bot.services.ai_service import check_new_listings_ai_mode
from datetime import datetime
from constants.constants import DELIVERY_MODE_BRIEF, DELIVERY_MODE_FULL, DELIVERY_MODE_DEFAULT
from bot.services.notification_service import USER_DELIVERY_MODES

router = Router()


# FSM состояния для ввода цены
class PriceStates(StatesGroup):
    waiting_for_min_price = State()
    waiting_for_max_price = State()


# FSM состояния для ввода города
class CityStates(StatesGroup):
    waiting_for_city = State()


# FSM состояния для пошаговой настройки фильтров
class SetupStates(StatesGroup):
    waiting_for_city = State()
    waiting_for_rooms = State()
    waiting_for_price_min = State()
    waiting_for_price_max = State()
    waiting_for_seller = State()
    waiting_for_mode = State()


# Список областных центров и крупных городов Беларуси
BELARUS_CITIES = [
    # Областные центры
    ("Минск", "минск"),
    ("Гомель", "гомель"),
    ("Могилёв", "могилёв"),
    ("Витебск", "витебск"),
    ("Гродно", "гродно"),
    ("Брест", "брест"),
    # Крупные города
    ("Барановичи", "барановичи"),
    ("Бобруйск", "бобруйск"),
    ("Пинск", "пинск"),
    ("Орша", "орша"),
    ("Мозырь", "мозырь"),
    ("Солигорск", "солигорск"),
    ("Новополоцк", "новополоцк"),
    ("Лида", "лида"),
    ("Полоцк", "полоцк"),
    ("Молодечно", "молодечно"),
    ("Борисов", "борисов"),
    ("Жлобин", "жлобин"),
    ("Слуцк", "слуцк"),
    ("Кобрин", "кобрин"),
]


def normalize_city_name(city: str) -> str:
    """Нормализует название города для сравнения"""
    return city.lower().strip().replace("ё", "е").replace("й", "и")


def validate_city(city: str) -> tuple[bool, Optional[str]]:
    """
    Валидирует название города.
    Возвращает (is_valid, normalized_city_name)
    """
    normalized = normalize_city_name(city)

    # Проверяем точное совпадение
    for display_name, normalized_name in BELARUS_CITIES:
        if normalized == normalized_name:
            return True, normalized_name

    # Проверяем частичное совпадение (для опечаток)
    for display_name, normalized_name in BELARUS_CITIES:
        if normalized_name.startswith(normalized) or normalized.startswith(normalized_name):
            if len(normalized) >= 3:  # Минимум 3 символа для частичного совпадения
                return True, normalized_name

    # Если не найдено, разрешаем ввод вручную (но предупреждаем)
    if len(normalized) >= 2:  # Минимум 2 символа
        return True, normalized

    return False, None


def normalize_city_for_ui(filters: dict) -> str:
    """
    Единый helper для нормализации города для UI.
    
    Обрабатывает разные форматы:
    - city_display (строка) → возвращает строку
    - city как dict → извлекает display/name
    - city как строка → возвращает строку
    - нет города → "Не выбран"
    
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


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    """Обработчик команды /start - пошаговая настройка фильтров"""
    import logging
    logger = logging.getLogger(__name__)
    
    user_id = message.from_user.id

    # КРИТИЧНО: Активируем пользователя ДО любого await send_message(...)
    # Это гарантирует, что пользователь будет виден в get_active_users()
    try:
        from database_turso import activate_user
        
        success = await activate_user(
            telegram_id=user_id,
            is_active=True
        )
        
        if success:
            logger.info(
                "[user] activated user telegram_id=%s",
                user_id
            )
        else:
            logger.warning(f"[user] failed to activate user telegram_id={user_id}")
    except Exception as e:
        logger.warning(f"[user] failed to activate user telegram_id={user_id}: {e}")
        # Продолжаем работу даже если не удалось активировать пользователя
    
    # Также обновляем username пользователя (для совместимости)
    try:
        from database_turso import upsert_user
        await upsert_user(
            telegram_id=user_id,
            username=message.from_user.username,
            is_active=True
        )
    except Exception as e:
        logger.warning(f"Не удалось обновить username пользователя: {e}")

    # ЧАСТЬ C — START → QUICK MASTER
    # Гарантируем наличие фильтров
    from database_turso import ensure_user_filters, get_user_filters_turso
    await ensure_user_filters(telegram_id=user_id)
    
    # Получаем фильтры из Turso
    user_filters = await get_user_filters_turso(user_id)
    
    # Проверяем наличие города (может быть dict или строка)
    city_data = user_filters.get("city") if user_filters else None
    has_city = city_data and (
        (isinstance(city_data, str) and city_data.strip()) or 
        (isinstance(city_data, dict) and (city_data.get("name") or city_data.get("display") or city_data.get("label_ru")))
    )
    
    if not user_filters or not has_city:
        # Первый запуск или город не установлен - запрашиваем город
        await message.answer(
            "ℹ️ Чтобы начать поиск, нужно один раз настроить фильтры.\nЭто займет меньше минуты 👇",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardBuilder().button(
                text="⚙️ Настроить фильтры",
                callback_data="setup_filters"
            ).as_markup()
        )
        # Устанавливаем состояние для ввода города
        await state.set_state(CityStates.waiting_for_city)
    else:
        # Фильтры уже установлены - показываем упрощенное приветствие
        status = "✅ Активен" if user_filters.get("is_active") else "❌ Отключен"

        builder = InlineKeyboardBuilder()
        builder.button(text="🔍 Поиск", callback_data="check_now")
        builder.button(text="⚙️ Настройки", callback_data="setup_filters")
        builder.button(text="Ещё", callback_data="show_more_menu")

        # Принудительно размещаем по 1 кнопке в ряду
        builder.adjust(1)

        # Безопасное форматирование цен
        def fmt_price(v):
            return f"${int(v):,}".replace(",", " ") if v is not None else "—"
        
        min_price = user_filters.get('min_price')
        max_price = user_filters.get('max_price')
        price_from = fmt_price(min_price)
        price_to = fmt_price(max_price)
        
        city_name = normalize_city_for_ui(user_filters)
        
        # Формируем текст фильтров (только ключевые)
        min_rooms = user_filters.get('min_rooms', 1)
        max_rooms = user_filters.get('max_rooms', 4)
        rooms_text = f"{min_rooms}–{max_rooms}" if min_rooms != max_rooms else str(min_rooms)
        
        # Контекстная подсказка
        from bot.utils.ui_helpers import get_contextual_hint
        hint = get_contextual_hint("main_menu")
        
        await message.answer(
            "👋 Привет! Я KeyFlat — умный бот для поиска квартир.\n\n"
            f"📍 <b>Город:</b> {city_name}\n"
            f"🚪 <b>Комнаты:</b> {rooms_text}\n"
            f"💰 <b>Цена:</b> {price_from} – {price_to}\n"
            f"📡 <b>Статус:</b> {status}\n\n"
            f"{hint}",
            parse_mode=ParseMode.HTML,
            reply_markup=builder.as_markup(),
        )


async def show_city_selection_menu(message: Message, state: FSMContext, page: int = 0):
    """Показывает меню выбора города для пошаговой настройки с пагинацией"""
    import logging
    logger = logging.getLogger(__name__)
    
    from bot.utils.ui_helpers import build_paginated_keyboard, get_contextual_hint
    
    # Подготавливаем список городов для пагинации
    cities_items = [(display_name, normalized_name) for display_name, normalized_name in BELARUS_CITIES]
    
    per_page = 5
    start = page * per_page
    end = start + per_page
    
    # Если городов меньше или равно per_page, показываем все без пагинации
    if len(cities_items) <= per_page:
        builder = InlineKeyboardBuilder()
        for display_name, normalized_name in cities_items:
            builder.button(
                text=display_name,
                callback_data=f"setup_city_{normalized_name}"
            )
        builder.button(text="✏️ Ввести вручную", callback_data="setup_city_manual")
        builder.adjust(1)
        keyboard = builder.as_markup()
    else:
        # Используем пагинацию
        page_cities = cities_items[start:end]
        
        builder = InlineKeyboardBuilder()
        for display_name, normalized_name in page_cities:
            builder.button(
                text=display_name,
                callback_data=f"setup_city_{normalized_name}"
            )
        
        # Навигация
        nav_row = []
        if page > 0:
            nav_row.append(("◀️ Назад", f"city_page:{page-1}"))
        if end < len(cities_items):
            nav_row.append(("▶️ Далее", f"city_page:{page+1}"))
        
        if nav_row:
            builder.row(*[builder.button(text=t, callback_data=c) for t, c in nav_row])
        
        builder.button(text="✏️ Ввести вручную", callback_data="setup_city_manual")
        builder.adjust(1)
        keyboard = builder.as_markup()
    
    logger.debug(f"[CITY_KEYBOARD] Created city selection keyboard page={page} rows={len(keyboard.inline_keyboard)}")
    
    hint = get_contextual_hint("city_selection")
    
    await message.answer(
        "📍 <b>Шаг 1 из 4: Выберите город</b>\n\n"
        f"{hint}",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )
    await state.set_state(SetupStates.waiting_for_city)


@router.callback_query(F.data.startswith("city_page:"))
async def cb_city_page(callback: CallbackQuery, state: FSMContext):
    """Обработчик навигации по страницам выбора города"""
    await callback.answer()
    
    try:
        page = int(callback.data.split(":")[1])
        await callback.message.delete()
        await show_city_selection_menu(callback.message, state, page=page)
    except (ValueError, IndexError):
        await callback.answer("Ошибка навигации", show_alert=True)


@router.callback_query(F.data == "setup_city_manual")
async def cb_setup_city_manual(callback: CallbackQuery, state: FSMContext):
    """Обработчик кнопки 'Ввести вручную' для города"""
    await callback.answer()
    await callback.message.answer(
        "✏️ Введите название города (например: Барановичи, Полоцк, Орша):"
    )
    await state.set_state(CityStates.waiting_for_city)


@router.callback_query(F.data.startswith("setup_city_"))
async def cb_setup_city(callback: CallbackQuery, state: FSMContext):
    """Обработчик выбора города из предустановленного списка"""
    # Отвечаем сразу, чтобы предотвратить повторные запросы
    await callback.answer()
    
    from database_turso import get_user_filters_turso, set_user_filters_turso
    from bot.handlers.filters_quick import show_filters_master
    from bot.utils.city_lookup import find_city_slug_by_text
    
    city_name = callback.data.replace("setup_city_", "")
    
    # Ищем через lookup
    results = await find_city_slug_by_text(city_name, limit=1)
    
    if results:
        city_result = results[0]
        slug = city_result['slug']
        label_ru = city_result['label_ru']
        
        # Сохраняем
        user_id = callback.from_user.id
        filters = await get_user_filters_turso(user_id) or {}
        filters["city"] = label_ru.lower()
        filters["city_slug"] = slug
        filters["city_display"] = label_ru
        await set_user_filters_turso(user_id, filters)
        
        # Уже ответили в начале функции
        await callback.message.answer(
            f"✅ Выбран: {label_ru}\n\n"
            "🚪 <b>Шаг 2 из 4: Количество комнат</b>\n\n"
            "Введите количество комнат (например: 2 или 2-3):",
            parse_mode=ParseMode.HTML
        )
        await state.set_state(SetupStates.waiting_for_rooms)
    else:
        # Если город не найден, отправляем alert (это не повторный запрос)
        await callback.answer("Город не найден в базе", show_alert=True)


@router.callback_query(F.data == "check_now")
async def cb_check_now(callback: CallbackQuery):
    """Обработчик кнопки 'Проверить сейчас'"""
    await callback.answer("Проверяю объявления...")
    await callback.message.answer(
        "🔍 Проверяю новые объявления со всех источников...\nЭто может занять 30-60 секунд."
    )
    await check_new_listings(callback.message.bot)
    await callback.message.answer("✅ Проверка завершена!")


@router.callback_query(F.data == "check_now_ai")
async def cb_check_now_ai(callback: CallbackQuery):
    """Обработчик кнопки 'ИИ-анализ'"""
    from bot.services.search_service import fetch_listings_for_user

    user_id = callback.from_user.id
    await callback.answer("Запускаю ИИ-анализ...")

    user_filters = await get_user_filters(user_id)
    if not user_filters:
        await callback.message.answer("⚠️ Фильтры не настроены. Используйте /start для настройки.")
        return

    # Получаем объявления
    all_listings = await fetch_listings_for_user(user_id, user_filters)

    # Запускаем ИИ-режим
    await check_new_listings_ai_mode(callback.message.bot, user_id, user_filters, all_listings)


@router.callback_query(F.data == "setup_filters")
async def cb_setup_filters(callback: CallbackQuery, state: FSMContext):
    """Настройка фильтров для пользователя"""
    from bot.handlers.filters_quick import show_filters_master
    
    await callback.answer("Настройка фильтров...")
    user_id = callback.from_user.id
    
    # Проверяем, есть ли город
    from database_turso import get_user_filters_turso
    filters = await get_user_filters_turso(user_id)
    
    city_data = filters.get("city") if filters else None
    has_city = city_data and (
        (isinstance(city_data, str) and city_data.strip()) or 
        (isinstance(city_data, dict) and (city_data.get("name") or city_data.get("display") or city_data.get("label_ru")))
    )
    
    if not filters or not has_city:
        # Нет города - запрашиваем
        await callback.message.answer(
            "✏️ Введите город (например: Барановичи)",
            parse_mode=ParseMode.HTML,
        )
        await state.set_state(CityStates.waiting_for_city)
    else:
        # Город есть - открываем quick master
        try:
            await show_filters_master(callback, user_id)
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(
                "[FILTER_UI][START] Failed to show filters master in setup user=%s error=%s",
                user_id,
                e,
                exc_info=True
            )
            # Отправляем alert только при ошибке (это не повторный запрос)
            await callback.answer("Ошибка открытия настроек", show_alert=True)


@router.callback_query(F.data == "show_stats")
async def cb_show_stats(callback: CallbackQuery):
    """Показывает статистику пользователя"""
    await callback.answer("Статистика пока не реализована")
    # Возвращаемся в меню "Ещё"
    from bot.utils.ui_helpers import build_more_menu_keyboard, get_contextual_hint
    await callback.message.edit_text(
        "📊 <b>Статистика</b>\n\n"
        "Функция статистики находится в разработке.\n\n"
        f"{get_contextual_hint('more_menu')}",
        parse_mode=ParseMode.HTML,
        reply_markup=build_more_menu_keyboard(callback.from_user.id)
    )


@router.callback_query(F.data == "show_more_menu")
async def cb_show_more_menu(callback: CallbackQuery):
    """Показывает меню 'Ещё' с дополнительными функциями"""
    await callback.answer()
    from bot.utils.ui_helpers import build_more_menu_keyboard, get_contextual_hint
    
    await callback.message.edit_text(
        "📋 <b>Дополнительные функции</b>\n\n"
        f"{get_contextual_hint('more_menu')}",
        parse_mode=ParseMode.HTML,
        reply_markup=build_more_menu_keyboard(callback.from_user.id)
    )


@router.callback_query(F.data == "back_to_main")
async def cb_back_to_main(callback: CallbackQuery):
    """Возвращает пользователя в главное меню"""
    await callback.answer()
    
    from database_turso import get_user_filters_turso
    from bot.utils.ui_helpers import get_contextual_hint
    
    user_id = callback.from_user.id
    user_filters = await get_user_filters_turso(user_id) or {}
    
    status = "✅ Активен" if user_filters.get("is_active") else "❌ Отключен"
    
    builder = InlineKeyboardBuilder()
    builder.button(text="🔍 Поиск", callback_data="check_now")
    builder.button(text="⚙️ Настройки", callback_data="setup_filters")
    builder.button(text="Ещё", callback_data="show_more_menu")
    builder.adjust(1)
    
    def fmt_price(v):
        return f"${int(v):,}".replace(",", " ") if v is not None else "—"
    
    min_price = user_filters.get('min_price')
    max_price = user_filters.get('max_price')
    price_from = fmt_price(min_price)
    price_to = fmt_price(max_price)
    
    city_name = normalize_city_for_ui(user_filters)
    
    min_rooms = user_filters.get('min_rooms', 1)
    max_rooms = user_filters.get('max_rooms', 4)
    rooms_text = f"{min_rooms}–{max_rooms}" if min_rooms != max_rooms else str(min_rooms)
    
    hint = get_contextual_hint("main_menu")
    
    await callback.message.edit_text(
        "👋 Привет! Я KeyFlat — умный бот для поиска квартир.\n\n"
        f"📍 <b>Город:</b> {city_name}\n"
        f"🚪 <b>Комнаты:</b> {rooms_text}\n"
        f"💰 <b>Цена:</b> {price_from} – {price_to}\n"
        f"📡 <b>Статус:</b> {status}\n\n"
        f"{hint}",
        parse_mode=ParseMode.HTML,
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data == "show_sources")
async def cb_show_sources(callback: CallbackQuery):
    """Показывает список источников объявлений"""
    await callback.answer()
    
    from config import DEFAULT_SOURCES
    from bot.utils.ui_helpers import build_more_menu_keyboard, get_contextual_hint
    
    sources = [
        ("Kufar.by", "kufar", "крупнейшая доска объявлений Беларуси"),
        ("Etagi.com", "etagi", "агентство недвижимости"),
        ("Realt.by", "realt", "портал недвижимости (SPA - не поддерживается)"),
        ("Domovita.by", "domovita", "недвижимость (нет данных для Барановичей)"),
        ("Onliner.by", "onliner", "популярный портал (не поддерживается)"),
        ("GoHome.by", "gohome", "недвижимость (сервер недоступен)"),
    ]
    
    lines = ["📡 <b>Источники объявлений:</b>", ""]
    
    for name, key, desc in sources:
        if key in DEFAULT_SOURCES:
            lines.append(f"✅ <b>{name}</b> — {desc}")
        else:
            lines.append(f"❌ <s>{name}</s> — {desc}")
    
    lines.append("")
    lines.append(f"📊 <b>Активных источников:</b> {len(DEFAULT_SOURCES)}")
    lines.append("🔄 Проверка каждые 12 часов")
    lines.append("")
    lines.append(get_contextual_hint("more_menu"))
    
    await callback.message.edit_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=build_more_menu_keyboard(callback.from_user.id)
    )


@router.callback_query(F.data == "reset_filters_confirm")
async def cb_reset_filters_confirm(callback: CallbackQuery):
    """Показывает подтверждение сброса фильтров"""
    await callback.answer()
    
    from bot.utils.ui_helpers import build_confirmation_keyboard
    
    await callback.message.edit_text(
        "⚠️ <b>Сброс фильтров</b>\n\n"
        "Вы уверены, что хотите сбросить все фильтры?\n"
        "Это действие нельзя отменить. Вам придется настроить фильтры заново.",
        parse_mode=ParseMode.HTML,
        reply_markup=build_confirmation_keyboard(
            action="reset_filters",
            confirm_callback="reset_filters",
            cancel_callback="show_more_menu"
        )
    )


@router.callback_query(F.data == "reset_filters")
async def cb_reset_filters(callback: CallbackQuery, state: FSMContext):
    """Сбрасывает фильтры и начинает настройку заново"""
    await callback.answer("Сбрасываю фильтры...")
    
    user_id = callback.from_user.id
    
    # Сбрасываем фильтры в Turso
    from database_turso import set_user_filters_turso
    await set_user_filters_turso(user_id, {
        "city": None,
        "min_rooms": 1,
        "max_rooms": 4,
        "min_price": 0,
        "max_price": 100000,
        "seller_type": "all",
        "delivery_mode": "brief",
        "is_active": True,
    })
    
    # Очищаем состояние FSM
    await state.clear()
    
    # Начинаем настройку заново
    await callback.message.edit_text(
        "🔄 <b>Фильтры сброшены</b>\n\n"
        "Начинаем настройку заново...",
        parse_mode=ParseMode.HTML
    )
    
    # Показываем меню выбора города
    await show_city_selection_menu(callback.message, state)


@router.callback_query(F.data == "explain_scoring")
async def cb_explain_scoring(callback: CallbackQuery):
    """Объясняет, как бот выбирает лучшие квартиры"""
    await callback.answer()
    await callback.message.answer(
        "📊 <b>Как я выбираю лучшие квартиры:</b>\n\n"
        "Я использую умный алгоритм, который учитывает:\n\n"
        "• <b>Цену за м²</b> — чем ниже, тем лучше\n"
        "• <b>Отклонение от рынка</b> — насколько выгоднее среднего\n"
        "• <b>Стабильность цен</b> — небольшой разброс в доме\n"
        "• <b>Количество вариантов</b> — больше выбор\n\n"
        "Дома с лучшими показателями показываются первыми в summary.",
        parse_mode=ParseMode.HTML
    )


@router.callback_query(F.data.startswith("explain_house|"))
async def cb_explain_house(callback: CallbackQuery):
    """Объясняет, почему этот дом в подборке"""
    # Отвечаем сразу, чтобы предотвратить повторные запросы
    await callback.answer()
    
    from bot.services.notification_service import get_listings_for_house_hash
    from utils.scoring import calc_price_per_m2, calc_market_median_ppm
    from statistics import median
    
    try:
        house_hash = callback.data.split("|")[1]
        listings = await get_listings_for_house_hash(house_hash)
        
        if not listings:
            # Отправляем alert только если дом не найден (это не повторный запрос)
            await callback.answer("Дом не найден", show_alert=True)
            return
        
        address = listings[0].address if listings else "Неизвестный адрес"
        prices_per_m2 = [calc_price_per_m2(l) for l in listings if calc_price_per_m2(l) is not None]
        
        if not prices_per_m2:
            explanation = (
                "📊 Почему этот дом в подборке:\n\n"
                "• Подходит под ваши фильтры\n"
                "• Несколько вариантов на выбор\n"
                "• Это один из самых выгодных вариантов среди новых объявлений."
            )
        else:
            house_median_ppm = median(prices_per_m2)
            market_median_ppm = calc_market_median_ppm(listings)
            
            # Вычисляем характеристики
            price_below_market = house_median_ppm < market_median_ppm if market_median_ppm else False
            price_diff = ((market_median_ppm - house_median_ppm) / market_median_ppm * 100) if market_median_ppm else 0
            
            dispersion = 0.0
            if len(prices_per_m2) > 1 and house_median_ppm:
                dispersion = (max(prices_per_m2) - min(prices_per_m2)) / house_median_ppm
            
            # Формируем объяснение
            reasons = []
            if price_below_market and price_diff > 5:
                reasons.append(f"• Средняя цена ниже рынка на ~{int(price_diff)}%")
            else:
                reasons.append("• Средняя цена ниже рынка")
            
            if dispersion < 0.2:
                reasons.append("• Небольшой разброс цен")
            else:
                reasons.append("• Разброс цен в пределах нормы")
            
            reasons.append(f"• {len(listings)} вариантов на выбор")
            reasons.append("• Подходит под ваши фильтры")
            
            explanation = (
                "📊 Почему этот дом в подборке:\n\n"
                + "\n".join(reasons) + "\n\n"
                "Это один из самых выгодных вариантов среди новых объявлений."
            )
        
        await callback.answer()
        await callback.message.answer(explanation, parse_mode=ParseMode.HTML)
        
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Ошибка объяснения дома: {e}")
        await callback.answer("Произошла ошибка", show_alert=True)


@router.callback_query(F.data.startswith("hide_house|"))
async def cb_hide_house(callback: CallbackQuery):
    """Скрывает дом из summary (только UI, не влияет на БД)"""
    await callback.answer("Дом скрыт из этого сообщения")
    # Просто удаляем сообщение или редактируем его
    try:
        await callback.message.delete()
    except Exception:
        # Если не удалось удалить, просто подтверждаем действие
        pass


@router.callback_query(F.data.startswith("loc_select:"))
async def cb_loc_select(callback: CallbackQuery):
    """Обработчик выбора локации из списка"""
    # Отвечаем сразу, чтобы предотвратить повторные запросы
    await callback.answer()
    
    from database_turso import get_user_filters_turso, set_user_filters_turso
    from services.location_service import get_location_by_id
    from bot.handlers.filters_quick import show_filters_master
    
    try:
        parts = callback.data.split(":")
        user_id = int(parts[1])
        location_id = parts[2]
        
        # Проверяем, что callback от правильного пользователя
        if callback.from_user.id != user_id:
            # Отправляем alert только если это не правильный пользователь (это не повторный запрос)
            await callback.answer("⛔ Это не ваш выбор", show_alert=True)
            return
        
        # Получаем локацию по ID
        location = await get_location_by_id(location_id)
        
        if not location:
            # Если не найдено в кэше, пробуем найти через search
            from services.location_service import search_locations
            results = await search_locations(location_id)
            if results:
                location = results[0]
            else:
                # Отправляем alert только если локация не найдена (это не повторный запрос)
                await callback.answer("Локация не найдена", show_alert=True)
                return
        
        # Получаем текущие фильтры
        filters = await get_user_filters_turso(user_id)
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
        
        # Сохраняем location dict
        filters["city"] = location
        
        # Сохраняем фильтры
        await set_user_filters_turso(user_id, filters)
        
        # Логируем выбор
        import logging
        logger = logging.getLogger(__name__)
        logger.info(
            f"[LOC_USER_SELECT] user={user_id} chosen={location.get('id')} name={location.get('name')}"
        )
        
        # Уже ответили в начале функции
        # Обновляем сообщение или показываем quick wizard
        try:
            await show_filters_master(callback.message, user_id)
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(
                "[FILTER_UI][START] Failed to show filters master after location select user=%s error=%s",
                user_id,
                e,
                exc_info=True
            )
            # Отправляем alert только при ошибке (это не повторный запрос)
            await callback.answer("Ошибка открытия настроек", show_alert=True)
        
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Ошибка обработки выбора локации: {e}")
        # Отправляем alert только при ошибке (это не повторный запрос)
        await callback.answer("Произошла ошибка", show_alert=True)


@router.callback_query(F.data.startswith("show_house|"))
async def cb_show_house(callback: CallbackQuery):
    """Обработчик кнопки 'Показать варианты' для конкретного дома с поддержкой пагинации"""
    # Отвечаем сразу, чтобы предотвратить повторные запросы
    await callback.answer()
    
    from bot.services.notification_service import get_listings_for_house_hash, send_grouped_listings_with_pagination
    
    user_id = callback.from_user.id
    
    try:
        # Извлекаем hash адреса и offset из callback_data
        parts = callback.data.split("|")
        house_hash = parts[1]
        offset = int(parts[2]) if len(parts) > 2 else 0
        
        # Получаем объявления для этого адреса
        listings = await get_listings_for_house_hash(house_hash)
        
        if not listings:
            # Отправляем alert только если нет вариантов (это не повторный запрос)
            await callback.answer("Нет доступных вариантов", show_alert=True)
            return
        
        # Отправляем объявления с пагинацией
        # Уже ответили в начале функции
        await send_grouped_listings_with_pagination(
            callback.bot,
            user_id,
            listings,
            offset
        )
        
    except ValueError:
        # Отправляем alert только при ошибке (это не повторный запрос)
        await callback.answer("Ошибка формата запроса", show_alert=True)
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Ошибка обработки show_house для пользователя {user_id}: {e}")
        # Отправляем alert только при ошибке (это не повторный запрос)
        await callback.answer("Произошла ошибка при загрузке вариантов", show_alert=True)


@router.message(Command("mode"))
async def cmd_mode(message: Message):
    """Обработчик команды /mode для переключения режимов доставки"""
    from database_turso import get_user_filters_turso, set_user_filters_turso
    
    user_id = message.from_user.id
    filters = await get_user_filters_turso(user_id)
    current_mode = filters.get('delivery_mode', 'brief') if filters else 'brief'
    
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🔹 Кратко" if current_mode != 'brief' else "🔹 Кратко (текущий)",
        callback_data="mode_set:brief"
    )
    builder.button(
        text="🔹 Полностью" if current_mode != 'full' else "🔹 Полностью (текущий)",
        callback_data="mode_set:full"
    )
    builder.adjust(1)
    
    current_mode_text = "🔹 Кратко" if current_mode == 'brief' else "🔹 Полностью"
    
    await message.answer(
        "📦 <b>Режим доставки объявлений</b>\n\n"
        "🔹 <b>Кратко</b> (рекомендуется)\n"
        "— сначала список лучших домов\n"
        "— детали по запросу\n"
        "— минимум сообщений\n\n"
        "🔹 <b>Полностью</b>\n"
        "— каждое объявление отдельно\n"
        "— подходит для ручного выбора\n\n"
        f"Текущий режим: {current_mode_text}",
        parse_mode=ParseMode.HTML,
        reply_markup=builder.as_markup()
    )


@router.callback_query(F.data.startswith("mode_set:"))
async def cb_mode_set(callback: CallbackQuery):
    """Обработчик установки режима доставки"""
    from database_turso import get_user_filters_turso, set_user_filters_turso
    
    mode = callback.data.split(":")[1]
    user_id = callback.from_user.id
    
    # Сохраняем режим в БД
    filters = await get_user_filters_turso(user_id)
    if filters:
        filters['delivery_mode'] = mode
        await set_user_filters_turso(user_id, filters)
    
    # Сохраняем режим в in-memory хранилище
    USER_DELIVERY_MODES[user_id] = mode
    
    # Уже ответили в начале функции
    mode_text = "кратко" if mode == DELIVERY_MODE_BRIEF else "подробно"
    await callback.message.edit_text(
        f"✅ Режим уведомлений установлен: <b>{mode_text}</b>\n\n"
        f"{'📋 Вы будете получать одно summary-сообщение с группировкой по адресам' if mode == DELIVERY_MODE_BRIEF else '📨 Вы будете получать подробные уведомления по каждому объявлению'}",
        parse_mode=ParseMode.HTML
    )


async def process_city_input_no_fsm(message: Message, state: FSMContext):
    """
    Обработка ввода города БЕЗ FSM (использует флаг awaiting_city)
    """
    import logging
    from database_turso import ensure_user_filters, get_user_filters_turso, set_user_filters_turso
    from bot.handlers.filters_quick import show_filters_master
    from bot.utils.city_lookup import find_city_slug_by_text
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from constants.constants import LOG_FILTER_SAVE, LOG_FILTER_VERIFY
    from error_logger import log_info
    
    logger = logging.getLogger(__name__)
    user_id = message.from_user.id
    user_input = message.text.strip()
    city_raw = user_input
    
    log_info("city_input", f"[CITY_INPUT] user={user_id} input={city_raw!r}")
    
    # Гарантируем наличие фильтров
    await ensure_user_filters(telegram_id=user_id)
    
    # Ищем город через локальную карту
    log_info("city_lookup", f"[CITY_LOOKUP] user={user_id} query={city_raw!r}")
    results = await find_city_slug_by_text(user_input, limit=6)
    
    if not results:
        # Город не найден
        builder = InlineKeyboardBuilder()
        builder.button(text="Попробовать ещё", callback_data="setup_filters")
        await message.answer(
            "❌ Город не найден. Проверьте написание или уточните название.\n"
            "Попробуйте ввести название города ещё раз.",
            reply_markup=builder.as_markup()
        )
        return
    
    if len(results) == 1:
        # Один результат - автоматически выбираем
        city_result = results[0]
        slug = city_result['slug']
        label_ru = city_result['label_ru']
        
        # Получаем текущие фильтры
        filters = await get_user_filters_turso(user_id)
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
        
        # Сохраняем slug и label, сбрасываем awaiting_city
        filters["city"] = label_ru.lower()  # Для совместимости
        filters["city_slug"] = slug  # Новое поле для slug
        filters["city_display"] = label_ru  # Отображаемое имя
        filters["awaiting_city"] = 0  # Сбрасываем флаг
        
        # Сохраняем фильтры
        await set_user_filters_turso(user_id, filters)
        
        log_info("city_selected", f"[CITY_SELECTED] user={user_id} city={label_ru} slug={slug} auto_selected=True")
        logger.info(f"{LOG_FILTER_SAVE} user={user_id} city={label_ru} slug={slug} auto_selected=True")
        
        await message.answer(f"✅ Выбран город: <b>{label_ru}</b>", parse_mode=ParseMode.HTML)
        await state.clear()
        
        # Показываем quick master
        try:
            await show_filters_master(message, user_id)
        except Exception as e:
            logger.error(f"[FILTER_UI] Failed to show filters master: {e}", exc_info=True)
            await message.answer("Фильтры сохранены. Используйте /start для просмотра.")
        return
    
    # Несколько результатов - показываем выбор
    # Дедупликация городов по slug для предотвращения дубликатов кнопок
    from collections import OrderedDict
    unique_results = []
    seen_slugs = set()
    for city_result in results[:6]:  # Максимум 6 вариантов
        slug = city_result['slug']
        if slug not in seen_slugs:
            seen_slugs.add(slug)
            unique_results.append(city_result)
    
    builder = InlineKeyboardBuilder()
    from bot.utils.callback_codec import encode_callback_payload
    for city_result in unique_results:
        slug = city_result['slug']
        label_ru = city_result['label_ru']
        score = city_result.get('score', 0)
        province = city_result.get('province', '')
        
        # Формируем текст кнопки
        button_text = label_ru
        if province:
            # Показываем провинцию если есть
            province_display = province.replace('_', ' ').title()
            button_text = f"{label_ru} ({province_display})"
        
        # Кодируем длинный slug через short_links
        short_code = await encode_callback_payload(slug)
        builder.button(
            text=button_text,
            callback_data=f"select_city|{short_code}"
        )
    
    builder.button(text="❌ Отмена", callback_data="setup_filters")
    builder.adjust(1)
    
    keyboard = builder.as_markup()
    logger.debug(f"[CITY_KEYBOARD] Created city selection keyboard user={user_id} buttons={len(unique_results)} rows={len(keyboard.inline_keyboard)}")
    log_info("city_lookup", f"[CITY_LOOKUP] user={user_id} query={city_raw!r} found={len(results)} results unique={len(unique_results)}")
    
    await message.answer(
        f"🔍 Найдено {len(unique_results)} вариантов. Выберите нужный город:",
        reply_markup=keyboard
    )


@router.message(CityStates.waiting_for_city)
async def process_city_input(message: Message, state: FSMContext):
    """Обработка ввода города с использованием локальной карты городов"""
    import logging
    from database_turso import ensure_user_filters, get_user_filters_turso, set_user_filters_turso
    from bot.handlers.filters_quick import show_filters_master
    from bot.utils.city_lookup import find_city_slug_by_text
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from constants.constants import LOG_FILTER_SAVE, LOG_FILTER_VERIFY
    
    logger = logging.getLogger(__name__)
    user_id = message.from_user.id
    user_input = message.text.strip()
    city_raw = user_input
    
    # Гарантируем наличие фильтров
    await ensure_user_filters(telegram_id=user_id)
    
    # Лог до сохранения
    logger.info(f"{LOG_FILTER_SAVE} user={user_id} saving city_raw={city_raw!r}")
    
    # Ищем город через локальную карту
    log_info("city_lookup", f"[CITY_LOOKUP] user={user_id} query={city_raw!r}")
    results = await find_city_slug_by_text(user_input, limit=6)
    
    if not results:
        # Город не найден
        builder = InlineKeyboardBuilder()
        builder.button(text="Попробовать ещё", callback_data="setup_filters")
        await message.answer(
            "❌ Город не найден. Проверьте написание или уточните название.\n"
            "Попробуйте ввести название города ещё раз.",
            reply_markup=builder.as_markup()
        )
        return
    
    if len(results) == 1:
        # Один результат - автоматически выбираем
        city_result = results[0]
        slug = city_result['slug']
        label_ru = city_result['label_ru']
        
        # Получаем текущие фильтры
        filters = await get_user_filters_turso(user_id)
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
        
        # Сохраняем slug и label, сбрасываем awaiting_city
        filters["city"] = label_ru.lower()  # Для совместимости
        filters["city_slug"] = slug  # Новое поле для slug
        filters["city_display"] = label_ru  # Отображаемое имя
        filters["awaiting_city"] = 0  # Сбрасываем флаг
        
        # Сохраняем фильтры
        await set_user_filters_turso(user_id, filters)
        
        from error_logger import log_info
        log_info("city_selected", f"[CITY_SELECTED] user={user_id} city={label_ru} slug={slug} auto_selected=True")
        logger.info(f"{LOG_FILTER_SAVE} user={user_id} city={label_ru} slug={slug} auto_selected=True")
        
        await message.answer(f"✅ Выбран город: <b>{label_ru}</b>", parse_mode=ParseMode.HTML)
        await state.clear()
        
        # Показываем quick master
        try:
            await show_filters_master(message, user_id)
        except Exception as e:
            logger.error(f"[FILTER_UI] Failed to show filters master: {e}", exc_info=True)
            await message.answer("Фильтры сохранены. Используйте /start для просмотра.")
        return
    
    # Несколько результатов - показываем выбор
    # Дедупликация городов по slug для предотвращения дубликатов кнопок
    from collections import OrderedDict
    unique_results = []
    seen_slugs = set()
    for city_result in results[:6]:  # Максимум 6 вариантов
        slug = city_result['slug']
        if slug not in seen_slugs:
            seen_slugs.add(slug)
            unique_results.append(city_result)
    
    builder = InlineKeyboardBuilder()
    from bot.utils.callback_codec import encode_callback_payload
    for city_result in unique_results:
        slug = city_result['slug']
        label_ru = city_result['label_ru']
        score = city_result.get('score', 0)
        province = city_result.get('province', '')
        
        # Формируем текст кнопки
        button_text = label_ru
        if province:
            # Показываем провинцию если есть
            province_display = province.replace('_', ' ').title()
            button_text = f"{label_ru} ({province_display})"
        
        # Кодируем длинный slug через short_links
        short_code = await encode_callback_payload(slug)
        builder.button(
            text=button_text,
            callback_data=f"select_city|{short_code}"
        )
    
    builder.button(text="❌ Отмена", callback_data="setup_filters")
    builder.adjust(1)
    
    keyboard = builder.as_markup()
    logger.debug(f"[CITY_KEYBOARD] Created city selection keyboard user={user_id} buttons={len(unique_results)} rows={len(keyboard.inline_keyboard)}")
    
    await message.answer(
        f"🔍 Найдено {len(unique_results)} вариантов. Выберите нужный город:",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )


@router.callback_query(F.data.startswith("select_city|"))
async def cb_select_city(callback: CallbackQuery, state: FSMContext):
    """Обработчик выбора города из списка"""
    # Отвечаем сразу, чтобы предотвратить повторные запросы
    await callback.answer()
    
    import logging
    from database_turso import get_user_filters_turso, set_user_filters_turso
    from bot.handlers.filters_quick import show_filters_master
    from bot.utils.city_lookup import get_city_by_slug
    from bot.utils.callback_codec import decode_callback_payload
    
    logger = logging.getLogger(__name__)
    user_id = callback.from_user.id
    
    try:
        code = callback.data.split("|")[1]
        
        # Декодируем slug из короткого кода
        slug = await decode_callback_payload(code)

        if not slug:
            # Fallback: пробуем использовать сам код как slug (для старых сообщений)
            slug = code
        
        # Получаем информацию о городе
        city_info = await get_city_by_slug(slug)
        if not city_info:
            # Отправляем alert только если город не найден (это не повторный запрос)
            await callback.answer("Город не найден", show_alert=True)
            return
        
        label_ru = city_info['label_ru']
        
        # Получаем текущие фильтры
        filters = await get_user_filters_turso(user_id)
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
        
        # Сохраняем slug и label, сбрасываем awaiting_city
        filters["city"] = label_ru.lower()  # Для совместимости
        filters["city_slug"] = slug  # Новое поле для slug
        filters["city_display"] = label_ru  # Отображаемое имя
        filters["awaiting_city"] = 0  # Сбрасываем флаг
        
        # Сохраняем фильтры
        await set_user_filters_turso(user_id, filters)
        
        from error_logger import log_info
        log_info("city_selected", f"[CITY_SELECTED] user={user_id} city={label_ru} slug={slug} selected_from_list=True")
        logger.info(f"[CITY_SELECT] user={user_id} city={label_ru} slug={slug}")
        
        # Уже ответили в начале функции
        await state.clear()
        
        # Показываем quick master
        try:
            await show_filters_master(callback.message, user_id)
        except Exception as e:
            logger.error(f"[FILTER_UI] Failed to show filters master: {e}", exc_info=True)
            await callback.message.answer("Фильтры сохранены. Используйте /start для просмотра.")
    
    except Exception as e:
        logger.error(f"Ошибка обработки выбора города: {e}", exc_info=True)
        # Отправляем alert только при ошибке (это не повторный запрос)
        await callback.answer("Произошла ошибка", show_alert=True)


@router.message(SetupStates.waiting_for_city)
async def process_setup_city_input(message: Message, state: FSMContext):
    """Обработка ввода города в пошаговой настройке (legacy, использует новый lookup)"""
    from database_turso import get_user_filters_turso
    
    # Перенаправляем на основной обработчик
    await process_city_input(message, state)
    
    # Если город был выбран, переходим к следующему шагу
    filters = await get_user_filters_turso(message.from_user.id)
    city_data = filters.get("city") if filters else None
    has_city = city_data and (
        (isinstance(city_data, str) and city_data.strip()) or 
        (isinstance(city_data, dict) and (city_data.get("name") or city_data.get("display") or city_data.get("label_ru")))
    )
    if filters and has_city:
        await state.set_state(SetupStates.waiting_for_rooms)
        await message.answer(
            "🚪 <b>Шаг 2 из 4: Количество комнат</b>\n\n"
            "Введите количество комнат (например: 2 или 2-3):",
            parse_mode=ParseMode.HTML
        )


# Generic handler для текстовых сообщений (проверяет awaiting_city)
# Должен быть зарегистрирован ПОСЛЕ всех специфичных handlers
# Используем F.text фильтр для обработки только текстовых сообщений (не команд)
@router.message(F.text)
async def handle_text_message(message: Message, state: FSMContext):
    """
    Generic handler для текстовых сообщений.
    Проверяет флаг awaiting_city и обрабатывает ввод города без FSM.
    """
    from database_turso import get_user_filters_turso
    
    # Проверяем, ожидает ли пользователь ввода города
    user_id = message.from_user.id
    filters = await get_user_filters_turso(user_id)
    
    # Обрабатываем ТОЛЬКО если awaiting_city == 1
    if filters and filters.get("awaiting_city") == 1:
        # Пользователь ожидает ввода города - обрабатываем как город
        await process_city_input_no_fsm(message, state)
        return
    
    # Если не ожидает города - просто возвращаемся без ошибок
    # Другие handlers обработают это сообщение


# Импортируем остальные обработчики из старого bot.py
# Временно оставляем их там, чтобы не ломать функциональность
# Постепенно перенесем их сюда
