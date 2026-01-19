"""
Сервис для отправки уведомлений пользователям
"""

import asyncio
import logging
from typing import Optional, Dict, Any, List

from aiogram import Bot
from aiogram.types import InputMediaPhoto, Message
from aiogram.enums import ParseMode
from aiogram.utils.keyboard import InlineKeyboardBuilder

from scrapers.base import Listing
from database import (
    mark_listing_sent,
    mark_listing_sent_to_user,
    is_listing_ai_valuated,
)
from config import MAX_PHOTOS
from error_logger import log_info, log_warning, log_error
from bot.services.telegram_api import (
    safe_send_message,
    safe_send_media_group,
    safe_edit_message_text,
)

logger = logging.getLogger(__name__)

# ИИ-оценщик (опционально)
try:
    from ai_valuator import valuate_listing

    AI_VALUATOR_AVAILABLE = True
except ImportError:
    AI_VALUATOR_AVAILABLE = False
    valuate_listing = None


def format_listing_message(listing: Listing, ai_valuation: Optional[Dict[str, Any]] = None) -> str:
    """Форматирует сообщение об объявлении"""
    rooms_text = f"{listing.rooms}-комн." if listing.rooms > 0 else ""
    area_text = f"{listing.area} м²" if listing.area > 0 else ""

    # Формируем заголовок
    title_parts = [p for p in [rooms_text, area_text] if p]
    title = " • ".join(title_parts) if title_parts else listing.title

    # Строим сообщение
    lines = [f"🏠 <b>{title}</b>", ""]

    # Цена
    lines.append(f"💰 <b>Цена:</b> {listing.price_formatted}")

    # ИИ-оценка (если доступна)
    if ai_valuation:
        fair_price = ai_valuation.get("fair_price_usd", 0)
        is_overpriced = ai_valuation.get("is_overpriced", False)
        assessment = ai_valuation.get("assessment", "")
        renovation_state = ai_valuation.get("renovation_state", "")
        recommendations = ai_valuation.get("recommendations", "")
        value_score = ai_valuation.get("value_score", 0)

        if fair_price > 0:
            price_status = "🔴 Завышена" if is_overpriced else "🟢 Справедлива"
            lines.append("")
            lines.append(f"🤖 <b>ИИ-оценка:</b> ${fair_price:,} {price_status}".replace(",", " "))

            # Оценка соотношения цена/качество
            if value_score > 0:
                score_emoji = "⭐" * min(value_score, 5)  # До 5 звезд
                lines.append(f"⭐ <b>Оценка:</b> {value_score}/10 {score_emoji}")

            # Состояние ремонта
            if renovation_state:
                renovation_emoji = {
                    "отличное": "✨",
                    "хорошее": "✅",
                    "среднее": "⚪",
                    "требует ремонта": "⚠️",
                    "плохое": "❌",
                }.get(renovation_state.lower(), "📋")
                lines.append(f"{renovation_emoji} <b>Ремонт:</b> {renovation_state}")

            # Детальная оценка
            if assessment:
                lines.append(f"💡 <i>{assessment}</i>")

            # Рекомендации
            if recommendations:
                lines.append("")
                lines.append(f"📋 <b>Рекомендации:</b>")
                lines.append(f"<i>{recommendations}</i>")

            lines.append("")

    # Цена за м² (вычисляется автоматически в Listing.__post_init__)
    if listing.price_per_sqm_formatted:
        lines.append(f"📊 <b>Цена/м²:</b> {listing.price_per_sqm_formatted}")

    # Основная информация
    lines.append(f"🚪 <b>Комнат:</b> {listing.rooms}")
    lines.append(f"📐 <b>Площадь:</b> {listing.area} м²")

    # Жилая площадь (если отличается от общей)
    if listing.living_area > 0 and listing.living_area != listing.area:
        lines.append(f"🛋️ <b>Жилая площадь:</b> {listing.living_area} м²")

    # Площадь кухни
    if listing.kitchen_area > 0:
        lines.append(f"🍳 <b>Кухня:</b> {listing.kitchen_area} м²")

    # Этаж
    if listing.floor:
        lines.append(f"🏢 <b>Этаж:</b> {listing.floor}")
    elif listing.total_floors:
        # Если есть только этажность без этажа
        lines.append(f"🏢 <b>Этажность:</b> {listing.total_floors} этажей")

    # Год постройки
    if listing.year_built:
        lines.append(f"📅 <b>Год:</b> {listing.year_built}")

    # Тип дома
    if listing.house_type:
        lines.append(f"🏗️ <b>Тип дома:</b> {listing.house_type}")

    # Балкон/лоджия
    if listing.balcony:
        balcony_emoji = "✅" if listing.balcony.lower() in ["есть", "да", "1"] else "❌"
        lines.append(f"{balcony_emoji} <b>Балкон:</b> {listing.balcony}")

    # Санузел
    if listing.bathroom:
        lines.append(f"🚿 <b>Санузел:</b> {listing.bathroom}")

    # Состояние ремонта
    if listing.renovation_state:
        renovation_emoji = {
            "отличное": "✨",
            "хорошее": "✅",
            "среднее": "⚪",
            "требует ремонта": "⚠️",
            "плохое": "❌",
            "вторичное": "📋",
        }.get(listing.renovation_state.lower(), "📋")
        lines.append(f"{renovation_emoji} <b>Ремонт:</b> {listing.renovation_state}")

    # Тип продавца
    if listing.is_company is not None:
        seller_type = "🏢 Агентство" if listing.is_company else "👤 Собственник"
        lines.append(f"{seller_type}")

    # Дата создания объявления
    if listing.created_at:
        # Форматируем дату для вывода
        try:
            from datetime import datetime

            date_obj = datetime.strptime(listing.created_at, "%Y-%m-%d")
            today = datetime.now()
            days_diff = (today - date_obj).days

            if days_diff == 0:
                date_display = "сегодня"
            elif days_diff == 1:
                date_display = "вчера"
            elif days_diff < 7:
                date_display = f"{days_diff} дн. назад"
            else:
                date_display = date_obj.strftime("%d.%m.%Y")
        except Exception:
            date_display = listing.created_at

        lines.append(f"📆 <b>Опубликовано:</b> {date_display}")

    # Описание (первые 300 символов)
    if listing.description:
        description_text = listing.description.strip()
        if len(description_text) > 300:
            description_text = description_text[:300] + "..."
        lines.append("")
        lines.append(f"📝 <b>Описание:</b>")
        lines.append(f"<i>{description_text}</i>")

    lines.append("")
    lines.append(f"📍 <b>Адрес:</b> {listing.address}")
    lines.append(f"🌐 <b>Источник:</b> {listing.source}")
    lines.append("")
    lines.append(f'🔗 <a href="{listing.url}">Открыть объявление</a>')

    return "\n".join(lines)


async def send_listing_to_user(
    bot: Bot, user_id: int, listing: Listing, use_ai_valuation: bool = False
) -> bool:
    """Отправляет объявление пользователю

    Args:
        bot: Экземпляр бота
        user_id: ID пользователя
        listing: Объявление для отправки
        use_ai_valuation: Если True, будет выполнена ИИ-оценка (по умолчанию False - без оценки)
    """
    try:
        # ИИ-оценка выполняется ТОЛЬКО если явно запрошена
        ai_valuation = None
        if use_ai_valuation and AI_VALUATOR_AVAILABLE and valuate_listing:
            try:
                # Задержка между запросами к ИИ (чтобы не превысить rate limit)
                # Groq: 30 запросов/минуту = ~2 секунды между запросами
                await asyncio.sleep(2)

                # Таймаут для ИИ-оценки (максимум 20 секунд - включает инспекцию страницы)
                ai_valuation = await asyncio.wait_for(valuate_listing(listing), timeout=20.0)
                if ai_valuation:
                    log_info(
                        "ai",
                        f"ИИ-оценка получена для {listing.id}: ${ai_valuation.get('fair_price_usd', 0):,}",
                    )
            except asyncio.TimeoutError:
                log_warning("ai", f"Таймаут ИИ-оценки для {listing.id}")
            except Exception as e:
                log_error("ai", f"Ошибка ИИ-оценки для {listing.id}", e)

        message_text = format_listing_message(listing, ai_valuation)
        photos = listing.photos

        # Создаем кнопку "ИИ Оценка квартиры" если ИИ доступен, оценка не была выполнена и объявление еще не оценено
        reply_markup = None
        if not use_ai_valuation and AI_VALUATOR_AVAILABLE and valuate_listing:
            # Проверяем, было ли объявление уже оценено через ИИ
            if not await is_listing_ai_valuated(user_id, listing.id):
                # Используем только listing_id в callback_data (Telegram ограничивает до 64 байт)
                builder = InlineKeyboardBuilder()
                builder.button(text="🤖 ИИ Оценка квартиры", callback_data=f"ai_val_{listing.id}")
                builder.adjust(1)
                reply_markup = builder.as_markup()

        if photos:
            # Отправляем медиагруппу с фотографиями
            media_group = []
            for i, photo_url in enumerate(photos[:MAX_PHOTOS]):
                if i == 0:
                    # Первое фото с подписью и кнопкой
                    media_group.append(
                        InputMediaPhoto(
                            media=photo_url, caption=message_text, parse_mode=ParseMode.HTML
                        )
                    )
                else:
                    media_group.append(InputMediaPhoto(media=photo_url))

            # Отправляем медиагруппу через безопасную обертку
            sent_messages = await safe_send_media_group(bot=bot, chat_id=user_id, media=media_group)
            
            # Проверяем успешность отправки
            if sent_messages is None or len(sent_messages) == 0:
                log_error(
                    "notification",
                    f"Не удалось отправить медиагруппу для объявления {listing.id} пользователю {user_id}",
                )
                return False

            # Если есть кнопка ИИ-оценки, отправляем её отдельным сообщением после медиагруппы
            # (Telegram не поддерживает кнопки в медиагруппе напрямую)
            # Кнопка ИИ-оценки не критична, продолжаем даже если не отправилась
            if reply_markup:
                ai_button_msg = await safe_send_message(
                    bot=bot,
                    chat_id=user_id,
                    text="🤖 <b>Хотите получить ИИ-оценку этой квартиры?</b>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup,
                )
                if ai_button_msg is None:
                    log_warning("notification", f"Не удалось отправить кнопку ИИ-оценки для {listing.id}")
            
            # Медиагруппа отправлена успешно - отмечаем как отправленное
            await mark_listing_sent_to_user(user_id, listing.id)
            await mark_listing_sent(listing.to_dict())  # Глобальная дедупликация
            log_info(
                "notification", f"Отправлено пользователю {user_id}: {listing.id} ({listing.source})"
            )
            return True
        else:
            # Без фотографий - просто текст с кнопкой
            sent_message = await safe_send_message(
                bot=bot,
                chat_id=user_id,
                text=message_text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=False,
                reply_markup=reply_markup,
            )
            
            # Проверяем успешность отправки
            if sent_message is None:
                log_error(
                    "notification",
                    f"Не удалось отправить сообщение для объявления {listing.id} пользователю {user_id}",
                )
                return False

            # Сообщение отправлено успешно - отмечаем как отправленное
            await mark_listing_sent_to_user(user_id, listing.id)
            await mark_listing_sent(listing.to_dict())  # Глобальная дедупликация
            log_info(
                "notification", f"Отправлено пользователю {user_id}: {listing.id} ({listing.source})"
            )
            return True

    except Exception as e:
        log_error(
            "notification", f"Ошибка отправки объявления {listing.id} пользователю {user_id}", e
        )
        return False


async def show_actions_menu(
    bot: Bot, user_id: int, listings_count: int, mode: str = "Обычный режим"
):
    """Показывает меню действий после отправки объявлений"""
    from aiogram.utils.keyboard import InlineKeyboardBuilder

    builder = InlineKeyboardBuilder()

    # Если это ИИ-режим, показываем меню выбора режима + сброс фильтров
    if mode == "ИИ-режим":
        builder.button(text="🔍 Обычный парсер", callback_data="check_now_from_ai")
        builder.button(text="🤖 ИИ-мод", callback_data="check_now_ai")
        builder.button(text="🔄 Сбросить фильтры и начать заново", callback_data="reset_filters")
    else:
        # Обычный режим - стандартное меню
        builder.button(text="🔍 Проверить сейчас", callback_data="check_now")
        builder.button(text="🤖 ИИ-анализ", callback_data="check_now_ai")
        builder.button(text="⚙️ Изменить фильтры", callback_data="setup_filters")
        builder.button(text="📊 Статистика", callback_data="show_stats")

    # Принудительно размещаем по 1 кнопке в ряду
    builder.adjust(1)

    mode_text = "ИИ-мод" if mode == "ИИ-режим" else "Обычный парсер"
    if listings_count > 0:
        if mode == "ИИ-режим":
            message_text = (
                f"✅ <b>ИИ выбрал {listings_count} лучших вариантов</b>\n\n"
                f"<b>Что дальше?</b>\n"
                f"• 🔍 Обычный парсер - получить все найденные объявления\n"
                f"• 🤖 ИИ-мод - снова выбрать лучшие варианты\n"
                f"• 🔄 Сбросить фильтры - начать настройку заново"
            )
        else:
            message_text = (
                f"✅ <b>Готово!</b>\n\n"
                f"📨 Отправлено объявлений: <b>{listings_count}</b>\n"
                f"🤖 Режим: <b>{mode_text}</b>\n\n"
                f"<b>Что дальше?</b>\n"
                f"• 🔍 Проверить сейчас - найти все новые объявления\n"
                f"• 🤖 ИИ-анализ - выбрать лучшие варианты\n"
                f"• ⚙️ Изменить фильтры - настроить поиск\n"
                f"• 📊 Статистика - посмотреть историю"
            )
    else:
        if mode == "ИИ-режим":
            message_text = (
                f"📭 <b>ИИ не нашел подходящих вариантов</b>\n\n"
                f"<b>Что дальше?</b>\n"
                f"• 🔍 Обычный парсер - получить все найденные объявления\n"
                f"• 🤖 ИИ-мод - попробовать снова\n"
                f"• 🔄 Сбросить фильтры - начать настройку заново"
            )
        else:
            message_text = (
                f"📭 <b>Новых объявлений нет</b>\n\n"
                f"Все подходящие объявления уже были отправлены ранее.\n\n"
                f"<b>Что дальше?</b>\n"
                f"• 🔍 Проверить сейчас - найти все новые объявления\n"
                f"• 🤖 ИИ-анализ - выбрать лучшие варианты\n"
                f"• ⚙️ Изменить фильтры - настроить поиск\n"
                f"• 📊 Статистика - посмотреть историю"
            )

    # Используем безопасную обертку - ошибки уже обрабатываются внутри
    await safe_send_message(
        bot=bot,
        chat_id=user_id,
        text=message_text,
        parse_mode=ParseMode.HTML,
        reply_markup=builder.as_markup(),
    )


async def show_no_listings_message(bot: Bot, user_id: int, status_msg: Optional[Message] = None):
    """Показывает сообщение об отсутствии объявлений с предложением обновить фильтры"""
    from aiogram.utils.keyboard import InlineKeyboardBuilder

    message_text = (
        "📭 <b>Объявлений не найдено</b>\n\n"
        "Не найдено объявлений, соответствующих вашим фильтрам.\n\n"
        "💡 <b>Попробуйте изменить фильтры:</b>\n"
        "• Расширьте диапазон цен\n"
        "• Измените количество комнат\n"
        "• Выберите другой город\n\n"
        "Используйте кнопку ниже для изменения фильтров."
    )

    builder = InlineKeyboardBuilder()
    builder.button(text="⚙️ Изменить фильтры", callback_data="setup_filters")
    builder.adjust(1)

    # Используем безопасные обертки
    if status_msg:
        # Пытаемся отредактировать существующее сообщение
        result = await safe_edit_message_text(
            bot=bot,
            chat_id=status_msg.chat.id,
            message_id=status_msg.message_id,
            text=message_text,
            parse_mode=ParseMode.HTML,
            reply_markup=builder.as_markup(),
        )
        # Если не удалось отредактировать, отправляем новое сообщение
        if not result:
            await safe_send_message(
                bot=bot,
                chat_id=user_id,
                text=message_text,
                parse_mode=ParseMode.HTML,
                reply_markup=builder.as_markup(),
            )
    else:
        await safe_send_message(
            bot=bot,
            chat_id=user_id,
            text=message_text,
            parse_mode=ParseMode.HTML,
            reply_markup=builder.as_markup(),
        )


async def show_listings_list(bot: Bot, user_id: int, listings: List[Listing], status_msg: Message):
    """Показывает список всех найденных объявлений с краткой информацией"""

    if not listings:
        await status_msg.edit_text("📭 <b>Объявлений не найдено</b>", parse_mode=ParseMode.HTML)
        await show_actions_menu(bot, user_id, 0, "ИИ-режим")
        return

    # Ограничиваем до 20 объявлений для удобства
    listings_to_show = listings[:20]

    # Формируем список объявлений
    listings_text = f"✅ <b>Найдено {len(listings)} объявлений</b>\n\n"
    listings_text += f"<b>Список всех вариантов:</b>\n\n"

    for i, listing in enumerate(listings_to_show, 1):
        rooms_text = f"{listing.rooms}-комн." if listing.rooms > 0 else "?"
        area_text = f"{listing.area} м²" if listing.area > 0 else "?"
        price_text = listing.price_formatted

        # Краткая информация
        listing_info = f"<b>{i}.</b> {rooms_text}, {area_text} - {price_text}\n"
        listing_info += f"📍 {listing.address[:50]}\n\n"

        # Если текст слишком длинный, обрезаем
        if len(listings_text) + len(listing_info) > 3500:
            listings_text += f"\n... и еще {len(listings) - i + 1} объявлений"
            break

        listings_text += listing_info

    builder = InlineKeyboardBuilder()
    builder.button(text="📤 Отправить все", callback_data="send_all_listings")
    builder.button(text="❌ Отмена", callback_data="cancel_listings")

    # Принудительно размещаем по 1 кнопке в ряду
    builder.adjust(1)

    try:
        await status_msg.edit_text(
            listings_text, parse_mode=ParseMode.HTML, reply_markup=builder.as_markup()
        )
    except Exception as e:
        # Если сообщение слишком длинное, разбиваем на части
        log_warning("bot", f"Сообщение слишком длинное, отправляю сокращенную версию: {e}")
        short_text = f"✅ <b>Найдено {len(listings)} объявлений</b>\n\n"
        short_text += (
            f"Показано первых {min(10, len(listings_to_show))} из {len(listings)} объявлений.\n\n"
        )
        short_text += f"Нажмите 'Отправить все' чтобы получить все объявления."
        await status_msg.edit_text(
            short_text, parse_mode=ParseMode.HTML, reply_markup=builder.as_markup()
        )
