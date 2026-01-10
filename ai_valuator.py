"""
ИИ-оценщик квартир - бесплатные варианты интеграции
"""
import os
import aiohttp
import json
from typing import Optional, Dict, Any
from scrapers.base import Listing

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
        prompt = f"""Оцени стоимость квартиры в Барановичах, Беларусь.

Параметры квартиры:
- Комнат: {listing.rooms}
- Площадь: {listing.area} м²
- Этаж: {listing.floor if listing.floor else 'не указан'}
- Год постройки: {listing.year_built if listing.year_built else 'не указан'}
- Адрес: {listing.address}
- Цена за м²: {listing.price_per_sqm_usd if listing.price_per_sqm_usd else listing.price_per_sqm_byn} {'USD' if listing.price_per_sqm_usd else 'BYN'}/м²
- Текущая цена: {listing.price_formatted}

Задача:
1. Оцени справедливую стоимость квартиры в USD
2. Определи, завышена ли цена (да/нет)
3. Дай краткую оценку (1-2 предложения)

Ответ в формате JSON:
{{
    "fair_price_usd": число,
    "is_overpriced": true/false,
    "assessment": "текст оценки"
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
            async with self.session.post(GROQ_API_URL, json=payload, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    content = data["choices"][0]["message"]["content"]
                    # Парсим JSON из ответа
                    return self._parse_ai_response(content)
        except Exception as e:
            print(f"[AI] Groq ошибка: {e}")
        
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
            # Пытаемся найти JSON в ответе
            import re
            json_match = re.search(r'\{[^}]+\}', content, re.DOTALL)
            if json_match:
                json_str = json_match.group(0)
                return json.loads(json_str)
        except Exception as e:
            print(f"[AI] Ошибка парсинга ответа: {e}")
        
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

