"""
Система логирования ошибок для бота

Особенности:
- Логирование с уровнями INFO / WARNING / ERROR
- Запись в файл logs/app.log
- Логирование traceback для ошибок
- Совместимость с существующим API
- Безопасность: фильтрация токенов и чувствительных данных из логов
"""
import logging
import sys
import traceback
import os
import re
from datetime import datetime
from typing import List, Dict, Any, Optional
from collections import deque
from pathlib import Path


# Создаем директорию для логов, если её нет
LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "app.log"


def setup_logging():
    """
    Настраивает систему логирования
    
    ВАЖНО: Логи должны идти в stdout/stderr для Railway и других платформ.
    Файловый handler опционален и используется только если возможно создать файл.
    """
    # Создаем форматтер
    formatter = logging.Formatter(
        fmt='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Настраиваем корневой логгер
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    
    # Удаляем существующие handlers, чтобы избежать дублирования
    root_logger.handlers.clear()
    
    # ВАЖНО: Консольный handler ДОЛЖЕН быть первым для Railway
    # Railway читает логи из stdout/stderr
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)  # В консоль только INFO и выше
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    
    # Также добавляем stderr handler для ошибок (Railway читает и его)
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.WARNING)  # В stderr только WARNING и ERROR
    stderr_handler.setFormatter(formatter)
    root_logger.addHandler(stderr_handler)
    
    # Файловый handler опционален (может не работать в Railway)
    # Пытаемся создать только если возможно
    try:
        file_handler = logging.FileHandler(LOG_FILE, encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)  # В файл пишем все уровни
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
    except (OSError, PermissionError, FileNotFoundError):
        # Если не удалось создать файл (например, в Railway), продолжаем без него
        # Логи все равно будут в stdout/stderr
        pass
    
    return root_logger


# Инициализируем логирование при импорте модуля
setup_logging()


def sanitize_sensitive_data(text: str) -> str:
    """
    Удаляет чувствительные данные из текста перед логированием
    
    Фильтрует:
    - Токены бота (BOT_TOKEN) - формат: 123456789:ABCdefGHIjklMNOpqrsTUVwxyz
    - Токены Turso (TURSO_AUTH_TOKEN) - длинные строки
    - API ключи и секреты
    - Пароли
    - Полные сообщения пользователей
    - Персональные данные (username, first_name, last_name, phone, email)
    
    Args:
        text: Текст для очистки
    
    Returns:
        Очищенный текст
    """
    if not text:
        return text
    
    # Паттерны для поиска чувствительных данных (в порядке приоритета)
    patterns = [
        # Переменные окружения с токенами (самый приоритетный - заменяем полностью)
        (r'(BOT_TOKEN|TURSO_AUTH_TOKEN|API_KEY|SECRET|PASSWORD|AUTH_TOKEN)\s*[:=]\s*["\']?[A-Za-z0-9_:_-]{20,}["\']?', r'\1=[REDACTED]'),
        # Токены бота Telegram (формат: 123456789:ABCdefGHIjklMNOpqrsTUVwxyz)
        # Очень специфичный паттерн для токенов бота (цифры:буквы_цифры_дефисы)
        # Должен быть перед общим паттерном token, чтобы не перехватывался
        # Ищем токены бота в любом контексте (с пробелами, в кавычках, после = и т.д.)
        # Токены бота обычно имеют 8+ цифр, двоеточие и 20+ символов после
        (r'\d{8,}:[A-Za-z0-9_-]{20,}', '[BOT_TOKEN]'),
        # Токены в URL или connection strings (но не токены бота, которые уже обработаны)
        (r'(auth_token|token|api_key|apikey)\s*[:=]\s*["\']?[A-Za-z0-9_-]{20,}["\']?', r'\1=[REDACTED]'),
        # Длинные токены (40+ символов) - могут быть токенами Turso или другими
        # Но только если это не часть URL или другого контекста
        (r'\b[A-Za-z0-9_-]{40,}\b', '[TOKEN]'),
        # API ключи в различных форматах
        (r'\b(api[_-]?key|apikey|api_key)\s*[:=]\s*["\']?[A-Za-z0-9_-]{20,}["\']?', r'\1=[API_KEY]'),
        # Секреты и пароли
        (r'\b(secret|password|passwd|pwd|pass)\s*[:=]\s*["\']?[^\s"\']+["\']?', r'\1=[REDACTED]'),
        # Email адреса
        (r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', '[EMAIL]'),
        # Телефонные номера (различные форматы) - но не токены бота
        (r'\b\+?\d{1,3}[-.\s]?\(?\d{1,4}\)?[-.\s]?\d{1,4}[-.\s]?\d{1,9}\b(?!:)', '[PHONE]'),
        # Полные сообщения пользователей (оставляем только ID)
        (r'message\.text\s*[:=]\s*["\'][^"\']+["\']', 'message.text=[REDACTED]'),
        (r'message\.from_user\.(username|first_name|last_name|phone_number)\s*[:=]\s*["\'][^"\']+["\']', r'message.from_user.\1=[REDACTED]'),
        # Данные пользователя в словарях
        (r'["\'](username|first_name|last_name|phone_number|email)["\']\s*:\s*["\'][^"\']+["\']', r'"\1":"[REDACTED]"'),
    ]
    
    sanitized = text
    for pattern, replacement in patterns:
        sanitized = re.sub(pattern, replacement, sanitized, flags=re.IGNORECASE)
    
    return sanitized


class ErrorLogger:
    """Класс для сбора и хранения ошибок с логированием в файл"""
    
    def __init__(self, max_errors: int = 50, max_warnings: int = 30):
        """
        Инициализация логгера ошибок
        
        Args:
            max_errors: Максимальное количество хранимых ошибок в памяти
            max_warnings: Максимальное количество хранимых предупреждений в памяти
        """
        self.errors: deque = deque(maxlen=max_errors)
        self.warnings: deque = deque(maxlen=max_warnings)
        
        # Создаем отдельный логгер для этого модуля
        self._logger = logging.getLogger("error_logger")
        self._logger.setLevel(logging.DEBUG)
    
    def log_error(
        self, 
        source: str, 
        message: str, 
        exception: Optional[Exception] = None,
        exc_info: bool = True
    ):
        """
        Логирует ошибку с полным traceback
        
        Args:
            source: Источник ошибки (модуль/компонент)
            message: Сообщение об ошибке
            exception: Объект исключения (опционально)
            exc_info: Логировать ли traceback (по умолчанию True)
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Очищаем чувствительные данные из сообщения
        sanitized_message = sanitize_sensitive_data(message)
        
        # Формируем полное сообщение
        full_message = f"[{source}] {sanitized_message}"
        
        # Если передан exception, добавляем его информацию (также очищенную)
        exception_str = None
        if exception:
            exception_str = str(exception)
            exception_str = sanitize_sensitive_data(exception_str)
            full_message += f": {exception_str}"
        
        # Записываем в память (только очищенные данные)
        error_entry = {
            "timestamp": timestamp,
            "source": source,
            "message": sanitized_message,
            "exception": exception_str,
            "type": "error"
        }
        self.errors.append(error_entry)
        
        # Логируем с traceback если нужно
        # ВАЖНО: traceback может содержать чувствительные данные (токены, пароли)
        # Поэтому мы очищаем traceback перед логированием
        if exc_info and exception:
            try:
                # Получаем traceback и очищаем его от чувствительных данных
                import traceback as tb
                tb_str = ''.join(tb.format_exception(type(exception), exception, exception.__traceback__))
                # Очищаем traceback от токенов и чувствительных данных
                tb_str = sanitize_sensitive_data(tb_str)
                # Логируем очищенный traceback
                self._logger.error(f"{full_message}\n{tb_str}")
            except Exception:
                # Если не удалось получить или очистить traceback, логируем только сообщение
                self._logger.error(full_message)
        else:
            # Логируем без traceback
            self._logger.error(full_message)
    
    def log_warning(self, source: str, message: str):
        """
        Логирует предупреждение
        
        Args:
            source: Источник предупреждения
            message: Сообщение
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Очищаем чувствительные данные из сообщения
        sanitized_message = sanitize_sensitive_data(message)
        
        # Записываем в память (только очищенные данные)
        warning_entry = {
            "timestamp": timestamp,
            "source": source,
            "message": sanitized_message,
            "type": "warning"
        }
        self.warnings.append(warning_entry)
        
        # Логируем
        self._logger.warning(f"[{source}] {sanitized_message}")
    
    def log_info(self, source: str, message: str):
        """
        Логирует информационное сообщение
        
        Args:
            source: Источник информации
            message: Сообщение
        """
        self._logger.info(f"[{source}] {message}")
    
    def log_debug(self, source: str, message: str):
        """
        Логирует отладочное сообщение
        
        Args:
            source: Источник отладки
            message: Сообщение
        """
        self._logger.debug(f"[{source}] {message}")
    
    def get_errors(self, limit: int = 20) -> List[Dict]:
        """Возвращает последние ошибки"""
        return list(self.errors)[-limit:]
    
    def get_warnings(self, limit: int = 10) -> List[Dict]:
        """Возвращает последние предупреждения"""
        return list(self.warnings)[-limit:]
    
    def get_all_logs(self, limit: int = 30) -> List[Dict]:
        """Возвращает все логи (ошибки + предупреждения)"""
        all_logs = list(self.errors) + list(self.warnings)
        # Сортируем по времени (новые сверху)
        all_logs.sort(key=lambda x: x["timestamp"], reverse=True)
        return all_logs[:limit]
    
    def clear(self):
        """Очищает все логи из памяти"""
        self.errors.clear()
        self.warnings.clear()
        self._logger.info("Логи ошибок очищены из памяти")
    
    def get_stats(self) -> Dict:
        """Возвращает статистику ошибок"""
        return {
            "total_errors": len(self.errors),
            "total_warnings": len(self.warnings),
            "errors_by_source": self._count_by_source(self.errors),
            "warnings_by_source": self._count_by_source(self.warnings),
        }
    
    def _count_by_source(self, logs: deque) -> Dict[str, int]:
        """Подсчитывает логи по источникам"""
        counts: Dict[str, int] = {}
        for log in logs:
            source = log.get("source", "unknown")
            counts[source] = counts.get(source, 0) + 1
        return counts
    
    def format_for_telegram(self, limit: int = 15) -> str:
        """Форматирует ошибки для отправки в Telegram"""
        errors = self.get_errors(limit)
        
        if not errors:
            return "✅ Ошибок нет! Все работает отлично."
        
        text = "🚨 <b>Последние ошибки:</b>\n\n"
        
        for i, err in enumerate(reversed(errors), 1):
            timestamp = err.get("timestamp", "")
            source = err.get("source", "unknown")
            msg = err.get("message", "")
            exc = err.get("exception", "")
            
            text += f"<b>{i}.</b> [{source}] {timestamp}\n"
            text += f"   📝 {msg[:100]}\n"
            if exc:
                text += f"   ⚠️ <code>{exc[:150]}</code>\n"
            text += "\n"
        
        # Telegram ограничивает длину сообщения
        if len(text) > 4000:
            text = text[:4000] + "\n\n... (сокращено)"
        
        return text


# Глобальный экземпляр логгера ошибок
error_logger = ErrorLogger()


# Удобные функции для импорта
def log_error(
    source: str, 
    message: str, 
    exception: Optional[Exception] = None,
    exc_info: bool = True
):
    """
    Быстрое логирование ошибки с traceback
    
    Args:
        source: Источник ошибки
        message: Сообщение об ошибке
        exception: Объект исключения (опционально)
        exc_info: Логировать ли traceback (по умолчанию True)
    
    Пример использования:
        try:
            # какой-то код
        except Exception as e:
            log_error("module_name", "Описание ошибки", e)
    """
    error_logger.log_error(source, message, exception, exc_info)


def log_warning(source: str, message: str):
    """
    Быстрое логирование предупреждения
    
    Args:
        source: Источник предупреждения
        message: Сообщение
    
    Пример использования:
        log_warning("module_name", "Предупреждение о чем-то")
    """
    error_logger.log_warning(source, message)


def log_info(source: str, message: str):
    """
    Быстрое логирование информации
    
    Args:
        source: Источник информации
        message: Сообщение
    
    Пример использования:
        log_info("module_name", "Информационное сообщение")
    """
    error_logger.log_info(source, message)


def log_debug(source: str, message: str):
    """
    Быстрое логирование отладочного сообщения
    
    Args:
        source: Источник отладки
        message: Сообщение
    
    Пример использования:
        log_debug("module_name", "Отладочное сообщение")
    """
    error_logger.log_debug(source, message)
