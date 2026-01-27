"""
Обработчики команд поиска и проверки объявлений
"""

from aiogram import Router
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.enums import ParseMode

from bot.services.search_service import check_new_listings
from database import get_user_filters
from database_turso import set_user_filters_turso

router = Router()


@router.message(Command("check"))
async def cmd_check(message: Message):
    """Ручная проверка объявлений"""
    await message.answer(
        "🔍 Проверяю новые объявления со всех источников...\nЭто может занять 30-60 секунд."
    )
    await check_new_listings(message.bot)
    await message.answer("✅ Проверка завершена!")


@router.message(Command("start_monitoring"))
async def cmd_start_monitoring(message: Message):
    """Включение мониторинга для пользователя"""
    user_id = message.from_user.id
    user_filters = await get_user_filters(user_id)

    if not user_filters:
        await message.answer(
            "⚠️ Фильтры не настроены. Используйте /start для настройки.", parse_mode=ParseMode.HTML
        )
        return

    from database_turso import get_user_filters_turso
    current_filters = await get_user_filters_turso(user_id) or {}
    await set_user_filters_turso(
        user_id,
        {
            "city": current_filters.get("city", "барановичи"),
            "min_rooms": current_filters.get("min_rooms", 1),
            "max_rooms": current_filters.get("max_rooms", 4),
            "min_price": current_filters.get("min_price", 0),
            "max_price": current_filters.get("max_price", 100000),
            "seller_type": current_filters.get("seller_type", "all"),
            "delivery_mode": current_filters.get("delivery_mode", "brief"),
        }
    )
    await message.answer("✅ Мониторинг включен!")


@router.message(Command("stop_monitoring"))
async def cmd_stop_monitoring(message: Message):
    """Выключение мониторинга для пользователя"""
    user_id = message.from_user.id
    user_filters = await get_user_filters(user_id)

    if not user_filters:
        await message.answer(
            "⚠️ Фильтры не настроены. Используйте /start для настройки.", parse_mode=ParseMode.HTML
        )
        return

    from database_turso import get_user_filters_turso
    current_filters = await get_user_filters_turso(user_id) or {}
    await set_user_filters_turso(
        user_id,
        {
            "city": current_filters.get("city", "барановичи"),
            "min_rooms": current_filters.get("min_rooms", 1),
            "max_rooms": current_filters.get("max_rooms", 4),
            "min_price": current_filters.get("min_price", 0),
            "max_price": current_filters.get("max_price", 100000),
            "seller_type": current_filters.get("seller_type", "all"),
            "delivery_mode": current_filters.get("delivery_mode", "brief"),
        }
    )
    await message.answer("❌ Мониторинг отключен.")


@router.message(Command("filters"))
async def cmd_filters(message: Message):
    """Показывает текущие фильтры пользователя с кнопками настройки"""
    from aiogram.utils.keyboard import InlineKeyboardBuilder

    user_id = message.from_user.id
    user_filters = await get_user_filters(user_id)

    if not user_filters:
        await message.answer(
            "⚠️ Фильтры не настроены. Используйте /start для настройки.", parse_mode=ParseMode.HTML
        )
        return

    status = "✅ Активен" if user_filters.get("is_active", True) else "❌ Отключен"

    # Создаем inline кнопки
    builder = InlineKeyboardBuilder()
    builder.button(text="⚙️ Настройки", callback_data="setup_filters")

    # Принудительно размещаем по 1 кнопке в ряду
    builder.adjust(1)

    # Используем helper для нормализации города
    from bot.handlers.start import normalize_city_for_ui
    city_name = normalize_city_for_ui(user_filters)
    
    await message.answer(
        f"⚙️ <b>Ваши фильтры</b>\n\n"
        f"📍 <b>Город:</b> {city_name}\n"
        f"🚪 <b>Комнат:</b> от {user_filters.get('min_rooms', 1)} до {user_filters.get('max_rooms', 4)}\n"
        f"💰 <b>Цена:</b> ${user_filters.get('min_price', 0):,} - ${user_filters.get('max_price', 100000):,}\n"
        f"🤖 <b>Режим:</b> {'ИИ-режим' if user_filters.get('ai_mode') else 'Обычный режим'}\n\n"
        f"📡 <b>Статус:</b> {status}\n\n"
        f"<i>Нажмите кнопку для изменения фильтров</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=builder.as_markup(),
    )
