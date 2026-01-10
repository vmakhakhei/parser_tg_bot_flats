"""
ИИ-оценщик квартир - бесплатные варианты интеграции
"""
import os
import sys
import asyncio
import aiohttp
import json
import re
from typing import Optional, Dict, Any
from scrapers.base import Listing

# Добавляем путь для импорта error_logger
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from error_logger import log_error, log_warning, log_info
except ImportError:
    # Fallback если error_logger недоступен
    def log_error(source, msg, exc=None):
        print(f"[ERROR] [{source}] {msg}: {exc}")
    def log_warning(source, msg):
        print(f"[WARNING] [{source}] {msg}")
    def log_info(source, msg):
        print(f"[INFO] [{source}] {msg}")

# ========== ВАРИАНТ 1: Groq API (РЕКОМЕНДУЕТСЯ) ==========
# Бесплатно: 30 запросов/минуту, очень быстро
# Регистрация: https://console.groq.com/
# Получить API ключ: https://console.groq.com/keys

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.1-70b-versatile"  # или "mixtral-8x7b-32768"


# ========== ВАРИАНТ 2: Hugging Face Inference API ==========
# Бесплатно: ограниченное количество запросов
# Регистрация: https://huggingface.co/
# Получить токен: https://huggingface.co/settings/tokens

HF_API_KEY = os.getenv("HF_API_KEY", "")
HF_API_URL = "https://api-inference.huggingface.co/models/meta-llama/Meta-Llama-3-8B-Instruct"


# ========== ВАРИАНТ 3: Google Gemini API ==========
# Бесплатно: 60 запросов/минуту
# Регистрация: https://makersuite.google.com/app/apikey
# Получить API ключ: https://aistudio.google.com/app/apikey

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent"


# ========== ВАРИАНТ 4: Ollama (локально) ==========
# Полностью бесплатно, но нужно запускать локально
# Установка: https://ollama.ai/
# Запуск: ollama run llama3

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")


class AIValuator:
    """ИИ-оценщик квартир"""
    
    def __init__(self, provider: str = "groq"):
        """
        Инициализация оценщика
        
        Args:
            provider: "groq", "huggingface", "gemini", "ollama"
        """
        self.provider = provider.lower()
        self.session: Optional[aiohttp.ClientSession] = None
    
    async def start_session(self):
        """Создает HTTP сессию"""
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
    
    async def close_session(self):
        """Закрывает HTTP сессию"""
        if self.session and not self.session.closed:
            await self.session.close()
    
    async def __aenter__(self):
        await self.start_session()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close_session()
    
    def _prepare_prompt(self, listing: Listing) -> str:
        """Подготавливает промпт для ИИ"""
        # Определяем цену за м²
        price_per_sqm = listing.price_per_sqm_usd if listing.price_per_sqm_usd else (
            int(listing.price_per_sqm_byn / 2.95) if listing.price_per_sqm_byn else 0
        )
        price_per_sqm_text = f"{price_per_sqm} USD/м²" if price_per_sqm > 0 else "не указана"
        
        # Определяем текущую цену в USD
        current_price_usd = listing.price_usd if listing.price_usd else (
            int(listing.price_byn / 2.95) if listing.price_byn else 0
        )
        
        prompt = f"""Ты эксперт по оценке недвижимости в Барановичах, Беларусь.

Параметры квартиры:
- Комнат: {listing.rooms}
- Площадь: {listing.area} м²
- Этаж: {listing.floor if listing.floor else 'не указан'}
- Год постройки: {listing.year_built if listing.year_built else 'не указан'}
- Адрес: {listing.address}
- Цена за м²: {price_per_sqm_text}
- Текущая цена: {listing.price_formatted} (≈${current_price_usd:,} USD)

Средние цены в Барановичах (2024-2025):
- 1-комн: $20,000-30,000 ($400-600/м²)
- 2-комн: $30,000-45,000 ($500-700/м²)
- 3-комн: $45,000-65,000 ($600-800/м²)
- 4+ комн: $65,000+ ($700-900/м²)

Задача:
1. Оцени справедливую стоимость квартиры в USD на основе параметров и рыночных цен
2. Определи, завышена ли цена (true если текущая цена > справедливая цена + 10%)
3. Дай краткую оценку на русском языке (1-2 предложения)

ВАЖНО: Ответь ТОЛЬКО валидным JSON без дополнительного текста:
{{
    "fair_price_usd": число,
    "is_overpriced": true/false,
    "assessment": "текст оценки на русском"
}}"""
        return prompt
    
    async def valuate_groq(self, listing: Listing) -> Optional[Dict[str, Any]]:
        """Оценка через Groq API"""
        if not GROQ_API_KEY:
            return None
        
        prompt = self._prepare_prompt(listing)
        
        payload = {
            "messages": [
                {"role": "system", "content": "Ты эксперт по оценке недвижимости в Беларуси. Отвечай только JSON."},
                {"role": "user", "content": prompt}
            ],
            "model": GROQ_MODEL,
            "temperature": 0.3,
            "max_tokens": 300
        }
        
        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        }
        
        try:
            timeout = aiohttp.ClientTimeout(total=10)  # 10 секунд таймаут
            async with self.session.post(GROQ_API_URL, json=payload, headers=headers, timeout=timeout) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    content = data["choices"][0]["message"]["content"]
                    log_info("ai_groq", f"Получен ответ от Groq: {content[:100]}...")
                    # Парсим JSON из ответа
                    result = self._parse_ai_response(content)
                    if result:
                        log_info("ai_groq", f"Успешная оценка: ${result.get('fair_price_usd', 0):,}")
                    return result
                else:
                    error_text = await resp.text()
                    log_warning("ai_groq", f"Groq API вернул статус {resp.status}: {error_text[:200]}")
        except asyncio.TimeoutError:
            log_warning("ai_groq", "Таймаут запроса к Groq API")
        except Exception as e:
            log_error("ai_groq", f"Ошибка запроса к Groq API", e)
        
        return None
    
    async def valuate_huggingface(self, listing: Listing) -> Optional[Dict[str, Any]]:
        """Оценка через Hugging Face API"""
        if not HF_API_KEY:
            return None
        
        prompt = self._prepare_prompt(listing)
        
        payload = {
            "inputs": prompt,
            "parameters": {
                "max_new_tokens": 300,
                "temperature": 0.3,
                "return_full_text": False
            }
        }
        
        headers = {
            "Authorization": f"Bearer {HF_API_KEY}",
            "Content-Type": "application/json"
        }
        
        try:
            async with self.session.post(HF_API_URL, json=payload, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    content = data[0]["generated_text"]
                    return self._parse_ai_response(content)
        except Exception as e:
            print(f"[AI] HuggingFace ошибка: {e}")
        
        return None
    
    async def valuate_gemini(self, listing: Listing) -> Optional[Dict[str, Any]]:
        """Оценка через Google Gemini API"""
        if not GEMINI_API_KEY:
            return None
        
        prompt = self._prepare_prompt(listing)
        
        url = f"{GEMINI_API_URL}?key={GEMINI_API_KEY}"
        payload = {
            "contents": [{
                "parts": [{"text": prompt}]
            }]
        }
        
        try:
            async with self.session.post(url, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    content = data["candidates"][0]["content"]["parts"][0]["text"]
                    return self._parse_ai_response(content)
        except Exception as e:
            print(f"[AI] Gemini ошибка: {e}")
        
        return None
    
    async def valuate_ollama(self, listing: Listing) -> Optional[Dict[str, Any]]:
        """Оценка через Ollama (локально)"""
        prompt = self._prepare_prompt(listing)
        
        payload = {
            "model": "llama3",
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.3
            }
        }
        
        try:
            async with self.session.post(OLLAMA_URL, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    content = data["response"]
                    return self._parse_ai_response(content)
        except Exception as e:
            print(f"[AI] Ollama ошибка: {e}")
        
        return None
    
    def _parse_ai_response(self, content: str) -> Optional[Dict[str, Any]]:
        """Парсит ответ ИИ и извлекает JSON"""
        try:
            # Убираем markdown code blocks если есть
            content = re.sub(r'```json\s*', '', content)
            content = re.sub(r'```\s*', '', content)
            content = content.strip()
            
            # Пытаемся найти JSON объект (может быть многострочным)
            json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', content, re.DOTALL)
            if json_match:
                json_str = json_match.group(0)
                result = json.loads(json_str)
                
                # Валидация результата
                if "fair_price_usd" in result and isinstance(result["fair_price_usd"], (int, float)):
                    if "is_overpriced" in result and isinstance(result["is_overpriced"], bool):
                        if "assessment" in result and isinstance(result["assessment"], str):
                            return result
                
                log_warning("ai_parser", f"Неполный JSON в ответе: {result}")
            else:
                log_warning("ai_parser", f"JSON не найден в ответе: {content[:200]}")
        except json.JSONDecodeError as e:
            log_error("ai_parser", f"Ошибка парсинга JSON: {content[:200]}", e)
        except Exception as e:
            log_error("ai_parser", f"Ошибка парсинга ответа", e)
        
        return None
    
    async def valuate(self, listing: Listing) -> Optional[Dict[str, Any]]:
        """Оценивает квартиру используя выбранный провайдер"""
        await self.start_session()
        
        if self.provider == "groq":
            return await self.valuate_groq(listing)
        elif self.provider == "huggingface":
            return await self.valuate_huggingface(listing)
        elif self.provider == "gemini":
            return await self.valuate_gemini(listing)
        elif self.provider == "ollama":
            return await self.valuate_ollama(listing)
        
        return None


# Глобальный экземпляр
_valuator: Optional[AIValuator] = None


def get_valuator() -> Optional[AIValuator]:
    """Получает глобальный экземпляр оценщика"""
    global _valuator
    
    if _valuator is None:
        # Определяем провайдер по наличию ключей
        if GROQ_API_KEY:
            _valuator = AIValuator("groq")
        elif GEMINI_API_KEY:
            _valuator = AIValuator("gemini")
        elif HF_API_KEY:
            _valuator = AIValuator("huggingface")
        elif os.getenv("OLLAMA_URL"):
            _valuator = AIValuator("ollama")
    
    return _valuator


async def valuate_listing(listing: Listing) -> Optional[Dict[str, Any]]:
    """Оценивает объявление"""
    valuator = get_valuator()
    if not valuator:
        return None
    
    async with valuator:
        return await valuator.valuate(listing)

