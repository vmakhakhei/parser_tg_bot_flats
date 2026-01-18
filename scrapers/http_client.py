"""
Унифицированный HTTP-клиент для всех парсеров

Особенности:
- Единый timeout для всех запросов
- Retry механизм (3 попытки)
- Общий User-Agent из конфигурации
- Централизованная обработка ошибок
"""
import asyncio
import aiohttp
from typing import Optional, Dict, Any
from functools import wraps

# Импортируем конфигурацию
try:
    from config import USER_AGENT
except ImportError:
    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# Импортируем error_logger для логирования
try:
    from error_logger import log_error, log_warning, log_info
except ImportError:
    def log_error(source, message, exception=None):
        print(f"[ERROR] [{source}] {message}: {exception}")
    def log_warning(source, message):
        print(f"[WARN] [{source}] {message}")
    def log_info(source, message):
        print(f"[INFO] [{source}] {message}")


# Настройки по умолчанию
DEFAULT_TIMEOUT = 30  # секунд
DEFAULT_RETRY_COUNT = 3
DEFAULT_RETRY_DELAY = 1  # секунд между попытками


class HTTPClient:
    """
    Унифицированный HTTP-клиент для парсеров
    
    Особенности:
    - Единый timeout
    - Retry механизм (3 попытки по умолчанию)
    - Общий User-Agent
    - Централизованная обработка ошибок
    """
    
    def __init__(
        self,
        timeout: int = DEFAULT_TIMEOUT,
        retry_count: int = DEFAULT_RETRY_COUNT,
        retry_delay: int = DEFAULT_RETRY_DELAY,
        user_agent: Optional[str] = None
    ):
        """
        Инициализация HTTP-клиента
        
        Args:
            timeout: Таймаут запроса в секундах
            retry_count: Количество попыток при ошибке
            retry_delay: Задержка между попытками в секундах
            user_agent: User-Agent для запросов (по умолчанию из config)
        """
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.retry_count = retry_count
        self.retry_delay = retry_delay
        self.user_agent = user_agent or USER_AGENT
        self.session: Optional[aiohttp.ClientSession] = None
        
        # Базовые заголовки
        self.base_headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Cache-Control": "max-age=0",
        }
    
    async def __aenter__(self):
        """Context manager entry"""
        await self.start_session()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        await self.close_session()
    
    async def start_session(self):
        """Создает HTTP сессию"""
        if self.session is None or self.session.closed:
            # Закрываем старую сессию, если она существует
            if self.session is not None and not self.session.closed:
                try:
                    await self.session.close()
                except Exception:
                    pass  # Игнорируем ошибки при закрытии
            self.session = aiohttp.ClientSession(
                headers=self.base_headers,
                timeout=self.timeout
            )
    
    async def close_session(self):
        """Закрывает HTTP сессию"""
        if self.session and not self.session.closed:
            await self.session.close()
            self.session = None
    
    async def get(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        source_name: str = "http_client"
    ) -> Optional[aiohttp.ClientResponse]:
        """
        Выполняет GET запрос с retry механизмом
        
        Args:
            url: URL для запроса
            headers: Дополнительные заголовки
            params: Параметры запроса
            source_name: Имя источника для логирования
        
        Returns:
            Response объект или None при ошибке
        """
        await self.start_session()
        
        # Объединяем заголовки
        request_headers = {**self.base_headers}
        if headers:
            request_headers.update(headers)
        
        last_exception = None
        
        for attempt in range(1, self.retry_count + 1):
            # Проверяем и пересоздаем сессию перед каждой попыткой
            if self.session is None or self.session.closed:
                await self.start_session()
            
            # Дополнительная проверка на случай, если сессия все еще None
            if self.session is None:
                log_error(source_name, f"Не удалось создать сессию для {url}")
                return None
            
            try:
                async with self.session.get(url, headers=request_headers, params=params) as response:
                    if response.status == 200:
                        if attempt > 1:
                            log_info(source_name, f"Успешный запрос к {url} после {attempt} попытки")
                        return response
                    else:
                        error_msg = f"HTTP {response.status} для {url}"
                        if attempt < self.retry_count:
                            log_warning(source_name, f"{error_msg}, попытка {attempt}/{self.retry_count}")
                        else:
                            log_error(source_name, error_msg)
                            return None
                            
            except asyncio.TimeoutError as e:
                last_exception = e
                if attempt < self.retry_count:
                    log_warning(source_name, f"Таймаут для {url}, попытка {attempt}/{self.retry_count}")
                    await asyncio.sleep(self.retry_delay * attempt)  # Экспоненциальная задержка
                else:
                    log_error(source_name, f"Таймаут для {url} после {self.retry_count} попыток", e)
                    return None
                    
            except aiohttp.ClientError as e:
                last_exception = e
                if attempt < self.retry_count:
                    log_warning(source_name, f"Ошибка клиента для {url}, попытка {attempt}/{self.retry_count}: {type(e).__name__}")
                    # Пересоздаем сессию при ошибке соединения
                    if isinstance(e, (aiohttp.ClientConnectionError, aiohttp.ClientOSError)):
                        await self.start_session()
                    await asyncio.sleep(self.retry_delay * attempt)
                else:
                    log_error(source_name, f"Ошибка клиента для {url} после {self.retry_count} попыток", e)
                    return None
                    
            except AttributeError as e:
                # Ошибка "NoneType object has no attribute 'get'" - сессия была закрыта
                last_exception = e
                if attempt < self.retry_count:
                    log_warning(source_name, f"Сессия закрыта для {url}, пересоздаю сессию, попытка {attempt}/{self.retry_count}")
                    await self.start_session()  # Пересоздаем сессию
                    await asyncio.sleep(self.retry_delay * attempt)
                else:
                    log_error(source_name, f"Не удалось создать сессию для {url} после {self.retry_count} попыток", e)
                    return None
            except Exception as e:
                last_exception = e
                if attempt < self.retry_count:
                    log_warning(source_name, f"Неожиданная ошибка для {url}, попытка {attempt}/{self.retry_count}: {type(e).__name__}")
                    # Пересоздаем сессию при ошибке соединения
                    if isinstance(e, (aiohttp.ClientConnectionError, aiohttp.ClientOSError)):
                        await self.start_session()
                    await asyncio.sleep(self.retry_delay * attempt)
                else:
                    log_error(source_name, f"Неожиданная ошибка для {url} после {self.retry_count} попыток", e)
                    return None
        
        return None
    
    async def fetch_html(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        source_name: str = "http_client"
    ) -> Optional[str]:
        """
        Получает HTML страницы с retry механизмом
        
        Args:
            url: URL для запроса
            headers: Дополнительные заголовки
            params: Параметры запроса
            source_name: Имя источника для логирования
        
        Returns:
            HTML текст или None при ошибке
        """
        await self.start_session()
        
        # Объединяем заголовки
        request_headers = {**self.base_headers}
        if headers:
            request_headers.update(headers)
        
        for attempt in range(1, self.retry_count + 1):
            # Проверяем и пересоздаем сессию перед каждой попыткой
            if self.session is None or self.session.closed:
                await self.start_session()
            
            if self.session is None:
                log_error(source_name, f"Не удалось создать сессию для {url}")
                return None
            
            try:
                async with self.session.get(url, headers=request_headers, params=params) as response:
                    if response.status == 200:
                        # Читаем тело ответа ВНУТРИ async with блока
                        try:
                            text = await response.text()
                            if attempt > 1:
                                log_info(source_name, f"Успешный запрос к {url} после {attempt} попытки")
                            return text
                        except Exception as e:
                            log_error(source_name, f"Ошибка чтения HTML из {url}", e)
                            if attempt < self.retry_count:
                                await asyncio.sleep(self.retry_delay * attempt)
                                continue
                            return None
                    else:
                        error_msg = f"HTTP {response.status} для {url}"
                        if attempt < self.retry_count:
                            log_warning(source_name, f"{error_msg}, попытка {attempt}/{self.retry_count}")
                            await asyncio.sleep(self.retry_delay * attempt)
                        else:
                            log_error(source_name, error_msg)
                            return None
                            
            except (asyncio.TimeoutError, aiohttp.ClientError, AttributeError) as e:
                if attempt < self.retry_count:
                    log_warning(source_name, f"Ошибка для {url}, попытка {attempt}/{self.retry_count}: {type(e).__name__}")
                    if isinstance(e, (aiohttp.ClientConnectionError, aiohttp.ClientOSError, AttributeError)):
                        await self.start_session()
                    await asyncio.sleep(self.retry_delay * attempt)
                else:
                    log_error(source_name, f"Ошибка для {url} после {self.retry_count} попыток", e)
                    return None
            except Exception as e:
                if attempt < self.retry_count:
                    log_warning(source_name, f"Неожиданная ошибка для {url}, попытка {attempt}/{self.retry_count}: {type(e).__name__}")
                    if isinstance(e, (aiohttp.ClientConnectionError, aiohttp.ClientOSError)):
                        await self.start_session()
                    await asyncio.sleep(self.retry_delay * attempt)
                else:
                    log_error(source_name, f"Неожиданная ошибка для {url} после {self.retry_count} попыток", e)
                    return None
        
        return None
    
    async def fetch_json(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        source_name: str = "http_client"
    ) -> Optional[Dict[str, Any]]:
        """
        Получает JSON данные с retry механизмом
        
        Args:
            url: URL для запроса
            headers: Дополнительные заголовки (по умолчанию добавляется Accept: application/json)
            params: Параметры запроса
            source_name: Имя источника для логирования
        
        Returns:
            JSON данные или None при ошибке
        """
        await self.start_session()
        
        # Устанавливаем заголовок для JSON
        json_headers = {"Accept": "application/json, text/plain, */*"}
        if headers:
            json_headers.update(headers)
        else:
            json_headers = {**self.base_headers, **json_headers}
        
        for attempt in range(1, self.retry_count + 1):
            # Проверяем и пересоздаем сессию перед каждой попыткой
            if self.session is None or self.session.closed:
                await self.start_session()
            
            if self.session is None:
                log_error(source_name, f"Не удалось создать сессию для {url}")
                return None
            
            try:
                async with self.session.get(url, headers=json_headers, params=params) as response:
                    if response.status == 200:
                        # Читаем тело ответа ВНУТРИ async with блока
                        try:
                            data = await response.json()
                            if attempt > 1:
                                log_info(source_name, f"Успешный запрос к {url} после {attempt} попытки")
                            return data
                        except Exception as e:
                            log_error(source_name, f"Ошибка чтения JSON из {url}", e)
                            if attempt < self.retry_count:
                                await asyncio.sleep(self.retry_delay * attempt)
                                continue
                            return None
                    else:
                        error_msg = f"HTTP {response.status} для {url}"
                        if attempt < self.retry_count:
                            log_warning(source_name, f"{error_msg}, попытка {attempt}/{self.retry_count}")
                            await asyncio.sleep(self.retry_delay * attempt)
                        else:
                            log_error(source_name, error_msg)
                            return None
                            
            except (asyncio.TimeoutError, aiohttp.ClientError, AttributeError) as e:
                if attempt < self.retry_count:
                    log_warning(source_name, f"Ошибка для {url}, попытка {attempt}/{self.retry_count}: {type(e).__name__}")
                    if isinstance(e, (aiohttp.ClientConnectionError, aiohttp.ClientOSError, AttributeError)):
                        await self.start_session()
                    await asyncio.sleep(self.retry_delay * attempt)
                else:
                    log_error(source_name, f"Ошибка для {url} после {self.retry_count} попыток", e)
                    return None
            except Exception as e:
                if attempt < self.retry_count:
                    log_warning(source_name, f"Неожиданная ошибка для {url}, попытка {attempt}/{self.retry_count}: {type(e).__name__}")
                    if isinstance(e, (aiohttp.ClientConnectionError, aiohttp.ClientOSError)):
                        await self.start_session()
                    await asyncio.sleep(self.retry_delay * attempt)
                else:
                    log_error(source_name, f"Неожиданная ошибка для {url} после {self.retry_count} попыток", e)
                    return None
        
        return None


# Глобальный экземпляр клиента (singleton pattern)
_global_client: Optional[HTTPClient] = None


def get_http_client(
    timeout: int = DEFAULT_TIMEOUT,
    retry_count: int = DEFAULT_RETRY_COUNT,
    retry_delay: int = DEFAULT_RETRY_DELAY
) -> HTTPClient:
    """
    Получает глобальный экземпляр HTTP-клиента
    
    Args:
        timeout: Таймаут запроса в секундах
        retry_count: Количество попыток при ошибке
        retry_delay: Задержка между попытками в секундах
    
    Returns:
        Экземпляр HTTPClient
    """
    global _global_client
    
    if _global_client is None or _global_client.session is None or _global_client.session.closed:
        _global_client = HTTPClient(
            timeout=timeout,
            retry_count=retry_count,
            retry_delay=retry_delay
        )
    
    return _global_client
