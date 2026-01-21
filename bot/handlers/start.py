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
        await show_filters_master(callback, user_id)


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


@router.message(CityStates.waiting_for_city)
async def process_city_input(message: Message, state: FSMContext):
    """Обработка ввода города и запуск quick wizard"""
    from database_turso import ensure_user_filters, get_user_filters_turso, set_user_filters_turso
    from bot.handlers.filters_quick import show_filters_master
    
    user_id = message.from_user.id
    city = message.text.strip()
    
    # Гарантируем наличие фильтров
    await ensure_user_filters(telegram_id=user_id)
    
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
    
    # Обновляем город
    filters["city"] = city
    
    # Сохраняем фильтры с городом
    await set_user_filters_turso(user_id, filters)
    
    # Запускаем quick wizard
    await show_filters_master(message, user_id)
    
    await state.clear()


# Импортируем остальные обработчики из старого bot.py
# Временно оставляем их там, чтобы не ломать функциональность
# Постепенно перенесем их сюда
