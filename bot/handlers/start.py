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
    
    if not user_filters or not user_filters.get("city"):
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
        # Фильтры уже установлены - показываем приветствие и текущие фильтры
        status = "✅ Активен" if user_filters.get("is_active") else "❌ Отключен"

        builder = InlineKeyboardBuilder()
        builder.button(text="⚙️ Изменить фильтры", callback_data="setup_filters")
        builder.button(text="📊 Как я выбираю лучшие квартиры", callback_data="explain_scoring")
        builder.button(text="🔍 Проверить сейчас", callback_data="check_now")

        # Принудительно размещаем по 1 кнопке в ряду
        builder.adjust(1)

        # Безопасное форматирование цен
        def fmt_price(v):
            return f"${int(v):,}".replace(",", " ") if v is not None else "—"
        
        min_price = user_filters.get('min_price')
        max_price = user_filters.get('max_price')
        price_from = fmt_price(min_price)
        price_to = fmt_price(max_price)
        
        city_name = user_filters.get("city", "барановичи") or "Не выбран"
        city_name = city_name.title() if city_name else "Не выбран"
        
        # Формируем текст фильтров
        min_rooms = user_filters.get('min_rooms', 1)
        max_rooms = user_filters.get('max_rooms', 4)
        rooms_text = f"{min_rooms}–{max_rooms}" if min_rooms != max_rooms else str(min_rooms)
        
        seller_type = user_filters.get('seller_type', 'all')
        seller_text = {
            'all': 'все',
            'owner': 'только собственники',
            'owners': 'только собственники',
            'company': 'только агентства'
        }.get(seller_type, 'все')
        
        delivery_mode = user_filters.get('delivery_mode', 'brief')
        mode_text = 'кратко' if delivery_mode == 'brief' else 'подробно'
        
        await message.answer(
            "👋 Привет! Я KeyFlat — умный бот для поиска квартир.\n\n"
            "Я:\n"
            "• автоматически отслеживаю новые объявления\n"
            "• группирую варианты по домам\n"
            "• показываю сначала лучшие предложения\n"
            "• не спамлю десятками сообщений\n\n"
            "📍 Сейчас я ищу квартиры по этим параметрам:\n\n"
            f"📍 Город: {city_name}\n"
            f"🚪 Комнаты: {rooms_text}\n"
            f"💰 Цена: {price_from} – {price_to}\n"
            f"👤 Продавец: {seller_text}\n"
            f"📦 Режим: {mode_text}\n\n"
            f"📡 Статус: {status}\n\n"
            "⚙️ Вы можете изменить фильтры или просто подождать — я пришлю новые варианты сам.",
            parse_mode=ParseMode.HTML,
            reply_markup=builder.as_markup(),
        )


async def show_city_selection_menu(message: Message, state: FSMContext):
    """Показывает меню выбора города для пошаговой настройки"""
    builder = InlineKeyboardBuilder()

    # Все кнопки на отдельных строках для лучшей читаемости
    builder.button(text="Минск", callback_data="setup_city_минск")
    builder.button(text="Брест", callback_data="setup_city_брест")
    builder.button(text="Гродно", callback_data="setup_city_гродно")
    builder.button(text="Витебск", callback_data="setup_city_витебск")
    builder.button(text="Гомель", callback_data="setup_city_гомель")
    builder.button(text="Могилёв", callback_data="setup_city_могилёв")
    builder.button(text="✏️ Ввести вручную", callback_data="setup_city_manual")

    # Принудительно размещаем по 1 кнопке в ряду
    builder.adjust(1)

    await message.answer(
        "📍 <b>Шаг 1 из 4: Выберите город</b>\n\n"
        "Выберите город из списка или введите название вручную.",
        parse_mode=ParseMode.HTML,
        reply_markup=builder.as_markup(),
    )
    await state.set_state(SetupStates.waiting_for_city)


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
        
        await callback.answer(f"✅ Выбран: {label_ru}")
        await state.set_state(SetupStates.waiting_for_rooms)
        await callback.message.answer(
            "🚪 <b>Шаг 2 из 4: Количество комнат</b>\n\n"
            "Введите количество комнат (например: 2 или 2-3):",
            parse_mode=ParseMode.HTML
        )
    else:
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
    
    if not filters or not filters.get("city"):
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
            await callback.answer("Ошибка открытия настроек", show_alert=True)


@router.callback_query(F.data == "show_stats")
async def cb_show_stats(callback: CallbackQuery):
    """Показывает статистику пользователя"""
    await callback.answer("Статистика пока не реализована")


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
    from bot.services.notification_service import get_listings_for_house_hash
    from utils.scoring import calc_price_per_m2, calc_market_median_ppm
    from statistics import median
    
    try:
        house_hash = callback.data.split("|")[1]
        listings = await get_listings_for_house_hash(house_hash)
        
        if not listings:
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
    from database_turso import get_user_filters_turso, set_user_filters_turso
    from services.location_service import get_location_by_id
    from bot.handlers.filters_quick import show_filters_master
    
    try:
        parts = callback.data.split(":")
        user_id = int(parts[1])
        location_id = parts[2]
        
        # Проверяем, что callback от правильного пользователя
        if callback.from_user.id != user_id:
            await callback.answer("⛔ Это не ваш выбор")
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
        
        # Сообщаем пользователю
        region_text = f" ({location.get('region', '')})" if location.get('region') else ""
        await callback.answer(f"Выбран город: {location.get('name', '')}{region_text}")
        
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
            await callback.answer("Ошибка открытия настроек", show_alert=True)
        
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Ошибка обработки выбора локации: {e}")
        await callback.answer("Произошла ошибка", show_alert=True)


@router.callback_query(F.data.startswith("show_house|"))
async def cb_show_house(callback: CallbackQuery):
    """Обработчик кнопки 'Показать варианты' для конкретного дома с поддержкой пагинации"""
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
            await callback.answer("Нет доступных вариантов", show_alert=True)
            return
        
        # Отправляем объявления с пагинацией
        await send_grouped_listings_with_pagination(
            callback.bot,
            user_id,
            listings,
            offset
        )
        
        await callback.answer()
        
    except ValueError:
        await callback.answer("Ошибка формата запроса", show_alert=True)
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Ошибка обработки show_house для пользователя {user_id}: {e}")
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
    
    mode_text = "кратко" if mode == DELIVERY_MODE_BRIEF else "подробно"
    await callback.answer(f"Режим установлен: {mode_text}")
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
    builder = InlineKeyboardBuilder()
    for city_result in results[:6]:  # Максимум 6 вариантов
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
        
        builder.button(
            text=button_text,
            callback_data=f"select_city|{slug}"
        )
    
    builder.button(text="❌ Отмена", callback_data="setup_filters")
    builder.adjust(1)
    
    log_info("city_lookup", f"[CITY_LOOKUP] user={user_id} query={city_raw!r} found={len(results)} results")
    
    await message.answer(
        f"🔍 Найдено {len(results)} вариантов. Выберите нужный город:",
        reply_markup=builder.as_markup()
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
    builder = InlineKeyboardBuilder()
    for city_result in results[:6]:  # Максимум 6 вариантов
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
        
        builder.button(
            text=button_text,
            callback_data=f"select_city|{slug}"
        )
    
    builder.button(text="❌ Отмена", callback_data="setup_filters")
    builder.adjust(1)
    
    await message.answer(
        f"🔍 Найдено {len(results)} вариантов. Выберите нужный город:",
        reply_markup=builder.as_markup(),
        parse_mode=ParseMode.HTML
    )


@router.callback_query(F.data.startswith("select_city|"))
async def cb_select_city(callback: CallbackQuery, state: FSMContext):
    """Обработчик выбора города из списка"""
    import logging
    from database_turso import get_user_filters_turso, set_user_filters_turso
    from bot.handlers.filters_quick import show_filters_master
    from bot.utils.city_lookup import get_city_by_slug
    
    logger = logging.getLogger(__name__)
    user_id = callback.from_user.id
    
    try:
        slug = callback.data.split("|")[1]
        
        # Получаем информацию о городе
        city_info = await get_city_by_slug(slug)
        if not city_info:
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
        
        await callback.answer(f"✅ Выбран город: {label_ru}")
        await state.clear()
        
        # Показываем quick master
        try:
            await show_filters_master(callback.message, user_id)
        except Exception as e:
            logger.error(f"[FILTER_UI] Failed to show filters master: {e}", exc_info=True)
            await callback.message.answer("Фильтры сохранены. Используйте /start для просмотра.")
    
    except Exception as e:
        logger.error(f"Ошибка обработки выбора города: {e}", exc_info=True)
        await callback.answer("Произошла ошибка", show_alert=True)


@router.message(SetupStates.waiting_for_city)
async def process_setup_city_input(message: Message, state: FSMContext):
    """Обработка ввода города в пошаговой настройке (legacy, использует новый lookup)"""
    from database_turso import get_user_filters_turso
    
    # Перенаправляем на основной обработчик
    await process_city_input(message, state)
    
    # Если город был выбран, переходим к следующему шагу
    filters = await get_user_filters_turso(message.from_user.id)
    if filters and filters.get("city"):
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
    
    if filters and filters.get("awaiting_city"):
        # Пользователь ожидает ввода города - обрабатываем как город
        await process_city_input_no_fsm(message, state)
        return
    
    # Если не ожидает города - пропускаем (другие handlers обработают)


# Импортируем остальные обработчики из старого bot.py
# Временно оставляем их там, чтобы не ломать функциональность
# Постепенно перенесем их сюда
