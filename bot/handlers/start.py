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
            "✏️ Введите город (например: Барановичи)",
            parse_mode=ParseMode.HTML,
        )
        # Устанавливаем состояние для ввода города
        await state.set_state(CityStates.waiting_for_city)
    else:
        # Фильтры уже установлены - показываем их и предлагаем изменить
        status = "✅ Активен" if user_filters.get("is_active") else "❌ Отключен"

        builder = InlineKeyboardBuilder()
        builder.button(text="🔍 Проверить сейчас", callback_data="check_now")
        builder.button(text="🤖 ИИ-анализ", callback_data="check_now_ai")
        builder.button(text="⚙️ Изменить фильтры", callback_data="setup_filters")
        builder.button(text="📊 Статистика", callback_data="show_stats")

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
        await message.answer(
            f"🏠 <b>Ваши фильтры</b>\n\n"
            f"📍 <b>Город:</b> {city_name}\n"
            f"🚪 <b>Комнат:</b> от {user_filters.get('min_rooms', 1)} до {user_filters.get('max_rooms', 4)}\n"
            f"💰 <b>Цена:</b> {price_from} – {price_to}\n\n"
            f"📡 <b>Статус:</b> {status}\n\n"
            f"Я проверяю новые объявления каждые 12 часов и присылаю только те, что подходят под ваши фильтры.\n\n"
            f'💡 <i>Для ИИ-оценки конкретного объявления используйте кнопку "🤖 ИИ Оценка квартиры" под каждым объявлением.</i>',
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
    await callback.answer("Настройка фильтров...")
    await show_city_selection_menu(callback.message, state)


@router.callback_query(F.data == "show_stats")
async def cb_show_stats(callback: CallbackQuery):
    """Показывает статистику пользователя"""
    await callback.answer("Статистика пока не реализована")


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
    parts = message.text.split()
    
    if len(parts) != 2 or parts[1] not in (DELIVERY_MODE_BRIEF, DELIVERY_MODE_FULL):
        await message.answer(
            f"Использование: /mode {DELIVERY_MODE_BRIEF} или /mode {DELIVERY_MODE_FULL}\n\n"
            f"• {DELIVERY_MODE_BRIEF} - краткие summary-сообщения с группировкой\n"
            f"• {DELIVERY_MODE_FULL} - подробные уведомления по каждому объявлению",
            parse_mode=ParseMode.HTML
        )
        return
    
    mode = parts[1]
    user_id = message.from_user.id
    
    # Сохраняем режим в in-memory хранилище
    USER_DELIVERY_MODES[user_id] = mode
    
    mode_text = "кратко" if mode == DELIVERY_MODE_BRIEF else "подробно"
    await message.answer(
        f"✅ Режим уведомлений установлен: <b>{mode_text}</b>\n\n"
        f"{'📋 Вы будете получать одно summary-сообщение с группировкой по адресам' if mode == DELIVERY_MODE_BRIEF else '📨 Вы будете получать подробные уведомления по каждому объявлению'}",
        parse_mode=ParseMode.HTML
    )


@router.message(CityStates.waiting_for_city)
async def process_city_input(message: Message, state: FSMContext):
    """Обработка ввода города и запуск quick wizard"""
    from database_turso import ensure_user_filters, get_user_filters_turso, set_user_filters_turso
    from bot.handlers.filters_quick import build_kb, format_filters_summary
    
    user_id = message.from_user.id
    city = message.text.strip()
    
    # Гарантируем наличие фильтров
    await ensure_user_filters(telegram_id=user_id)
    
    # Получаем текущие фильтры
    f = await get_user_filters_turso(user_id)
    if not f:
        f = {
            "city": None,
            "min_rooms": 1,
            "max_rooms": 4,
            "min_price": 0,
            "max_price": 100000,
            "seller_type": "all",
            "delivery_mode": "brief",
        }
    
    # Обновляем город
    f["city"] = city
    
    # Сохраняем фильтры с городом
    await set_user_filters_turso(user_id, f)
    
    # Запускаем quick wizard
    await message.answer(
        "⚙️ Быстрая настройка фильтров\n\n" + format_filters_summary(f),
        reply_markup=build_kb(user_id),
    )
    
    await state.clear()


# Импортируем остальные обработчики из старого bot.py
# Временно оставляем их там, чтобы не ломать функциональность
# Постепенно перенесем их сюда
