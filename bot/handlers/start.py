"""
Обработчики команды /start и настройки фильтров
"""

from typing import Optional
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.enums import ParseMode
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database import get_user_filters
from bot.services.search_service import check_new_listings
from bot.services.ai_service import check_new_listings_ai_mode

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
    user_id = message.from_user.id

    # Создаем/обновляем пользователя в Turso
    try:
        from database import create_or_update_user_turso

        await create_or_update_user_turso(
            user_id=user_id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
        )
    except Exception as e:
        import logging

        logger = logging.getLogger(__name__)
        logger.warning(f"Не удалось создать пользователя в Turso: {e}")

    # Проверяем, есть ли у пользователя фильтры (из старой БД или Turso)
    user_filters = await get_user_filters(user_id)

    # Если фильтров нет в старой БД, проверяем Turso
    if not user_filters:
        try:
            from database import get_user_filters_turso

            user_filters = await get_user_filters_turso(user_id)
            # Конвертируем формат фильтров из Turso в формат старой БД для совместимости
            if user_filters:
                # Конвертируем rooms из списка в min_rooms/max_rooms
                rooms = user_filters.get("rooms", [])
                if rooms and len(rooms) > 0:
                    user_filters["min_rooms"] = min(rooms)
                    user_filters["max_rooms"] = max(rooms)
                else:
                    user_filters["min_rooms"] = 1
                    user_filters["max_rooms"] = 4
                user_filters["is_active"] = user_filters.get("active", True)
                user_filters["city"] = user_filters.get("region", "барановичи")
        except Exception as e:
            import logging

            logger = logging.getLogger(__name__)
            logger.warning(f"Не удалось получить фильтры из Turso: {e}")

    if not user_filters:
        # Первый запуск - начинаем пошаговую настройку
        await message.answer(
            "👋 <b>Добро пожаловать!</b>\n\n"
            "Я помогу вам найти квартиру.\n\n"
            "📋 <b>Давайте настроим фильтры пошагово:</b>\n"
            "1️⃣ Выберите город\n"
            "2️⃣ Выберите диапазон комнат\n"
            "3️⃣ Укажите диапазон цен\n"
            "4️⃣ Выберите тип продавца (Kufar)\n"
            "5️⃣ Выберите режим работы\n\n"
            "Начнем с выбора города:",
            parse_mode=ParseMode.HTML,
        )

        # Показываем меню выбора города
        await show_city_selection_menu(message, state)
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

        city_name = user_filters.get("city", "барановичи").title()
        await message.answer(
            f"🏠 <b>Ваши фильтры</b>\n\n"
            f"📍 <b>Город:</b> {city_name}\n"
            f"🚪 <b>Комнат:</b> от {user_filters.get('min_rooms', 1)} до {user_filters.get('max_rooms', 4)}\n"
            f"💰 <b>Цена:</b> ${user_filters.get('min_price', 0):,} - ${user_filters.get('max_price', 100000):,}\n\n"
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
async def cb_setup_filters(callback: CallbackQuery):
    """Настройка фильтров для пользователя"""
    await callback.answer("Настройка фильтров...")
    await show_city_selection_menu(callback.message, callback.state)


@router.callback_query(F.data == "show_stats")
async def cb_show_stats(callback: CallbackQuery):
    """Показывает статистику пользователя"""
    await callback.answer("Статистика пока не реализована")


# Импортируем остальные обработчики из старого bot.py
# Временно оставляем их там, чтобы не ломать функциональность
# Постепенно перенесем их сюда
