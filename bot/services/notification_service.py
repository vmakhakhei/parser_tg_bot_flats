"""
Сервис для отправки уведомлений пользователям
"""

import asyncio
import json
import logging
from time import time
from typing import Optional, Dict, Any, List

from aiogram import Bot
from aiogram.types import InputMediaPhoto, Message
from aiogram.enums import ParseMode
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramRetryAfter

from scrapers.base import Listing
from database import (
    mark_listing_sent,
    mark_listing_sent_to_user,
    is_listing_ai_valuated,
    is_ad_sent_to_user,
    mark_ad_sent_to_user,
)
from config import MAX_PHOTOS
from config.constants import (
    MAX_GROUPS_IN_SUMMARY,
    MAX_LISTINGS_PER_GROUP_PREVIEW,
    DELIVERY_MODE_BRIEF,
    DELIVERY_MODE_FULL,
    DELIVERY_MODE_DEFAULT,
)
from error_logger import log_info, log_warning, log_error
from bot.services.telegram_api import (
    safe_send_message,
    safe_send_media_group,
    safe_edit_message_text,
)

logger = logging.getLogger(__name__)

# Per-user rate limit (soft lock): user_id -> unlock_timestamp
USER_SEND_LOCKS: Dict[int, float] = {}

# In-memory хранилище для delivery_mode пользователей
USER_DELIVERY_MODES: Dict[int, str] = {}

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
    # Адрес должен браться ТОЛЬКО из listing.address, без fallback'ов
    # Временно добавляем защиту: если адрес None - это ошибка данных
    assert listing.address is not None, f"listing.address is None for listing.id={listing.id}"
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
    
    Returns:
        True если объявление было отправлено, False если уже было отправлено ранее или произошла ошибка
    """
    try:
        # ДИАГНОСТИЧЕСКИЙ ЛОГ: логируем перед отправкой
        log_info(
            "notification",
            f"[NOTIFY] user={user_id} ad_id={listing.id} address={listing.address}"
        )
        
        # Идемпотентная проверка: если объявление уже было отправлено этому пользователю - не отправляем
        if await is_ad_sent_to_user(user_id, listing.id):
            log_info(
                "notification",
                f"Объявление {listing.id} уже было отправлено пользователю {user_id}, пропускаем"
            )
            return False
        
        # Проверка per-user rate limit (soft lock)
        now = time()
        unlock_at = USER_SEND_LOCKS.get(user_id)
        if unlock_at and now < unlock_at:
            log_info(
                "notification",
                f"Пользователь {user_id} на паузе до {unlock_at:.1f} (осталось {unlock_at - now:.1f} сек), пропуск отправки объявления {listing.id}"
            )
            return False
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

            # Отправляем медиагруппу напрямую для перехвата TelegramRetryAfter
            # Обрабатываем TelegramRetryAfter для установки паузы
            try:
                sent_messages = await bot.send_media_group(chat_id=user_id, media=media_group)
            except TelegramRetryAfter as e:
                retry_after = int(e.retry_after)
                USER_SEND_LOCKS[user_id] = time() + retry_after
                log_warning(
                    "notification",
                    f"Flood control для пользователя {user_id}, пауза {retry_after} сек. Объявление {listing.id} НЕ будет помечено как отправленное."
                )
                # ВАЖНО: НЕ mark_ad_sent_to_user - объявление не было отправлено
                return False
            except Exception as e:
                # Для других ошибок используем безопасную обертку
                log_warning("notification", f"Ошибка при прямой отправке медиагруппы, используем safe_send_media_group: {e}")
                sent_messages = await safe_send_media_group(bot=bot, chat_id=user_id, media=media_group)
            
            # Проверяем успешность отправки
            if sent_messages is None or len(sent_messages) == 0:
                log_error(
                    "notification",
                    f"Не удалось отправить медиагруппу для объявления {listing.id} пользователю {user_id}",
                )
                return False

            # Минимальная задержка между сообщениями для снижения flood-risk
            await asyncio.sleep(1.2)

            # Если есть кнопка ИИ-оценки, отправляем её отдельным сообщением после медиагруппы
            # (Telegram не поддерживает кнопки в медиагруппе напрямую)
            # Кнопка ИИ-оценки не критична, продолжаем даже если не отправилась
            if reply_markup:
                try:
                    ai_button_msg = await safe_send_message(
                        bot=bot,
                        chat_id=user_id,
                        text="🤖 <b>Хотите получить ИИ-оценку этой квартиры?</b>",
                        parse_mode=ParseMode.HTML,
                        reply_markup=reply_markup,
                    )
                    if ai_button_msg is None:
                        log_warning("notification", f"Не удалось отправить кнопку ИИ-оценки для {listing.id}")
                except TelegramRetryAfter as e:
                    retry_after = int(e.retry_after)
                    USER_SEND_LOCKS[user_id] = time() + retry_after
                    log_warning(
                        "notification",
                        f"Flood control для пользователя {user_id} при отправке кнопки ИИ-оценки, пауза {retry_after} сек"
                    )
                    # Кнопка не критична, продолжаем - медиагруппа уже отправлена
            
            # Медиагруппа отправлена успешно - отмечаем как отправленное
            await mark_listing_sent_to_user(user_id, listing.id)
            await mark_listing_sent(listing.to_dict())  # Глобальная дедупликация
            await mark_ad_sent_to_user(user_id, listing.id)  # Идемпотентная запись
            log_info(
                "notification", f"Отправлено пользователю {user_id}: {listing.id} ({listing.source})"
            )
            return True
        else:
            # Без фотографий - просто текст с кнопкой
            # Отправляем напрямую для перехвата TelegramRetryAfter
            try:
                sent_message = await bot.send_message(
                    chat_id=user_id,
                    text=message_text,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=False,
                    reply_markup=reply_markup,
                )
            except TelegramRetryAfter as e:
                retry_after = int(e.retry_after)
                USER_SEND_LOCKS[user_id] = time() + retry_after
                log_warning(
                    "notification",
                    f"Flood control для пользователя {user_id}, пауза {retry_after} сек. Объявление {listing.id} НЕ будет помечено как отправленное."
                )
                # ВАЖНО: НЕ mark_ad_sent_to_user - объявление не было отправлено
                return False
            except Exception as e:
                # Для других ошибок используем безопасную обертку
                log_warning("notification", f"Ошибка при прямой отправке сообщения, используем safe_send_message: {e}")
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

            # Минимальная задержка между сообщениями для снижения flood-risk
            await asyncio.sleep(1.2)

            # Сообщение отправлено успешно - отмечаем как отправленное
            await mark_listing_sent_to_user(user_id, listing.id)
            await mark_listing_sent(listing.to_dict())  # Глобальная дедупликация
            await mark_ad_sent_to_user(user_id, listing.id)  # Идемпотентная запись
            log_info(
                "notification", f"Отправлено пользователю {user_id}: {listing.id} ({listing.source})"
            )
            return True

    except Exception as e:
        log_error(
            "notification", f"Ошибка отправки объявления {listing.id} пользователю {user_id}", e
        )
        return False


async def send_grouped_listings_to_user(bot: Bot, user_id: int, listings: List[Listing]) -> bool:
    """
    Отправляет группированное сообщение о нескольких объявлениях из одного дома.
    
    Args:
        bot: Экземпляр бота
        user_id: ID пользователя
        listings: Список объявлений для группировки (минимум 2)
    
    Returns:
        True если сообщение было отправлено успешно, False в случае ошибки
    """
    if not listings or len(listings) < 2:
        log_warning("notification", f"send_grouped_listings_to_user вызвана с {len(listings) if listings else 0} объявлениями, требуется минимум 2")
        return False
    
    try:
        # ШАГ 2: ОБЩИЙ ЛОГ АНАЛИЗА ГРУППЫ
        from collections import defaultdict
        
        vendors = set()
        for l in listings:
            try:
                raw_json = getattr(l, 'raw_json', None)
                if raw_json:
                    if isinstance(raw_json, dict):
                        vendor = raw_json.get("agency") or raw_json.get("seller") or "UNKNOWN"
                    elif isinstance(raw_json, str):
                        try:
                            raw_data = json.loads(raw_json)
                            vendor = raw_data.get("agency") or raw_data.get("seller") or "UNKNOWN"
                        except:
                            vendor = "UNKNOWN"
                    else:
                        vendor = "UNKNOWN"
                else:
                    vendor = "UNKNOWN"
            except Exception:
                vendor = "UNKNOWN"
            vendors.add(vendor)
        
        logger.info(
            "[GROUP_ANALYSIS] address=%s total_listings=%d vendors=%s",
            listings[0].address,
            len(listings),
            list(vendors),
        )
        
        # ШАГ 3: ДЕТАЛИЗАЦИЯ ПО КАЖДОМУ АГЕНТСТВУ
        vendors_map = defaultdict(list)
        
        for l in listings:
            try:
                raw_json = getattr(l, 'raw_json', None)
                if raw_json:
                    if isinstance(raw_json, dict):
                        vendor = raw_json.get("agency") or raw_json.get("seller") or "UNKNOWN"
                    elif isinstance(raw_json, str):
                        try:
                            raw_data = json.loads(raw_json)
                            vendor = raw_data.get("agency") or raw_data.get("seller") or "UNKNOWN"
                        except:
                            vendor = "UNKNOWN"
                    else:
                        vendor = "UNKNOWN"
                else:
                    vendor = "UNKNOWN"
            except Exception:
                vendor = "UNKNOWN"
            
            vendors_map[vendor].append(l)
        
        for vendor, items in vendors_map.items():
            prices = sorted({i.price_usd for i in items if i.price_usd})
            areas = sorted({i.area for i in items if i.area})
            
            logger.info(
                "[GROUP_VENDOR] address=%s vendor=%s count=%d prices=%s areas=%s",
                listings[0].address,
                vendor,
                len(items),
                prices,
                areas,
            )
        
        # Сортируем объявления по цене (от меньшей к большей)
        sorted_listings = sorted(listings, key=lambda x: x.price_usd or 0)
        
        # Извлекаем цены для диапазона
        prices = [l.price_usd for l in sorted_listings if l.price_usd]
        if not prices:
            log_warning("notification", f"Нет цен в группированных объявлениях для пользователя {user_id}")
            return False
        
        min_price = min(prices)
        max_price = max(prices)
        
        # Берем адрес из первого объявления
        address = sorted_listings[0].address
        
        # Вычисляем диапазон комнат
        rooms = sorted({l.rooms for l in sorted_listings if l.rooms})
        if len(rooms) > 1:
            rooms_text = f"{rooms[0]}–{rooms[-1]} комнаты"
        elif len(rooms) == 1:
            rooms_text = f"{rooms[0]} комната"
        else:
            rooms_text = "комнаты не указаны"
        
        # Извлекаем топ-3 продавцов с количеством объявлений
        vendor_counts = {}
        for l in sorted_listings:
            vendor = None
            try:
                raw_json = getattr(l, 'raw_json', None)
                if raw_json:
                    if isinstance(raw_json, dict):
                        vendor = raw_json.get('agency') or raw_json.get('seller')
                    elif isinstance(raw_json, str):
                        try:
                            raw_data = json.loads(raw_json)
                            vendor = raw_data.get('agency') or raw_data.get('seller')
                        except:
                            pass
            except:
                pass
            
            vendor_key = vendor or "Частник"
            vendor_counts[vendor_key] = vendor_counts.get(vendor_key, 0) + 1
        
        # Сортируем продавцов по количеству объявлений (топ-3)
        top_vendors = sorted(vendor_counts.items(), key=lambda x: x[1], reverse=True)[:3]
        
        # Формируем текст сообщения
        text_lines = [
            f"🏢 <b>{len(sorted_listings)} квартир в одном доме</b>",
            f"📍 {address}",
            f"🛏 {rooms_text}",
            f"💰 ${min_price:,} – ${max_price:,}".replace(",", " "),
            ""
        ]
        
        # Добавляем топ продавцов если есть
        if top_vendors:
            vendors_text = ", ".join([f"{name} ({cnt})" for name, cnt in top_vendors])
            text_lines.append(f"📣 Топ продавцы: {vendors_text}")
            text_lines.append("")
        
        # Добавляем первые 5 объявлений
        for i, listing in enumerate(sorted_listings[:5], start=1):
            area_text = f"{listing.area} м²" if listing.area > 0 else "—"
            price_text = f"${listing.price_usd:,}".replace(",", " ") if listing.price_usd else "—"
            text_lines.append(f"{i}. {price_text} — {area_text} м²")
        
        # Если объявлений больше 5, добавляем информацию об остальных
        if len(sorted_listings) > 5:
            text_lines.append(f"\n…и ещё {len(sorted_listings) - 5}")
        
        # Добавляем призыв к действию
        text_lines.append(f"\n[Показать все варианты]")
        
        text = "\n".join(text_lines)
        
        # Отправляем сообщение
        try:
            sent_message = await bot.send_message(
                chat_id=user_id,
                text=text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=False,
            )
        except TelegramRetryAfter as e:
            retry_after = int(e.retry_after)
            USER_SEND_LOCKS[user_id] = time() + retry_after
            log_warning(
                "notification",
                f"Flood control для пользователя {user_id}, пауза {retry_after} сек. Группированные объявления НЕ будут помечены как отправленные."
            )
            return False
        except Exception as e:
            log_warning("notification", f"Ошибка при прямой отправке группированного сообщения, используем safe_send_message: {e}")
            sent_message = await safe_send_message(
                bot=bot,
                chat_id=user_id,
                text=text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=False,
            )
        
        # Проверяем успешность отправки
        if sent_message is None:
            log_error(
                "notification",
                f"Не удалось отправить группированное сообщение пользователю {user_id}"
            )
            return False
        
        # ДЕТАЛЬНОЕ ЛОГИРОВАНИЕ: логируем каждое объявление в группе с vendor
        group_key = f"{address}|{len(sorted_listings)}"
        members_info = []
        for listing in sorted_listings:
            vendor = "UNKNOWN"
            try:
                raw_json = getattr(listing, 'raw_json', None)
                if raw_json:
                    if isinstance(raw_json, dict):
                        vendor = raw_json.get("agency") or raw_json.get("seller") or "UNKNOWN"
                    elif isinstance(raw_json, str):
                        try:
                            raw_data = json.loads(raw_json)
                            vendor = raw_data.get("agency") or raw_data.get("seller") or "UNKNOWN"
                        except:
                            vendor = "UNKNOWN"
            except Exception:
                vendor = "UNKNOWN"
            
            members_info.append((listing.id, vendor, listing.price_usd or 0, listing.title[:50] if listing.title else ""))
        
        logger.info(
            "[group_debug] group_key=%s members=%s",
            group_key,
            members_info
        )
        
        # КРИТИЧНО: Помечаем КАЖДОЕ объявление как отправленное
        # Иначе будут повторные уведомления
        for listing in sorted_listings:
            await mark_listing_sent_to_user(user_id, listing.id)
            await mark_listing_sent(listing.to_dict())  # Глобальная дедупликация
            await mark_ad_sent_to_user(user_id, listing.id)  # Идемпотентная запись
        
        log_info(
            "notification",
            f"Отправлено группированное сообщение пользователю {user_id}: {len(sorted_listings)} объявлений ({sorted_listings[0].source})"
        )
        
        # Минимальная задержка между сообщениями для снижения flood-risk
        await asyncio.sleep(1.2)
        
        return True
        
    except Exception as e:
        log_error(
            "notification",
            f"Ошибка отправки группированных объявлений пользователю {user_id}",
            e
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


def score_group(group: List[Listing]) -> float:
    """
    Вычисляет score для группы объявлений.
    
    Чем выше score, тем лучше группа (больше вариантов, ниже цена, больше площадь).
    
    Args:
        group: Список Listing объектов в группе
    
    Returns:
        Числовой score (чем выше, тем лучше)
    """
    prices = [l.price_usd for l in group if l.price_usd]
    areas = [l.area for l in group if l.area]
    
    # Бонус за количество вариантов (каждый вариант = +2 балла)
    count_score = len(group) * 2
    
    # Бонус за низкую цену (минимальная цена, деленная на 10k, с минусом для сортировки по убыванию)
    price_score = -min(prices) / 10_000 if prices else 0
    
    # Бонус за большую площадь (средняя площадь, деленная на 10)
    area_score = (sum(areas) / len(areas)) / 10 if areas else 0
    
    return count_score + price_score + area_score


async def notify_users_about_new_apartments_summary(new_listings: List[Listing]) -> None:
    """
    Отправляет summary-уведомления пользователям о новых объявлениях.
    
    Для пользователей с delivery_mode="brief" отправляет одно summary-сообщение.
    Для пользователей с delivery_mode="full" отправляет полные уведомления.
    
    Args:
        new_listings: Список Listing объектов - реально новых объявлений (уже в БД)
    """
    if not new_listings:
        log_info("notification", "[SUMMARY] нет новых объявлений для уведомлений")
        return
    
    try:
        from database import get_user_filters
        from bot.services.search_service import matches_user_filters, validate_user_filters
        from bot.services.ai_service import check_new_listings_ai_mode
        from aiogram import Bot
        from config import BOT_TOKEN
        
        if not BOT_TOKEN:
            log_warning("notification", "[SUMMARY] BOT_TOKEN не настроен, уведомления отключены")
            return
        
        log_info("notification", f"[SUMMARY] начинаю обработку {len(new_listings)} новых объявлений")
        
        # Получаем активных пользователей
        users = await get_active_users()
        if not users:
            log_info("notification", "[SUMMARY] нет активных пользователей")
            return
        
        log_info("notification", f"[SUMMARY] найдено {len(users)} активных пользователей")
        
        # Создаем бот
        bot = Bot(token=BOT_TOKEN)
        try:
            listings = new_listings
            
            # Для каждого пользователя проверяем объявления по его фильтрам
            for user_id in users:
                try:
                    user_filters = await get_user_filters(user_id)
                    if not user_filters:
                        continue
                    
                    # Проверяем валидность фильтров
                    is_valid, error_msg = validate_user_filters(user_filters)
                    if not is_valid:
                        continue
                    
                    # Применяем фильтры пользователя к новым объявлениям
                    filtered_listings = []
                    for listing in listings:
                        # Проверяем соответствие фильтрам пользователя
                        if matches_user_filters(listing, user_filters, user_id=user_id, log_details=False):
                            filtered_listings.append(listing)
                    
                    if not filtered_listings:
                        continue
                    
                    # Получаем delivery_mode пользователя (по умолчанию "brief")
                    delivery_mode = USER_DELIVERY_MODES.get(user_id, DELIVERY_MODE_DEFAULT)
                    
                    if delivery_mode == DELIVERY_MODE_FULL:
                        # Полный режим - отправляем как раньше
                        if user_filters.get("ai_mode"):
                            await check_new_listings_ai_mode(bot, user_id, user_filters, filtered_listings)
                        else:
                            groups = group_similar_listings(filtered_listings)
                            for group in groups:
                                if len(group) == 1:
                                    await send_listing_to_user(bot, user_id, group[0], use_ai_valuation=False)
                                else:
                                    await send_grouped_listings_to_user(bot, user_id, group)
                        continue
                    
                    # Brief режим - отправляем summary
                    await send_summary_message(bot, user_id, filtered_listings)
                    
                except Exception as e:
                    log_error("notification", f"[SUMMARY] ошибка обработки пользователя {user_id}: {e}")
                    continue
            
            log_info("notification", "[SUMMARY] обработка завершена")
            
        finally:
            await bot.session.close()
        
        # КРИТИЧНО: Гарантируем ОДНО summary-сообщение
        # Никаких других сообщений в этом запуске
        return
        
    except ImportError as e:
        log_error("notification", f"[SUMMARY] не удалось импортировать необходимые модули: {e}")
    except Exception as e:
        log_error("notification", f"[SUMMARY] ошибка при отправке summary-уведомлений: {e}")
        import traceback
        traceback.print_exc()


async def send_summary_message(bot: Bot, user_id: int, apartments: List[Listing]) -> None:
    """
    Отправляет summary-сообщение пользователю с группировкой по адресам.
    
    Args:
        bot: Экземпляр бота
        user_id: ID пользователя
        apartments: Список Listing объектов
    """
    try:
        # Группируем объявления по адресу
        groups = group_similar_listings(apartments)
        
        if not groups:
            return
        
        # Сортируем группы по score (лучшие первыми)
        groups = sorted(
            groups,
            key=score_group,
            reverse=True
        )[:MAX_GROUPS_IN_SUMMARY]
        
        # Формируем текст сообщения
        text = "🏙 Найдено подходящих квартир:\n\n"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        
        for idx, group in enumerate(groups, 1):
            address = group[0].address
            prices = [l.price_usd for l in group if l.price_usd]
            
            if not prices:
                continue
            
            min_price = min(prices)
            max_price = max(prices)
            
            # Форматируем цены с пробелами вместо запятых
            min_price_formatted = f"${min_price:,}".replace(",", " ")
            max_price_formatted = f"${max_price:,}".replace(",", " ")
            
            text += (
                f"🏢 {address}\n"
                f"   • {len(group)} вариантов\n"
                f"   • 💰 {min_price_formatted} – {max_price_formatted}\n\n"
            )
            
            # Создаем callback_data с hash адреса и offset=0 для первой страницы
            house_hash = str(hash(address))
            callback_data = f"show_house|{house_hash}|0"
            
            # Ограничиваем длину текста кнопки
            button_text = f"Показать варианты · {address}"
            if len(button_text) > 60:
                button_text = f"Показать варианты · {address[:40]}..."
            
            keyboard.inline_keyboard.append([
                InlineKeyboardButton(
                    text=button_text,
                    callback_data=callback_data
                )
            ])
        
        # Отправляем сообщение
        await safe_send_message(
            bot=bot,
            chat_id=user_id,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard
        )
        
        log_info("notification", f"[SUMMARY] отправлено summary пользователю {user_id}: {len(groups)} групп")
        
    except Exception as e:
        log_error("notification", f"[SUMMARY] ошибка отправки summary пользователю {user_id}: {e}")


async def get_listings_for_house_hash(house_hash: str) -> List[Listing]:
    """
    Получает объявления по hash адреса.
    
    Args:
        house_hash: Hash адреса (строка)
    
    Returns:
        Список Listing объектов с соответствующим адресом
    """
    try:
        from database_turso import build_dynamic_query
        
        # Получаем все недавние объявления из БД
        all_apartments = await build_dynamic_query(
            is_active=True,
            limit=1000  # Достаточно большое число для получения всех недавних
        )
        
        # Фильтруем по hash адреса
        listings = []
        for a in all_apartments:
            listing = apartment_dict_to_listing(a)
            if listing and listing.address:
                if str(hash(listing.address)) == house_hash:
                    listings.append(listing)
        
        return listings
        
    except Exception as e:
        log_error("notification", f"[SUMMARY] ошибка получения объявлений по hash {house_hash}: {e}")
        return []


async def send_grouped_listings_with_pagination(
    bot: Bot,
    user_id: int,
    listings: List[Listing],
    offset: int = 0
) -> None:
    """
    Отправляет группированные объявления с пагинацией.
    
    Args:
        bot: Экземпляр бота
        user_id: ID пользователя
        listings: Список Listing объектов для показа
        offset: Смещение для пагинации (по умолчанию 0)
    """
    try:
        if not listings:
            return
        
        # Получаем chunk объявлений для текущей страницы
        chunk = listings[offset:offset + MAX_LISTINGS_PER_GROUP_PREVIEW]
        
        if not chunk:
            return
        
        address = chunk[0].address
        
        # Формируем текст сообщения
        text = f"🏢 <b>{address}</b>\n\n"
        
        for listing in chunk:
            price_text = f"${listing.price_usd:,}".replace(",", " ") if listing.price_usd else "—"
            rooms_text = f"{listing.rooms}к" if listing.rooms else "—"
            area_text = f"{listing.area} м²" if listing.area else "—"
            
            text += f"• {price_text} — {rooms_text} — {area_text}\n"
        
        # Создаем клавиатуру с кнопкой "Показать ещё" если есть еще объявления
        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        
        if offset + MAX_LISTINGS_PER_GROUP_PREVIEW < len(listings):
            house_hash = str(hash(address))
            next_offset = offset + MAX_LISTINGS_PER_GROUP_PREVIEW
            callback_data = f"show_house|{house_hash}|{next_offset}"
            
            keyboard.inline_keyboard.append([
                InlineKeyboardButton(
                    text="Показать ещё",
                    callback_data=callback_data
                )
            ])
        
        # Отправляем сообщение
        await safe_send_message(
            bot=bot,
            chat_id=user_id,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard if keyboard.inline_keyboard else None
        )
        
        # Помечаем объявления как отправленные
        for listing in chunk:
            await mark_ad_sent_to_user(user_id, listing.id)
        
        log_info("notification", f"[PAGINATION] отправлено {len(chunk)} объявлений пользователю {user_id}, offset={offset}")
        
    except Exception as e:
        log_error("notification", f"[PAGINATION] ошибка отправки объявлений с пагинацией пользователю {user_id}: {e}")
