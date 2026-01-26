"""
Безопасные обертки для Telegram API

Обрабатывает специфичные ошибки Telegram API:
- FloodWait / RetryAfter - автоматическое ожидание и retry
- Rate limit errors - логирование без падения
- Другие ошибки API - логирование с деталями
"""

import asyncio
import logging
from typing import Optional, Any, List

from aiogram import Bot
from aiogram.types import Message, InputMediaPhoto
from aiogram.enums import ParseMode
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
    TelegramUnauthorizedError,
    TelegramForbiddenError,
)

from error_logger import log_error, log_warning, log_info

logger = logging.getLogger(__name__)


async def safe_send_message(
    bot: Bot,
    chat_id: int,
    text: str,
    parse_mode: Optional[str] = ParseMode.HTML,
    reply_markup: Optional[Any] = None,
    disable_web_page_preview: Optional[bool] = None,
    max_retries: int = 3,
) -> Optional[Message]:
    """
    Безопасная отправка сообщения с обработкой ошибок Telegram API

    Args:
        bot: Экземпляр бота
        chat_id: ID чата
        text: Текст сообщения
        parse_mode: Режим парсинга (по умолчанию HTML)
        reply_markup: Клавиатура (опционально)
        disable_web_page_preview: Отключить превью ссылок
        max_retries: Максимум попыток при ошибке

    Returns:
        Message объект или None при ошибке
    """
    for attempt in range(1, max_retries + 1):
        try:
            return await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
                disable_web_page_preview=disable_web_page_preview,
            )
        except TelegramRetryAfter as e:
            # Telegram просит подождать - ждем указанное время
            wait_time = e.retry_after
            log_warning(
                "telegram_api",
                f"Rate limit для пользователя {chat_id}: ожидание {wait_time} сек (попытка {attempt}/{max_retries})",
            )
            await asyncio.sleep(wait_time)
            # Повторяем попытку
            continue
        except TelegramForbiddenError:
            # Пользователь заблокировал бота или удалил чат - это ожидаемая ситуация
            log_info(
                "telegram_api", f"Пользователь {chat_id} заблокировал бота или чат недоступен"
            )
            return None  # Не повторяем попытку
        except TelegramBadRequest as e:
            # Некорректный запрос (например, неверный chat_id)
            log_error(
                "telegram_api", f"Некорректный запрос для пользователя {chat_id}: {e.message}", e
            )
            return None  # Не повторяем попытку
        except TelegramUnauthorizedError as e:
            # Проблема с токеном бота
            log_error("telegram_api", f"Ошибка авторизации бота: {e.message}", e)
            return None  # Не повторяем попытку
        except TelegramNetworkError as e:
            # Проблемы с сетью
            if attempt < max_retries:
                wait_time = attempt * 2  # Экспоненциальная задержка
                log_warning(
                    "telegram_api",
                    f"Ошибка сети для пользователя {chat_id}, попытка {attempt}/{max_retries}, ожидание {wait_time} сек",
                )
                await asyncio.sleep(wait_time)
                continue
            else:
                log_error(
                    "telegram_api",
                    f"Ошибка сети для пользователя {chat_id} после {max_retries} попыток",
                    e,
                )
                return None
        except TelegramServerError as e:
            # Ошибка сервера Telegram
            if attempt < max_retries:
                wait_time = attempt * 5  # Большая задержка для серверных ошибок
                log_warning(
                    "telegram_api",
                    f"Ошибка сервера Telegram для пользователя {chat_id}, попытка {attempt}/{max_retries}, ожидание {wait_time} сек",
                )
                await asyncio.sleep(wait_time)
                continue
            else:
                log_error(
                    "telegram_api",
                    f"Ошибка сервера Telegram для пользователя {chat_id} после {max_retries} попыток",
                    e,
                )
                return None
        except TelegramAPIError as e:
            # Другие ошибки Telegram API
            log_error(
                "telegram_api", f"Ошибка Telegram API для пользователя {chat_id}: {e.message}", e
            )
            return None  # Не повторяем для неизвестных ошибок
        except Exception as e:
            # Неожиданные ошибки
            log_error(
                "telegram_api",
                f"Неожиданная ошибка при отправке сообщения пользователю {chat_id}",
                e,
            )
            return None

    return None


async def safe_send_media_group(
    bot: Bot, chat_id: int, media: List[InputMediaPhoto], max_retries: int = 3
) -> Optional[List[Message]]:
    """
    Безопасная отправка медиагруппы с обработкой ошибок Telegram API

    Args:
        bot: Экземпляр бота
        chat_id: ID чата
        media: Список медиафайлов
        max_retries: Максимум попыток при ошибке

    Returns:
        Список Message объектов или None при ошибке
    """
    for attempt in range(1, max_retries + 1):
        try:
            return await bot.send_media_group(chat_id=chat_id, media=media)
        except TelegramRetryAfter as e:
            wait_time = e.retry_after
            log_warning(
                "telegram_api",
                f"Rate limit для медиагруппы пользователя {chat_id}: ожидание {wait_time} сек",
            )
            await asyncio.sleep(wait_time)
            continue
        except TelegramForbiddenError:
            log_warning("telegram_api", f"Пользователь {chat_id} заблокировал бота (медиагруппа)")
            return None
        except TelegramBadRequest as e:
            log_error(
                "telegram_api",
                f"Некорректный запрос медиагруппы для пользователя {chat_id}: {e.message}",
                e,
            )
            return None
        except TelegramNetworkError as e:
            if attempt < max_retries:
                wait_time = attempt * 2
                log_warning(
                    "telegram_api",
                    f"Ошибка сети при отправке медиагруппы пользователю {chat_id}, попытка {attempt}/{max_retries}",
                )
                await asyncio.sleep(wait_time)
                continue
            else:
                log_error(
                    "telegram_api",
                    f"Ошибка сети при отправке медиагруппы пользователю {chat_id}",
                    e,
                )
                return None
        except TelegramAPIError as e:
            log_error(
                "telegram_api",
                f"Ошибка Telegram API при отправке медиагруппы пользователю {chat_id}: {e.message}",
                e,
            )
            return None
        except Exception as e:
            log_error(
                "telegram_api",
                f"Неожиданная ошибка при отправке медиагруппы пользователю {chat_id}",
                e,
            )
            return None

    return None


async def safe_edit_message_text(
    bot: Bot,
    chat_id: int,
    message_id: int,
    text: str,
    parse_mode: Optional[str] = ParseMode.HTML,
    reply_markup: Optional[Any] = None,
    disable_web_page_preview: Optional[bool] = None,
    max_retries: int = 2,
) -> Optional[Message]:
    """
    Безопасное редактирование сообщения с обработкой ошибок Telegram API

    Args:
        bot: Экземпляр бота
        chat_id: ID чата
        message_id: ID сообщения
        text: Новый текст
        parse_mode: Режим парсинга
        reply_markup: Клавиатура (опционально)
        disable_web_page_preview: Отключить превью ссылок
        max_retries: Максимум попыток при ошибке

    Returns:
        Message объект или None при ошибке
    """
    for attempt in range(1, max_retries + 1):
        try:
            return await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
                disable_web_page_preview=disable_web_page_preview,
            )
        except TelegramRetryAfter as e:
            wait_time = e.retry_after
            log_warning(
                "telegram_api",
                f"Rate limit при редактировании сообщения для пользователя {chat_id}: ожидание {wait_time} сек",
            )
            await asyncio.sleep(wait_time)
            continue
        except TelegramBadRequest as e:
            # Сообщение не изменилось или не найдено - это нормально, не логируем как ошибку
            if (
                "message is not modified" in str(e).lower()
                or "message to edit not found" in str(e).lower()
            ):
                log_info(
                    "telegram_api",
                    f"Сообщение для пользователя {chat_id} не изменилось или не найдено",
                )
                return None
            log_error(
                "telegram_api",
                f"Некорректный запрос редактирования для пользователя {chat_id}: {e.message}",
                e,
            )
            return None
        except TelegramForbiddenError:
            # Пользователь заблокировал бота - это ожидаемая ситуация
            log_info(
                "telegram_api", f"Пользователь {chat_id} заблокировал бота (редактирование)"
            )
            return None
        except TelegramNetworkError as e:
            if attempt < max_retries:
                wait_time = attempt * 2
                log_warning(
                    "telegram_api",
                    f"Ошибка сети при редактировании сообщения пользователю {chat_id}, попытка {attempt}/{max_retries}",
                )
                await asyncio.sleep(wait_time)
                continue
            else:
                log_error(
                    "telegram_api",
                    f"Ошибка сети при редактировании сообщения пользователю {chat_id}",
                    e,
                )
                return None
        except TelegramAPIError as e:
            log_error(
                "telegram_api",
                f"Ошибка Telegram API при редактировании сообщения пользователю {chat_id}: {e.message}",
                e,
            )
            return None
        except Exception as e:
            log_error(
                "telegram_api",
                f"Неожиданная ошибка при редактировании сообщения пользователю {chat_id}",
                e,
            )
            return None

    return None
