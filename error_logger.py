"""
Система логирования ошибок для бота
"""
import logging
from datetime import datetime
from typing import List, Dict, Any
from collections import deque


# Стандартный логгер Python
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("error_logger")


class ErrorLogger:
    """Класс для сбора и хранения ошибок"""
    
    def __init__(self, max_errors: int = 50, max_warnings: int = 30):
        self.errors: deque = deque(maxlen=max_errors)
        self.warnings: deque = deque(maxlen=max_warnings)
        self._logger = logging.getLogger("bot")
    
    def log_error(self, source: str, message: str, exception: Exception = None):
        """Логирует ошибку"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        error_entry = {
            "timestamp": timestamp,
            "source": source,
            "message": message,
            "exception": str(exception) if exception else None,
            "type": "error"
        }
        self.errors.append(error_entry)
        
        # Также в стандартный логгер
        if exception:
            self._logger.error(f"[{source}] {message}: {exception}")
        else:
            self._logger.error(f"[{source}] {message}")
    
    def log_warning(self, source: str, message: str):
        """Логирует предупреждение"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        warning_entry = {
            "timestamp": timestamp,
            "source": source,
            "message": message,
            "type": "warning"
        }
        self.warnings.append(warning_entry)
        self._logger.warning(f"[{source}] {message}")
    
    def log_info(self, source: str, message: str):
        """Логирует информацию"""
        self._logger.info(f"[{source}] {message}")
    
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
        """Очищает все логи"""
        self.errors.clear()
        self.warnings.clear()
        self._logger.info("Логи ошибок очищены")
    
    def get_stats(self) -> Dict:
        """Возвращает статистику ошибок"""
        return {
            "total_errors": len(self.errors),
            "total_warnings": len(self.warnings),
            "errors_by_source": self._count_by_source(self.errors),
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
def log_error(source: str, message: str, exception: Exception = None):
    """Быстрое логирование ошибки"""
    error_logger.log_error(source, message, exception)


def log_warning(source: str, message: str):
    """Быстрое логирование предупреждения"""
    error_logger.log_warning(source, message)


def log_info(source: str, message: str):
    """Быстрое логирование информации"""
    error_logger.log_info(source, message)

