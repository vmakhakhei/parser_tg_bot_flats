"""
from ai_valuator import valuate_listing, select_best_listings
from bot.services.search_service import matches_user_filters
from bot.services.notification_service import show_actions_menu, show_no_listings_message

Сервис для работы с ИИ-оценкой объявлений
"""

import logging
import time
from typing import List, Dict, Any, Optional

from aiogram import Bot
from aiogram.types import Message
from aiogram.enums import ParseMode

from scrapers.base import Listing
from database import (
    get_ai_selected_listings,
    save_ai_selected_listings,
)
from error_logger import log_error, log_warning, log_info
from bot.services.telegram_api import safe_send_message, safe_edit_message_text

logger = logging.getLogger(__name__)

# ИИ-оценщик (опционально)
try:

    AI_VALUATOR_AVAILABLE = True
except ImportError:
    AI_VALUATOR_AVAILABLE = False
    valuate_listing = None
    select_best_listings = None


async def evaluate_and_compare_new_listings(
    bot: Bot,
    user_id: int,
    new_listings: List[Listing],
    previous_selected: List[Dict[str, Any]],
    user_filters: Dict[str, Any],
):
    """Оценивает новые объявления через ИИ и сравнивает с предыдущими выбранными вариантами"""
    logger.info(
        f"Оцениваю {len(new_listings)} новых объявлений и сравниваю с {len(previous_selected)} предыдущими"
    )

    # Отправляем уведомление пользователю через безопасную обертку
    status_msg = await safe_send_message(
        bot=bot,
        chat_id=user_id,
        text=f"🔍 <b>Оценка новых объявлений</b>\n\n"
        f"Найдено {len(new_listings)} новых объявлений.\n"
        f"Оцениваю и сравниваю с предыдущими выбранными вариантами...",
        parse_mode=ParseMode.HTML,
    )

    # Оцениваем новые объявления через ИИ
    evaluated_listings = []
    for listing in new_listings[:10]:  # Ограничиваем до 10 для экономии API
        try:
            ai_valuation = await valuate_listing(listing)
            if ai_valuation:
                evaluated_listings.append({"listing": listing, "valuation": ai_valuation})
        except Exception as e:
            log_error("ai_mode", f"Ошибка оценки объявления {listing.id}", e)

    if not evaluated_listings:
        if status_msg:
            await safe_edit_message_text(
                bot=bot,
                chat_id=status_msg.chat.id,
                message_id=status_msg.message_id,
                text="⚠️ <b>Не удалось оценить новые объявления</b>\n\n"
                "Попробуйте повторить позже.",
                parse_mode=ParseMode.HTML,
            )
        return

    # Формируем сообщение с оценкой и сравнением
    results_text = "📊 <b>Оценка новых объявлений</b>\n\n"
    results_text += f"Проанализировано {len(evaluated_listings)} новых объявлений.\n"
    results_text += f"Сравнение с {len(previous_selected)} предыдущими выбранными вариантами.\n\n"
    results_text += "━━━━━━━━━━━━━━━━━━━━\n\n"

    # Сортируем по оценке (лучшие первыми)
    evaluated_listings.sort(key=lambda x: x["valuation"].get("value_score", 0), reverse=True)

    # Показываем топ-3 новых объявления с оценкой
    for i, item in enumerate(evaluated_listings[:3], 1):
        listing = item["listing"]
        valuation = item["valuation"]

        rooms_text = f"{listing.rooms}-комн." if listing.rooms > 0 else "?"
        area_text = f"{listing.area} м²" if listing.area > 0 else "?"

        price_per_sqm = ""
        if listing.area > 0 and listing.price > 0:
            price_per_sqm_usd = int(listing.price / listing.area)
            price_per_sqm = f" (${price_per_sqm_usd}/м²)"

        year_info = ""
        if listing.year_built:
            year_info = f", {listing.year_built}г"

        fair_price = valuation.get("fair_price_usd", 0)
        is_overpriced = valuation.get("is_overpriced", False)
        value_score = valuation.get("value_score", 0)
        assessment = valuation.get("assessment", "Оценка недоступна")

        results_text += f"<b>{i}. {rooms_text}, {area_text}{year_info}</b>\n"
        results_text += f"💰 {listing.price_formatted}{price_per_sqm}\n"
        results_text += f"📍 {listing.address}\n"
        results_text += f'🔗 <a href="{listing.url}">Открыть объявление</a>\n\n'

        if fair_price > 0:
            price_diff = listing.price - fair_price
            price_diff_percent = int((price_diff / fair_price) * 100) if fair_price > 0 else 0
            results_text += f"💵 <b>Справедливая цена:</b> ${fair_price:,}\n"
            if is_overpriced:
                results_text += (
                    f"⚠️ <b>Завышена на:</b> ${abs(price_diff):,} ({abs(price_diff_percent)}%)\n"
                )
            else:
                results_text += f"✅ <b>Цена справедлива</b>\n"

        results_text += f"⭐ <b>Оценка:</b> {value_score}/10\n"
        results_text += f"📋 <b>Анализ:</b> {assessment}\n\n"

        # Сравнение с предыдущими вариантами
        if previous_selected:
            results_text += f"📊 <b>Сравнение:</b> "
            if value_score >= 7:
                results_text += "Лучше большинства предыдущих вариантов\n"
            elif value_score >= 5:
                results_text += "Сопоставимо с предыдущими вариантами\n"
            else:
                results_text += "Хуже предыдущих вариантов\n"

        results_text += "━━━━━━━━━━━━━━━━━━━━\n\n"

    # Отправляем сообщение
    try:
        if status_msg:
            await status_msg.edit_text(
                results_text, parse_mode=ParseMode.HTML, disable_web_page_preview=False
            )
        else:
            await safe_send_message(
                bot=bot,
                chat_id=user_id,
                text=results_text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=False
            )
    except Exception as e:
        log_error("ai_mode", f"Ошибка отправки оценки пользователю {user_id}", e, exc_info=True)


async def check_new_listings_ai_mode(
    bot: Bot,
    user_id: int,
    user_filters: Dict[str, Any],
    all_listings: List[Listing],
    status_msg: Optional[Message] = None,
):
    """ИИ-режим: собирает все подходящие объявления, отправляет ИИ для выбора лучших"""

    logger.info(f"🤖 ИИ-режим для пользователя {user_id}")

    # Логируем фильтры пользователя
    log_info(
        "filter",
        f"[user_{user_id}] 📋 Применяю фильтры: город={user_filters.get('city')}, комнаты={user_filters.get('min_rooms')}-{user_filters.get('max_rooms')}, цена=${user_filters.get('min_price'):,}-${user_filters.get('max_price'):,}, продавец={user_filters.get('seller_type') or 'Все'}",
    )

    # Собираем ВСЕ подходящие объявления (без дедупликации)
    # ВАЖНО: НЕ проверяем is_listing_sent_to_user - берем ВСЕ подходящие объявления
    # ВАЖНО: НЕ проверяем is_duplicate_content - для ИИ-анализа нужны ВСЕ объявления, включая дубликаты
    candidate_listings = []
    filtered_out = 0

    for listing in all_listings:
        # Проверяем соответствие фильтрам пользователя
        if not matches_user_filters(listing, user_filters, user_id=user_id, log_details=True):
            filtered_out += 1
            continue

        # Добавляем ВСЕ подходящие объявления, включая уже отправленные и дубликаты
        # ИИ должен проанализировать все варианты, чтобы выбрать лучшие
        candidate_listings.append(listing)

    seller_type = user_filters.get("seller_type")
    seller_filter_text = f", фильтр продавца: {seller_type if seller_type else 'Все'}"
    logger.info(
        f"ИИ-режим: всего {len(all_listings)}, отфильтровано {filtered_out}, кандидатов для анализа {len(candidate_listings)}{seller_filter_text}"
    )

    if not candidate_listings:
        logger.info(f"Пользователю {user_id} нет новых объявлений для ИИ-анализа")
        return

    logger.info(f"Найдено {len(candidate_listings)} кандидатов для ИИ-анализа")

    # Получаем предыдущие выбранные ИИ варианты для сравнения
    previous_selected = await get_ai_selected_listings(user_id)
    has_previous_selections = len(previous_selected) > 0

    # Если есть предыдущие выборы ИИ, оцениваем новые объявления и сравниваем
    if has_previous_selections and AI_VALUATOR_AVAILABLE and valuate_listing:
        logger.info(
            f"Найдено {len(previous_selected)} предыдущих выборов ИИ, оцениваю новые объявления..."
        )
        await evaluate_and_compare_new_listings(
            bot, user_id, candidate_listings, previous_selected, user_filters
        )
        return

    # Отправляем уведомление пользователю о начале анализа (сохраняем для редактирования)
    if status_msg is None:
        try:
            # Рассчитываем примерное количество батчей для оценки времени
            total_candidates = len(candidate_listings)
            if total_candidates <= 15:
                estimated_batches_round1 = 1
            else:
                estimated_batches_round1 = (total_candidates + 11) // 12  # Округляем вверх

            # Рассчитываем примерное время обработки
            inspection_time = 7
            batch_delay = 15  # Задержка между батчами
            batch_processing_time = 3  # Время обработки одного батча
            final_comparison_time = 20

            # Время первого раунда батчей
            if estimated_batches_round1 == 1:
                round1_time = batch_processing_time
            else:
                round1_time = (
                    estimated_batches_round1 - 1
                ) * batch_delay + estimated_batches_round1 * batch_processing_time

            # Оцениваем количество дополнительных раундов
            max_results_after_round1 = estimated_batches_round1 * 2

            # Если получилось больше 12, нужен второй раунд
            additional_rounds_time = 0
            if max_results_after_round1 > 12:
                estimated_batches_round2 = (max_results_after_round1 + 11) // 12
                if estimated_batches_round2 == 1:
                    round2_time = batch_processing_time
                else:
                    round2_time = (
                        estimated_batches_round2 - 1
                    ) * batch_delay + estimated_batches_round2 * batch_processing_time
                additional_rounds_time = round2_time

                # Если и после второго раунда больше 12, нужен третий раунд (редко, но возможно)
                max_results_after_round2 = estimated_batches_round2 * 2
                if max_results_after_round2 > 12:
                    estimated_batches_round3 = (max_results_after_round2 + 11) // 12
                    if estimated_batches_round3 == 1:
                        round3_time = batch_processing_time
                    else:
                        round3_time = (
                            estimated_batches_round3 - 1
                        ) * batch_delay + estimated_batches_round3 * batch_processing_time
                    additional_rounds_time += round3_time

            estimated_time_seconds = (
                inspection_time + round1_time + additional_rounds_time + final_comparison_time
            )
            estimated_time_minutes = estimated_time_seconds // 60
            estimated_time_secs = estimated_time_seconds % 60

            if estimated_time_minutes > 0:
                time_text = f"~{estimated_time_minutes} мин {estimated_time_secs} сек"
            else:
                time_text = f"~{estimated_time_seconds} сек"

            status_msg = await safe_send_message(
                bot=bot,
                chat_id=user_id,
                text=f"🤖 <b>ИИ-анализ запущен</b>\n\n"
                f"📊 Найдено: {len(candidate_listings)} объявлений\n"
                f"📦 Будет обработано: {estimated_batches_round1} батч(ей) в первом раунде\n"
                f"⏱ Примерное время: {time_text}\n\n"
                f"⏳ Анализирую и выбираю лучшие варианты...",
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            log_warning("ai_mode", f"Не удалось отправить уведомление пользователю {user_id}: {e}")

    # Засекаем время начала анализа
    start_time = time.time()

    # Отправляем все объявления в ИИ для выбора лучших
    if AI_VALUATOR_AVAILABLE and select_best_listings:
        try:
            best_with_reasons = await select_best_listings(
                candidate_listings, user_filters, max_results=5  # Запрашиваем 5 вариантов
            )

            # Рассчитываем фактическое время обработки
            elapsed_time = time.time() - start_time
            elapsed_minutes = int(elapsed_time // 60)
            elapsed_seconds = int(elapsed_time % 60)

            if elapsed_minutes > 0:
                elapsed_text = f"{elapsed_minutes} мин {elapsed_seconds} сек"
            else:
                elapsed_text = f"{elapsed_seconds} сек"

            if best_with_reasons and len(best_with_reasons) > 0:
                logger.info(
                    f"ИИ выбрал {len(best_with_reasons)} лучших вариантов для пользователя {user_id}"
                )

                # Формируем сообщения с результатами (разбиваем на части если слишком длинные)
                TELEGRAM_MAX_LENGTH = 4000  # Оставляем запас от 4096

                # Заголовок
                header_text = f"✅ <b>ИИ выбрал {len(best_with_reasons)} лучших вариантов</b>\n\n"
                header_text += f"Из {len(candidate_listings)} объявлений проанализированы все по ссылкам и отобраны лучшие по соотношению цена-качество.\n"
                header_text += f"⏱ Время обработки: {elapsed_text}\n\n"

                # Формируем части сообщений
                messages_parts = []
                current_message = header_text

                for i, item in enumerate(best_with_reasons, 1):
                    listing = item.get("listing")
                    reason = item.get("reason", "Хорошее соотношение цена-качество")

                    if not listing:
                        logger.warning(f"Пропускаю элемент {i}: нет listing")
                        continue

                    rooms_text = f"{listing.rooms}-комн." if listing.rooms > 0 else "?"
                    area_text = f"{listing.area} м²" if listing.area > 0 else "?"

                    # Рассчитываем цену за м² для сравнения
                    price_per_sqm = ""
                    if listing.area > 0 and listing.price > 0:
                        price_per_sqm_usd = int(listing.price / listing.area)
                        price_per_sqm = f" (${price_per_sqm_usd}/м²)"

                    # Год постройки (если есть)
                    year_info = ""
                    if listing.year_built:
                        year_info = f", {listing.year_built}г"

                    # Формируем текст для варианта
                    variant_text = f"━━━━━━━━━━━━━━━━━━━━\n\n"
                    variant_text += f"<b>{i}. {rooms_text}, {area_text}{year_info}</b>\n"
                    variant_text += f"💰 {listing.price_formatted}{price_per_sqm}\n"
                    variant_text += f"📍 {listing.address}\n"
                    variant_text += f'🔗 <a href="{listing.url}">Открыть объявление</a>\n\n'

                    # Ограничиваем длину обоснования (максимум 500 символов)
                    if len(reason) > 500:
                        reason = reason[:497] + "..."

                    variant_text += f"<b>📋 Обоснование:</b>\n{reason}\n\n"

                    # Проверяем, поместится ли вариант в текущее сообщение
                    if len(current_message) + len(variant_text) > TELEGRAM_MAX_LENGTH:
                        # Сохраняем текущее сообщение и начинаем новое
                        messages_parts.append(current_message)
                        current_message = (
                            f"<b>Продолжение ({i}/{len(best_with_reasons)}):</b>\n\n{variant_text}"
                        )
                    else:
                        current_message += variant_text

                # Добавляем последнее сообщение
                if current_message.strip() != header_text.strip():
                    messages_parts.append(current_message)

                # Отправляем сообщения через безопасные обертки
                if status_msg:
                    # Первое сообщение редактируем статус
                    if messages_parts:
                        result = await safe_edit_message_text(
                            bot=bot,
                            chat_id=status_msg.chat.id,
                            message_id=status_msg.message_id,
                            text=messages_parts[0],
                            parse_mode=ParseMode.HTML,
                            disable_web_page_preview=False,
                        )
                        # Если не удалось отредактировать, отправляем новое сообщение
                        if not result and messages_parts:
                            await safe_send_message(
                                bot=bot,
                                chat_id=user_id,
                                text=messages_parts[0],
                                parse_mode=ParseMode.HTML,
                                disable_web_page_preview=False,
                            )
                        # Остальные отправляем отдельными сообщениями
                        for msg_part in messages_parts[1:]:
                            await safe_send_message(
                                bot=bot,
                                chat_id=user_id,
                                text=msg_part,
                                parse_mode=ParseMode.HTML,
                                disable_web_page_preview=False,
                            )
                else:
                    # Отправляем все сообщения отдельно
                    for msg_part in messages_parts:
                        await safe_send_message(
                            bot=bot,
                            chat_id=user_id,
                            text=msg_part,
                            parse_mode=ParseMode.HTML,
                            disable_web_page_preview=False,
                        )

                # Fallback: если не удалось отправить, отправляем сокращенную версию
                if not messages_parts or (status_msg and not messages_parts):
                    short_text = (
                        f"✅ <b>ИИ выбрал {len(best_with_reasons)} лучших вариантов</b>\n\n"
                    )
                    for i, item in enumerate(best_with_reasons[:3], 1):  # Только первые 3
                        listing = item.get("listing")
                        if listing:
                            rooms_text = f"{listing.rooms}-комн." if listing.rooms > 0 else "?"
                            area_text = f"{listing.area} м²" if listing.area > 0 else "?"
                            short_text += (
                                f"{i}. {rooms_text}, {area_text} - {listing.price_formatted}\n"
                            )
                            short_text += f'🔗 <a href="{listing.url}">Открыть</a>\n\n'
                    await safe_send_message(
                        bot=bot,
                        chat_id=user_id,
                        text=short_text,
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=False,
                    )

                # Сохраняем выбранные варианты для будущего сравнения
                await save_ai_selected_listings(user_id, best_with_reasons)

                # Показываем финальное меню действий после ИИ-анализа
                await show_actions_menu(bot, user_id, len(best_with_reasons), "ИИ-режим")

            else:
                logger.warning(f"ИИ не выбрал ни одного варианта для пользователя {user_id}")
                # ИИ не выбрал ни одного варианта - показываем сообщение с предложением изменить фильтры
                await show_no_listings_message(bot, user_id, status_msg)
        except Exception as e:
            log_error("ai_mode", f"Ошибка ИИ-анализа для пользователя {user_id}", e)
            # В ИИ-режиме НЕ отправляем объявления отдельно, только сообщение об ошибке
            error_text = "⚠️ <b>Ошибка ИИ-анализа</b>\n\nПроизошла ошибка при анализе объявлений. Попробуйте повторить позже."
            if status_msg:
                result = await safe_edit_message_text(
                    bot=bot,
                    chat_id=status_msg.chat.id,
                    message_id=status_msg.message_id,
                    text=error_text,
                    parse_mode=ParseMode.HTML,
                )
                if not result:
                    await safe_send_message(
                        bot=bot, chat_id=user_id, text=error_text, parse_mode=ParseMode.HTML
                    )
            else:
                await safe_send_message(
                    bot=bot, chat_id=user_id, text=error_text, parse_mode=ParseMode.HTML
                )
    else:
        logger.warning("ИИ-оценщик недоступен")
        # В ИИ-режиме НЕ отправляем объявления отдельно, только сообщение
        unavailable_text = "⚠️ <b>ИИ-оценщик недоступен</b>\n\nИИ-режим временно недоступен. Переключитесь на обычный режим в настройках."
        if status_msg:
            result = await safe_edit_message_text(
                bot=bot,
                chat_id=status_msg.chat.id,
                message_id=status_msg.message_id,
                text=unavailable_text,
                parse_mode=ParseMode.HTML,
            )
            if not result:
                await safe_send_message(
                    bot=bot, chat_id=user_id, text=unavailable_text, parse_mode=ParseMode.HTML
                )
        else:
            await safe_send_message(
                bot=bot, chat_id=user_id, text=unavailable_text, parse_mode=ParseMode.HTML
            )
