#!/usr/bin/env python3
"""
from playwright.async_api import async_playwright

Investigative script для анализа Kufar city lookup.
Собирает артефакты, анализирует HAR/логи/HTML, тестирует endpoints и генерирует отчеты.
"""
import os
import sys
import json
import re
import time
import asyncio
import logging
import aiohttp
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional, Set
from urllib.parse import urlparse, parse_qs, urlencode
from collections import defaultdict

# Добавляем путь к проекту
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Константы
RATE_LIMIT_RPS = 1.0  # 1 запрос в секунду
MAX_REQUESTS_TOTAL = 200
MAX_RETRIES = 3
BACKOFF_ON_403_429 = 60  # секунд
HEADLESS_ENABLED = os.getenv("HEADLESS", "false").lower() == "true"

# Стандартные заголовки для запросов
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Origin": "https://re.kufar.by",
    "Referer": "https://re.kufar.by/",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9",
}

# Ключевые слова для поиска candidate endpoints
LOCATION_KEYWORDS = ['autocomplete', 'locations', 'search', 'province', 'locality', 'place', 'region', 'city', 'location']

# Регулярка для slug'ов
SLUG_PATTERN = re.compile(r'province-[a-z0-9_~-]+(?:~[^"\'<> \n]+)*')
# Регулярка для русских названий городов
RUSSIAN_LABEL_PATTERN = re.compile(r'[А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+)*')


class RateLimiter:
    """Rate limiter для соблюдения лимита запросов"""
    def __init__(self, rps: float = 1.0):
        self.rps = rps
        self.min_interval = 1.0 / rps
        self.last_request_time = 0.0
    
    async def wait(self):
        """Ждет до следующего разрешенного времени запроса"""
        now = time.time()
        elapsed = now - self.last_request_time
        if elapsed < self.min_interval:
            await asyncio.sleep(self.min_interval - elapsed)
        self.last_request_time = time.time()


class InvestigationRunner:
    """Основной класс для выполнения investigation"""
    
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.logs_dir = output_dir / "logs"
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.rate_limiter = RateLimiter(RATE_LIMIT_RPS)
        self.request_count = 0
        self.blocked_hosts: Set[str] = set()
        self.commands_run = []
        
        # Результаты
        self.har_analysis = []
        self.html_slug_samples = []
        self.header_tests = []
        self.city_map_candidates = []
        
        # Настройка файлового логгера
        log_file = self.logs_dir / "kufar_investigate.log"
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        logger.addHandler(file_handler)
    
    def log_command(self, cmd: str):
        """Логирует выполненную команду"""
        self.commands_run.append(f"{datetime.now(timezone.utc).isoformat()}: {cmd}")
        logger.info(f"Command: {cmd}")
    
    async def find_artifacts(self, repo_root: Path) -> Dict[str, List[Path]]:
        """Находит все артефакты в репозитории"""
        logger.info("Step 1: Поиск артефактов в репозитории...")
        
        artifacts = {
            'har': [],
            'html': [],
            'json': [],
            'log': [],
        }
        
        # Расширения для поиска
        extensions = {
            'har': ['.har'],
            'html': ['.html', '.htm'],
            'json': ['.json'],
            'log': ['.log'],
        }
        
        # Игнорируемые директории
        ignore_dirs = {'.git', '__pycache__', 'node_modules', '.venv', 'venv', 'env'}
        
        for root, dirs, files in os.walk(repo_root):
            # Фильтруем игнорируемые директории
            dirs[:] = [d for d in dirs if d not in ignore_dirs]
            
            for file in files:
                file_path = Path(root) / file
                rel_path = file_path.relative_to(repo_root)
                
                # Проверяем по расширению
                ext = file_path.suffix.lower()
                for artifact_type, exts in extensions.items():
                    if ext in exts:
                        # Дополнительная проверка содержимого для JSON/LOG
                        if artifact_type in ['json', 'log']:
                            try:
                                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                                    content = f.read(1000).lower()
                                    # Проверяем на наличие ключевых слов
                                    if any(kw in content for kw in ['kufar', 'location', 'autocomplete', 'province', 'locality']):
                                        artifacts[artifact_type].append(file_path)
                            except:
                                pass
                        else:
                            artifacts[artifact_type].append(file_path)
                        break
        
        logger.info(f"Найдено артефактов: HAR={len(artifacts['har'])}, HTML={len(artifacts['html'])}, JSON={len(artifacts['json'])}, LOG={len(artifacts['log'])}")
        return artifacts
    
    async def analyze_har_logs(self, artifacts: Dict[str, List[Path]]):
        """Анализирует HAR файлы и логи для поиска candidate endpoints"""
        logger.info("Step 2: Анализ HAR/логов...")
        
        all_files = artifacts['har'] + artifacts['json'] + artifacts['log']
        
        for file_path in all_files:
            try:
                logger.info(f"Анализ файла: {file_path.name}")
                
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                
                # Пробуем парсить как JSON (HAR или JSON лог)
                try:
                    data = json.loads(content)
                    await self._parse_har_json(data, str(file_path))
                except json.JSONDecodeError:
                    # Пробуем парсить как текстовый лог
                    await self._parse_text_log(content, str(file_path))
            
            except Exception as e:
                logger.warning(f"Ошибка анализа {file_path}: {e}")
        
        logger.info(f"Найдено {len(self.har_analysis)} candidate endpoints")
    
    async def _parse_har_json(self, data: Any, source_file: str):
        """Парсит HAR или JSON структуру"""
        if isinstance(data, dict):
            # HAR формат
            if 'log' in data:
                entries = data['log'].get('entries', [])
                for entry in entries:
                    await self._process_har_entry(entry, source_file)
            # Или просто массив запросов
            elif 'entries' in data:
                for entry in data['entries']:
                    await self._process_har_entry(entry, source_file)
            # Или это может быть ответ API напрямую
            elif any(kw in str(data).lower() for kw in LOCATION_KEYWORDS):
                # Сохраняем как потенциальный ответ
                self.har_analysis.append({
                    'source_file': source_file,
                    'type': 'api_response',
                    'data': data,
                })
        
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    await self._parse_har_json(item, source_file)
    
    async def _process_har_entry(self, entry: Dict, source_file: str):
        """Обрабатывает одну запись из HAR"""
        request = entry.get('request', {})
        response = entry.get('response', {})
        
        url = request.get('url', '')
        method = request.get('method', 'GET')
        
        # Проверяем на наличие ключевых слов в URL или body
        url_lower = url.lower()
        body_text = ''
        
        if 'postData' in request:
            body_text = request['postData'].get('text', '').lower()
        
        # Проверяем response body
        if 'content' in response:
            response_text = response['content'].get('text', '').lower()
            body_text += ' ' + response_text
        
        # Ищем ключевые слова
        if any(kw in url_lower or kw in body_text for kw in LOCATION_KEYWORDS):
            # Извлекаем заголовки
            headers = {}
            for header in request.get('headers', []):
                name = header.get('name', '')
                value = header.get('value', '')
                if name.lower() not in ['cookie', 'authorization']:  # Не логируем чувствительные данные
                    headers[name] = value
            
            # Извлекаем body
            request_body = None
            if 'postData' in request:
                request_body = request['postData'].get('text', '')
            
            # Извлекаем response
            response_status = response.get('status', 0)
            response_body = ''
            if 'content' in response:
                response_body = response['content'].get('text', '')[:4000]
            
            self.har_analysis.append({
                'source_file': source_file,
                'method': method,
                'url': url,
                'request_headers': headers,
                'request_body': request_body,
                'response_status': response_status,
                'response_body': response_body[:4000] if response_body else None,
            })
    
    async def _parse_text_log(self, content: str, source_file: str):
        """Парсит текстовый лог для поиска URL'ов"""
        # Ищем URL'ы с ключевыми словами
        url_pattern = re.compile(r'https?://[^\s<>"\'\)]+')
        urls = url_pattern.findall(content)
        
        for url in urls:
            url_lower = url.lower()
            if any(kw in url_lower for kw in LOCATION_KEYWORDS):
                # Пробуем извлечь метод из контекста
                method = 'GET'
                if 'POST' in content[max(0, content.find(url) - 50):content.find(url)]:
                    method = 'POST'
                
                self.har_analysis.append({
                    'source_file': source_file,
                    'method': method,
                    'url': url,
                    'request_headers': {},
                    'request_body': None,
                    'response_status': 0,
                    'response_body': None,
                })
    
    async def extract_html_slugs(self, artifacts: Dict[str, List[Path]]):
        """Извлекает slug'и из HTML файлов"""
        logger.info("Step 3: Извлечение slug'ов из HTML...")
        
        for html_file in artifacts['html']:
            try:
                logger.info(f"Парсинг HTML: {html_file.name}")
                
                with open(html_file, 'r', encoding='utf-8', errors='ignore') as f:
                    html_content = f.read()
                
                await self._parse_html_content(html_content, str(html_file))
            
            except Exception as e:
                logger.warning(f"Ошибка парсинга HTML {html_file}: {e}")
        
        logger.info(f"Найдено {len(self.html_slug_samples)} slug'ов в HTML")
    
    async def _parse_html_content(self, html: str, source_file: str):
        """Парсит HTML контент для поиска slug'ов и labels"""
        # Ищем JSON в script тегах
        script_pattern = re.compile(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL | re.IGNORECASE)
        script_matches = script_pattern.findall(html)
        
        for script_content in script_matches:
            try:
                data = json.loads(script_content)
                await self._extract_from_json_data(data, source_file, html)
            except json.JSONDecodeError:
                pass
        
        # Ищем большие JSON блоки
        json_pattern = re.compile(r'\{[^{}]*"province"[^{}]*\}', re.IGNORECASE | re.DOTALL)
        json_matches = json_pattern.findall(html)
        
        for json_str in json_matches:
            try:
                data = json.loads(json_str)
                await self._extract_from_json_data(data, source_file, html)
            except json.JSONDecodeError:
                pass
        
        # Ищем slug'и напрямую в HTML
        slug_matches = SLUG_PATTERN.findall(html)
        for slug in slug_matches:
            await self._extract_slug_with_context(slug, html, source_file)
    
    async def _extract_from_json_data(self, data: Any, source_file: str, html: str):
        """Извлекает данные из JSON структуры"""
        if isinstance(data, dict):
            # Рекурсивно ищем slug'и и labels
            for key, value in data.items():
                if isinstance(value, (dict, list)):
                    await self._extract_from_json_data(value, source_file, html)
                elif isinstance(value, str):
                    if SLUG_PATTERN.search(value):
                        await self._extract_slug_with_context(value, html, source_file)
                    elif RUSSIAN_LABEL_PATTERN.search(value):
                        # Может быть label для slug'а
                        pass
        
        elif isinstance(data, list):
            for item in data:
                await self._extract_from_json_data(item, source_file, html)
    
    async def _extract_slug_with_context(self, slug: str, html: str, source_file: str):
        """Извлекает slug с контекстом (label)"""
        # Находим позицию slug'а в HTML
        slug_pos = html.find(slug)
        if slug_pos == -1:
            return
        
        # Берем контекст ±500 символов
        context_start = max(0, slug_pos - 500)
        context_end = min(len(html), slug_pos + len(slug) + 500)
        context = html[context_start:context_end]
        
        # Ищем русский label рядом
        label_match = RUSSIAN_LABEL_PATTERN.search(context)
        label_ru = label_match.group(0) if label_match else None
        
        # Ищем белорусский label (может быть в другом месте)
        label_by = None
        
        # Извлекаем snippet
        snippet = context[:200] + '...' if len(context) > 200 else context
        
        self.html_slug_samples.append({
            'slug': slug,
            'label_ru': label_ru,
            'label_by': label_by,
            'source_file': source_file,
            'context_snippet': snippet,
        })
    
    async def test_endpoints(self):
        """Тестирует найденные candidate endpoints"""
        logger.info("Step 4: Тестирование endpoints...")
        
        # Собираем уникальные endpoints
        endpoints = {}
        for entry in self.har_analysis:
            url = entry.get('url', '')
            if not url:
                continue
            
            # Нормализуем URL (убираем query params для группировки)
            parsed = urlparse(url)
            base_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
            
            if base_url not in endpoints:
                endpoints[base_url] = {
                    'url': url,
                    'method': entry.get('method', 'GET'),
                    'headers': entry.get('request_headers', {}),
                    'params': parse_qs(parsed.query),
                }
        
        # Добавляем известные endpoints из кода
        known_endpoints = [
            {
                'url': 'https://api.kufar.by/search-api/v1/autocomplete/location',
                'method': 'GET',
                'headers': {},
                'params': {'q': 'Полоцк'},
            },
            {
                'url': 'https://www.kufar.by/api/search/locations',
                'method': 'GET',
                'headers': {},
                'params': {'query': 'Полоцк'},
            },
        ]
        
        for ep in known_endpoints:
            base_url = urlparse(ep['url']).path
            if base_url not in endpoints:
                endpoints[base_url] = ep
        
        logger.info(f"Тестирование {len(endpoints)} endpoints...")
        
        # Тестируем каждый endpoint
        for base_url, ep_config in list(endpoints.items())[:50]:  # Лимит 50 endpoints
            if self.request_count >= MAX_REQUESTS_TOTAL:
                logger.warning("Достигнут лимит запросов")
                break
            
            host = urlparse(ep_config['url']).netloc
            if host in self.blocked_hosts:
                logger.info(f"Пропускаем заблокированный хост: {host}")
                continue
            
            await self._test_single_endpoint(ep_config)
            await self.rate_limiter.wait()
    
    async def _test_single_endpoint(self, ep_config: Dict):
        """Тестирует один endpoint"""
        url = ep_config['url']
        method = ep_config['method']
        headers = {**DEFAULT_HEADERS, **ep_config.get('headers', {})}
        params = ep_config.get('params', {})
        
        # Если есть query params в URL, добавляем их
        parsed = urlparse(url)
        if parsed.query:
            existing_params = parse_qs(parsed.query)
            params = {**existing_params, **params}
        
        # Формируем финальный URL
        if params and method == 'GET':
            query_string = urlencode(params, doseq=True)
            final_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{query_string}"
        else:
            final_url = url
        
        logger.info(f"Тест: {method} {final_url}")
        
        start_time = time.time()
        result = {
            'url': final_url,
            'method': method,
            'request_headers': headers,
            'status': None,
            'response_headers': {},
            'body': None,
            'latency_ms': 0,
            'error': None,
        }
        
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                for attempt in range(MAX_RETRIES):
                    try:
                        if method == 'GET':
                            async with session.get(final_url, headers=headers) as response:
                                result['status'] = response.status
                                result['response_headers'] = dict(response.headers)
                                body_text = await response.text()
                                result['body'] = body_text[:5000]
                                
                                # Парсим JSON если возможно
                                if response.status == 200:
                                    try:
                                        json_data = await response.json()
                                        await self._extract_city_data_from_response(json_data, final_url)
                                    except:
                                        pass
                                
                                break
                        else:
                            # POST запрос
                            body = ep_config.get('request_body')
                            async with session.post(final_url, headers=headers, data=body) as response:
                                result['status'] = response.status
                                result['response_headers'] = dict(response.headers)
                                body_text = await response.text()
                                result['body'] = body_text[:5000]
                                
                                if response.status == 200:
                                    try:
                                        json_data = await response.json()
                                        await self._extract_city_data_from_response(json_data, final_url)
                                    except:
                                        pass
                                
                                break
                    
                    except asyncio.TimeoutError:
                        if attempt == MAX_RETRIES - 1:
                            result['error'] = 'timeout'
                        else:
                            await asyncio.sleep(2 ** attempt)
                    
                    except Exception as e:
                        if attempt == MAX_RETRIES - 1:
                            result['error'] = str(e)
                        else:
                            await asyncio.sleep(2 ** attempt)
            
            # Проверяем на блокировку
            if result['status'] in [403, 429]:
                host = urlparse(final_url).netloc
                self.blocked_hosts.add(host)
                logger.warning(f"Хост {host} заблокирован (status {result['status']}), пропускаем дальнейшие запросы")
                await asyncio.sleep(BACKOFF_ON_403_429)
            
            result['latency_ms'] = int((time.time() - start_time) * 1000)
            self.request_count += 1
        
        except Exception as e:
            result['error'] = str(e)
            logger.error(f"Ошибка теста {final_url}: {e}")
        
        self.header_tests.append(result)
    
    async def _extract_city_data_from_response(self, data: Any, source_url: str):
        """Извлекает данные о городах из JSON ответа"""
        if isinstance(data, dict):
            # Ищем поля с городами
            city_fields = ['city', 'province', 'locality', 'value', 'slug', 'id', 'name', 'locations', 'data']
            
            for key, value in data.items():
                if key.lower() in city_fields:
                    if isinstance(value, list):
                        for item in value:
                            await self._process_city_item(item, source_url)
                    elif isinstance(value, dict):
                        await self._process_city_item(value, source_url)
                else:
                    await self._extract_city_data_from_response(value, source_url)
        
        elif isinstance(data, list):
            for item in data:
                await self._extract_city_data_from_response(item, source_url)
    
    async def _process_city_item(self, item: Dict, source_url: str):
        """Обрабатывает один элемент города"""
        if not isinstance(item, dict):
            return
        
        slug = item.get('slug', '')
        name = item.get('name', '') or item.get('value', '')
        
        if slug or name:
            self.city_map_candidates.append({
                'slug': slug,
                'label_ru': name,
                'label_by': item.get('name_by', ''),
                'sample_location_string': item.get('location', ''),
                'sample_coords': {
                    'lat': item.get('lat'),
                    'lng': item.get('lng'),
                } if item.get('lat') and item.get('lng') else None,
                'source': source_url,
            })
    
    async def test_html_extract_live(self):
        """Тестирует извлечение из HTML вживую"""
        logger.info("Step 5: Тестирование HTML-extract вживую...")
        
        # Тестовые slug'и
        test_slugs = [
            'country-belarus~province-minsk~locality-minsk',
            'country-belarus~province-brestskaja_oblast~locality-baranovichi',
            'country-belarus~province-vitebskaja_oblast~locality-polotsk',
            'country-belarus~province-vitebskaja_oblast~locality-orsha',
        ]
        
        test_urls = [
            'https://re.kufar.by/',
            'https://www.kufar.by/',
        ]
        
        for url in test_urls:
            if self.request_count >= MAX_REQUESTS_TOTAL:
                break
            
            host = urlparse(url).netloc
            if host in self.blocked_hosts:
                continue
            
            await self.rate_limiter.wait()
            
            try:
                timeout = aiohttp.ClientTimeout(total=10)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(url, headers=DEFAULT_HEADERS) as response:
                        if response.status == 200:
                            html = await response.text()
                            await self._parse_html_content(html, f"live_{url}")
                            
                            # Сохраняем в header_tests
                            self.header_tests.append({
                                'url': url,
                                'method': 'GET',
                                'status': response.status,
                                'body': html[:5000],
                                'latency_ms': 0,
                            })
                            
                            self.request_count += 1
            
            except Exception as e:
                logger.error(f"Ошибка запроса {url}: {e}")
    
    async def run_headless_probe(self):
        """Опциональный headless probe через Playwright"""
        if not HEADLESS_ENABLED:
            logger.info("Step 6: Headless probe пропущен (HEADLESS=false)")
            return
        
        logger.info("Step 6: Запуск headless probe...")
        
        try:
            
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    user_agent=DEFAULT_HEADERS['User-Agent'],
                    viewport={'width': 1920, 'height': 1080},
                )
                page = await context.new_page()
                
                # Подписываемся на XHR запросы
                xhr_responses = []
                
                async def handle_response(response):
                    url = response.url
                    if any(kw in url.lower() for kw in LOCATION_KEYWORDS):
                        try:
                            body = await response.text()
                            xhr_responses.append({
                                'url': url,
                                'status': response.status,
                                'headers': dict(response.headers),
                                'body': body[:5000],
                            })
                        except:
                            pass
                
                page.on('response', handle_response)
                
                # Открываем страницу
                await page.goto('https://re.kufar.by/', wait_until='networkidle')
                await asyncio.sleep(2)
                
                # Пробуем найти поле поиска и ввести "Полоцк"
                try:
                    # Ищем поле поиска
                    search_input = await page.query_selector('input[type="search"], input[placeholder*="город"], input[name*="search"]')
                    if search_input:
                        await search_input.fill('Полоцк')
                        await asyncio.sleep(1)
                        
                        # Ждем ответов
                        await asyncio.sleep(3)
                except Exception as e:
                    logger.warning(f"Не удалось найти поле поиска: {e}")
                
                # Сохраняем HAR
                har = await context.storage_state()
                
                await browser.close()
                
                # Обрабатываем найденные XHR ответы
                for xhr in xhr_responses:
                    self.header_tests.append({
                        'url': xhr['url'],
                        'method': 'GET',
                        'status': xhr['status'],
                        'response_headers': xhr['headers'],
                        'body': xhr['body'],
                        'source': 'headless',
                    })
                    
                    # Парсим JSON если возможно
                    try:
                        json_data = json.loads(xhr['body'])
                        await self._extract_city_data_from_response(json_data, xhr['url'])
                    except:
                        pass
        
        except ImportError:
            logger.warning("Playwright не установлен, пропускаем headless probe")
        except Exception as e:
            logger.error(f"Ошибка headless probe: {e}")
    
    def aggregate_city_map(self):
        """Агрегирует city_map_candidates"""
        logger.info("Step 7: Агрегация city_map...")
        
        # Объединяем данные из разных источников
        slug_map = {}
        
        # Из HTML samples
        for sample in self.html_slug_samples:
            slug = sample['slug']
            if slug not in slug_map:
                slug_map[slug] = {
                    'slug': slug,
                    'label_ru': sample.get('label_ru'),
                    'label_by': sample.get('label_by'),
                    'sample_location_string': None,
                    'sample_coords': None,
                    'sources': [sample['source_file']],
                }
        
        # Из API responses
        for candidate in self.city_map_candidates:
            slug = candidate.get('slug', '')
            if slug:
                if slug not in slug_map:
                    slug_map[slug] = {
                        'slug': slug,
                        'label_ru': candidate.get('label_ru'),
                        'label_by': candidate.get('label_by'),
                        'sample_location_string': candidate.get('sample_location_string'),
                        'sample_coords': candidate.get('sample_coords'),
                        'sources': [],
                    }
                slug_map[slug]['sources'].append(candidate.get('source', 'unknown'))
        
        self.city_map_candidates = list(slug_map.values())
        logger.info(f"Агрегировано {len(self.city_map_candidates)} уникальных slug'ов")
    
    def generate_decision_report(self) -> str:
        """Генерирует decision report"""
        logger.info("Step 8: Генерация decision report...")
        
        report_lines = []
        report_lines.append("# Kufar City Lookup Investigation Report\n")
        report_lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}\n\n")
        
        # Анализ endpoints
        working_endpoints = [t for t in self.header_tests if t.get('status') == 200]
        json_endpoints = []
        location_endpoints = []
        
        for test in working_endpoints:
            body = test.get('body', '')
            url = test.get('url', '')
            
            # Проверяем на JSON
            try:
                json_data = json.loads(body)
                json_endpoints.append(test)
                
                # Проверяем, содержит ли ответ данные о локациях
                body_lower = str(json_data).lower()
                if any(kw in body_lower for kw in ['location', 'city', 'province', 'locality', 'slug']):
                    location_endpoints.append(test)
            except:
                pass
        
        # Проверяем известные endpoints на 404
        known_endpoints_404 = [
            t for t in self.header_tests 
            if t.get('status') == 404 and any(ep in t.get('url', '') for ep in ['autocomplete', 'locations'])
        ]
        
        report_lines.append("## Endpoint Analysis\n\n")
        
        if location_endpoints:
            report_lines.append("### ✅ USE_ENDPOINT_WITH_HEADERS (RECOMMENDED)\n\n")
            report_lines.append(f"**Confidence:** HIGH\n")
            report_lines.append(f"**Estimated Effort:** L (Low)\n\n")
            report_lines.append(f"Найдено {len(location_endpoints)} рабочих endpoint'ов с данными о локациях:\n\n")
            
            for ep in location_endpoints[:3]:  # Показываем первые 3
                report_lines.append(f"- **URL:** `{ep['url'][:100]}...`\n")
                report_lines.append(f"  - Method: {ep.get('method', 'GET')}\n")
                report_lines.append(f"  - Status: {ep.get('status')}\n")
                report_lines.append(f"  - Latency: {ep.get('latency_ms', 0)}ms\n")
                report_lines.append(f"  - Headers required: {list(ep.get('request_headers', {}).keys())[:5]}\n\n")
            
            report_lines.append("**Next Steps:**\n")
            report_lines.append("1. Использовать найденный endpoint с правильными заголовками\n")
            report_lines.append("2. Интегрировать в `scrapers/kufar.py::lookup_kufar_location_async`\n")
            report_lines.append("3. Обновить `KUFAR_SUGGEST_URLS` если нужно\n\n")
        elif known_endpoints_404:
            report_lines.append("### ❌ KNOWN_ENDPOINTS_NOT_WORKING\n\n")
            report_lines.append(f"**Confidence:** HIGH\n")
            report_lines.append(f"**Status:** Известные endpoints возвращают 404\n\n")
            report_lines.append("Проверенные endpoints:\n\n")
            for ep in known_endpoints_404:
                report_lines.append(f"- `{ep['url']}` → Status: {ep.get('status')}\n")
            report_lines.append("\n**Вывод:** API endpoints для autocomplete/locations недоступны или изменились.\n\n")
        
        # HTML extract анализ
        if self.html_slug_samples or self.city_map_candidates:
            report_lines.append("### ✅ USE_HTML_EXTRACT (RECOMMENDED)\n\n")
            confidence = 'HIGH' if len(self.city_map_candidates) > 50 else 'MEDIUM'
            report_lines.append(f"**Confidence:** {confidence}\n")
            report_lines.append(f"**Estimated Effort:** M (Medium)\n\n")
            report_lines.append(f"Найдено {len(self.html_slug_samples)} slug'ов в HTML и {len(self.city_map_candidates)} уникальных городов.\n\n")
            
            # Показываем примеры
            report_lines.append("Примеры найденных slug'ов:\n\n")
            for sample in self.city_map_candidates[:5]:
                slug = sample.get('slug', '')
                label = sample.get('label_ru', 'N/A')
                report_lines.append(f"- `{slug}` → {label}\n")
            report_lines.append("\n")
            
            report_lines.append("**Next Steps:**\n")
            report_lines.append("1. Собрать полный city_map из HTML (one-time run)\n")
            report_lines.append("2. Сохранить в таблицу `city_codes` или файл `data/kufar_city_map.json`\n")
            report_lines.append("3. Использовать как основной метод lookup (endpoints не работают)\n")
            report_lines.append("4. Обновить `_get_city_gtsy()` в `scrapers/kufar.py` для использования city_map\n\n")
            
            report_lines.append("**Integration Example:**\n")
            report_lines.append("```python\n")
            report_lines.append("# В scrapers/kufar.py\n")
            report_lines.append("def _get_city_gtsy(self, city: str | dict) -> str:\n")
            report_lines.append("    # Загрузить city_map из JSON или БД\n")
            report_lines.append("    city_map = load_city_map()  # из data/kufar_city_map.json\n")
            report_lines.append("    city_name = city if isinstance(city, str) else city.get('name', '')\n")
            report_lines.append("    city_lower = city_name.lower().strip()\n")
            report_lines.append("    return city_map.get(city_lower, city_map['барановичи'])  # fallback\n")
            report_lines.append("```\n\n")
        
        # Headless анализ
        if HEADLESS_ENABLED:
            report_lines.append("### 🔄 USE_HEADLESS (OPTIONAL)\n\n")
            report_lines.append(f"**Confidence:** LOW\n")
            report_lines.append(f"**Estimated Effort:** H (High)\n\n")
            report_lines.append("Headless браузер может использоваться для дополнительного сбора данных.\n")
            report_lines.append("**Next Steps:**\n")
            report_lines.append("1. Реализовать Playwright-based crawler\n")
            report_lines.append("2. Использовать только для one-time crawl\n")
            report_lines.append("3. Кэшировать результаты\n\n")
        
        # Рекомендация
        report_lines.append("## Final Recommendation\n\n")
        
        if location_endpoints:
            report_lines.append("**✅ USE_ENDPOINT_WITH_HEADERS**\n\n")
            report_lines.append("Использовать найденный рабочий endpoint с правильными заголовками.\n")
        elif self.city_map_candidates:
            report_lines.append("**✅ USE_HTML_EXTRACT** (PRIMARY METHOD)\n\n")
            report_lines.append("Собрать city_map из HTML и использовать как основной метод lookup.\n")
            report_lines.append(f"Найдено {len(self.city_map_candidates)} городов, что достаточно для полноценной работы.\n")
        else:
            report_lines.append("**🔄 USE_HEADLESS**\n\n")
            report_lines.append("Использовать headless браузер для one-time crawl.\n")
        
        # Статистика
        report_lines.append("\n## Statistics\n\n")
        report_lines.append(f"- HAR/Log candidates: {len(self.har_analysis)}\n")
        report_lines.append(f"- HTML slug samples: {len(self.html_slug_samples)}\n")
        report_lines.append(f"- Header tests performed: {len(self.header_tests)}\n")
        report_lines.append(f"- City map candidates: {len(self.city_map_candidates)}\n")
        report_lines.append(f"- Requests made: {self.request_count}\n")
        report_lines.append(f"- Blocked hosts: {len(self.blocked_hosts)}\n")
        
        return '\n'.join(report_lines)
    
    def save_artifacts(self):
        """Сохраняет все артефакты"""
        logger.info("Сохранение артефактов...")
        
        # har_analysis.json
        with open(self.output_dir / 'har_analysis.json', 'w', encoding='utf-8') as f:
            json.dump(self.har_analysis, f, indent=2, ensure_ascii=False)
        
        # html_slug_samples.json
        with open(self.output_dir / 'html_slug_samples.json', 'w', encoding='utf-8') as f:
            json.dump(self.html_slug_samples, f, indent=2, ensure_ascii=False)
        
        # header_tests.json
        with open(self.output_dir / 'header_tests.json', 'w', encoding='utf-8') as f:
            json.dump(self.header_tests, f, indent=2, ensure_ascii=False)
        
        # city_map_candidates.json
        with open(self.output_dir / 'city_map_candidates.json', 'w', encoding='utf-8') as f:
            json.dump(self.city_map_candidates, f, indent=2, ensure_ascii=False)
        
        # decision_report.md
        report = self.generate_decision_report()
        with open(self.output_dir / 'decision_report.md', 'w', encoding='utf-8') as f:
            f.write(report)
        
        # commands_run.txt
        with open(self.output_dir / 'commands_run.txt', 'w', encoding='utf-8') as f:
            f.write('\n'.join(self.commands_run))
        
        logger.info("Артефакты сохранены")
    
    def create_zip(self) -> Path:
        """Создает ZIP архив с результатами"""
        logger.info("Создание ZIP архива...")
        
        zip_path = Path(str(self.output_dir) + '.zip')
        
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(self.output_dir):
                for file in files:
                    file_path = Path(root) / file
                    arcname = file_path.relative_to(self.output_dir.parent)
                    zipf.write(file_path, arcname)
        
        logger.info(f"ZIP архив создан: {zip_path}")
        return zip_path
    
    async def run(self, repo_root: Path):
        """Запускает полное investigation"""
        logger.info("=" * 80)
        logger.info("Kufar City Lookup Investigation")
        logger.info("=" * 80)
        
        self.log_command("python tools/kufar_investigate.py")
        
        # Step 1: Поиск артефактов
        artifacts = await self.find_artifacts(repo_root)
        
        # Step 2: Анализ HAR/логов
        await self.analyze_har_logs(artifacts)
        
        # Step 3: Извлечение slug'ов из HTML
        await self.extract_html_slugs(artifacts)
        
        # Step 4: Тестирование endpoints
        await self.test_endpoints()
        
        # Step 5: Тестирование HTML-extract вживую
        await self.test_html_extract_live()
        
        # Step 6: Headless probe (опционально)
        await self.run_headless_probe()
        
        # Step 7: Агрегация
        self.aggregate_city_map()
        
        # Step 8: Сохранение артефактов
        self.save_artifacts()
        
        # Step 9: Создание ZIP
        zip_path = self.create_zip()
        
        logger.info("=" * 80)
        logger.info("Investigation завершено!")
        logger.info(f"Результаты: {self.output_dir}")
        logger.info(f"ZIP архив: {zip_path}")
        logger.info("=" * 80)
        
        return zip_path


async def main():
    """Главная функция"""
    # Определяем корень репозитория
    script_dir = Path(__file__).parent
    repo_root = script_dir.parent
    
    # Создаем output директорию
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    output_dir = Path('/tmp') / f'kufar_investigation_{timestamp}'
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Запускаем investigation
    runner = InvestigationRunner(output_dir)
    zip_path = await runner.run(repo_root)
    
    # Выводим сводку
    print("\n" + "=" * 80)
    print("INVESTIGATION COMPLETE")
    print("=" * 80)
    print(f"\nResults directory: {output_dir}")
    print(f"ZIP archive: {zip_path}")
    print("\nKey files:")
    print(f"  - har_analysis.json: {len(runner.har_analysis)} candidates")
    print(f"  - html_slug_samples.json: {len(runner.html_slug_samples)} slugs")
    print(f"  - header_tests.json: {len(runner.header_tests)} tests")
    print(f"  - city_map_candidates.json: {len(runner.city_map_candidates)} cities")
    print(f"  - decision_report.md: See for recommendations")
    print("\nNext steps:")
    print("  1. Review decision_report.md")
    print("  2. Check header_tests.json for working endpoints")
    print("  3. Implement recommended approach")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
