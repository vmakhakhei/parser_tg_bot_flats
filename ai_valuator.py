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
# Актуальные модели Groq (2026):
# - llama-3.1-8b-instant (быстрая, стабильная)
# - mixtral-8x7b-32768 (стабильная, хорошее качество)
# - llama-3.3-70b-versatile (если доступна)
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")  # По умолчанию используем стабильную модель
GROQ_VISION_MODEL = "llama-3.2-90b-vision-preview"  # Vision модель для анализа фото (если доступна)
GROQ_FALLBACK_MODEL = "mixtral-8x7b-32768"  # Резервная модель


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
        # Определяем текущую цену в USD
        current_price_usd = listing.price_usd if listing.price_usd else (
            int(listing.price_byn / 2.95) if listing.price_byn else (
                int(listing.price / 2.95) if listing.currency == "BYN" else listing.price
            )
        )
        
        # Определяем цену за м² в USD
        price_per_sqm_usd = 0
        if listing.price_per_sqm > 0:
            # Если цена за м² указана в основной валюте
            if listing.currency == "USD":
                price_per_sqm_usd = listing.price_per_sqm
            elif listing.currency == "BYN":
                price_per_sqm_usd = int(listing.price_per_sqm / 2.95)
        elif listing.area > 0 and current_price_usd > 0:
            # Вычисляем цену за м² из общей цены
            price_per_sqm_usd = int(current_price_usd / listing.area)
        
        price_per_sqm_text = f"{price_per_sqm_usd} USD/м²" if price_per_sqm_usd > 0 else "не указана"
        
        # Анализируем этаж для оценки
        floor_info = ""
        floor_bonus = 0
        if listing.floor:
            try:
                floor_parts = listing.floor.split("/")
                if len(floor_parts) == 2:
                    floor_num = int(floor_parts[0])
                    total_floors = int(floor_parts[1])
                    if floor_num == 1:
                        floor_info = " (первый этаж - минус 5-10%)"
                        floor_bonus = -5
                    elif floor_num == total_floors:
                        floor_info = " (последний этаж - минус 3-5%)"
                        floor_bonus = -3
                    elif 2 <= floor_num <= total_floors - 1:
                        floor_info = " (средний этаж - оптимально)"
                        floor_bonus = 0
            except:
                pass
        
        # Анализируем год постройки
        year_info = ""
        year_bonus = 0
        if listing.year_built:
            try:
                year = int(listing.year_built)
                if year >= 2010:
                    year_info = " (новая постройка +5-10%)"
                    year_bonus = 5
                elif year >= 2000:
                    year_info = " (современная постройка)"
                    year_bonus = 0
                elif year >= 1980:
                    year_info = " (старая постройка -3-5%)"
                    year_bonus = -3
                else:
                    year_info = " (очень старая постройка -5-10%)"
                    year_bonus = -5
            except:
                pass
        
        # Анализируем описание
        description_text = listing.description.strip() if listing.description else ""
        description_analysis = ""
        renovation_state = "не указано"
        renovation_bonus = 0
        
        if description_text:
            desc_lower = description_text.lower()
            # Определяем состояние ремонта из описания
            if any(word in desc_lower for word in ['евроремонт', 'евро ремонт', 'капитальный ремонт', 'новый ремонт', 'свежий ремонт']):
                renovation_state = "отличное (евроремонт)"
                renovation_bonus = 10
            elif any(word in desc_lower for word in ['хороший ремонт', 'качественный ремонт', 'современный ремонт']):
                renovation_state = "хорошее"
                renovation_bonus = 5
            elif any(word in desc_lower for word in ['требует ремонта', 'нужен ремонт', 'под ремонт', 'требует косметического ремонта']):
                renovation_state = "требует ремонта"
                renovation_bonus = -10
            elif any(word in desc_lower for word in ['без ремонта', 'старый ремонт', 'советский ремонт']):
                renovation_state = "плохое"
                renovation_bonus = -15
            else:
                renovation_state = "среднее"
                renovation_bonus = 0
            
            # Извлекаем ключевые особенности из описания
            features = []
            if 'балкон' in desc_lower or 'лоджия' in desc_lower:
                features.append("балкон/лоджия")
            if 'парковка' in desc_lower or 'гараж' in desc_lower:
                features.append("парковка")
            if 'лифт' in desc_lower:
                features.append("лифт")
            if 'охрана' in desc_lower or 'консьерж' in desc_lower:
                features.append("охрана")
            if 'школа' in desc_lower or 'сад' in desc_lower:
                features.append("рядом школа/сад")
            
            if features:
                description_analysis = f"\n- Особенности: {', '.join(features)}"
        
        prompt = f"""Ты профессиональный оценщик недвижимости с 10+ летним опытом работы на рынке Барановичей, Беларусь.
Твоя задача - помочь пользователю принять правильное решение о покупке квартиры.

ОЦЕНИВАЕМЫЙ ОБЪЕКТ:
- Комнат: {listing.rooms}
- Площадь: {listing.area} м²
- Этаж: {listing.floor if listing.floor else 'не указан'}{floor_info}
- Год постройки: {listing.year_built if listing.year_built else 'не указан'}{year_info}
- Адрес: {listing.address}
- Цена за м²: {price_per_sqm_text}
- Текущая цена: {listing.price_formatted} (≈${current_price_usd:,} USD)
- Состояние ремонта: {renovation_state}{description_analysis}
{f'- Описание: {description_text[:500]}' if description_text else ''}

РЫНОЧНЫЕ ДАННЫЕ БАРАНОВИЧЕЙ (2024-2025):
Средние цены по типам квартир:
- 1-комн: $20,000-35,000 ($450-650/м²)
- 2-комн: $30,000-50,000 ($550-750/м²)
- 3-комн: $45,000-70,000 ($600-850/м²)
- 4+ комн: $65,000-100,000 ($700-950/м²)

ФАКТОРЫ ВЛИЯНИЯ НА ЦЕНУ:
1. Площадь: базовая цена зависит от количества комнат и площади
2. Этаж: первый и последний этажи дешевле на 3-10%
3. Год постройки: новые дома (2010+) дороже на 5-10%, старые (до 1980) дешевле на 5-10%
4. Район: центр дороже на 10-15%, окраины дешевле на 5-10%
5. Состояние ремонта: евроремонт +10%, хороший +5%, средний 0%, требует ремонта -10%, плохое -15%

ЗАДАЧА:
1. Рассчитай справедливую рыночную стоимость квартиры в USD, учитывая ВСЕ факторы (включая состояние ремонта)
2. Определи, завышена ли цена: true если текущая цена > справедливая цена + 10%, иначе false
3. Дай ДЕТАЛЬНУЮ оценку на русском языке (2-3 предложения):
   - Почему цена справедлива или завышена
   - Что влияет на стоимость (ремонт, район, этаж и т.д.)
   - Полезные советы для покупателя (что проверить, на что обратить внимание)
4. Оцени состояние ремонта по описанию (если указано): "отличное", "хорошее", "среднее", "требует ремонта", "плохое"
5. Дай рекомендации: стоит ли покупать, что проверить перед покупкой

ВАЖНО: Ответь ТОЛЬКО валидным JSON без дополнительного текста до или после:
{{
    "fair_price_usd": число,
    "is_overpriced": true/false,
    "assessment": "детальная оценка на русском (2-3 предложения)",
    "renovation_state": "отличное/хорошее/среднее/требует ремонта/плохое",
    "recommendations": "полезные советы для покупателя (что проверить, на что обратить внимание)",
    "value_score": число от 1 до 10 (10 = отличное соотношение цена/качество)
}}"""
        return prompt
    
    async def valuate_groq(self, listing: Listing) -> Optional[Dict[str, Any]]:
        """Оценка через Groq API"""
        if not GROQ_API_KEY:
            return None
        
        prompt = self._prepare_prompt(listing)
        
        payload = {
            "messages": [
                {
                    "role": "system", 
                    "content": """Ты профессиональный оценщик недвижимости с 10+ летним опытом работы на рынке Барановичей, Беларусь.
Твоя задача - помочь пользователю принять правильное решение о покупке квартиры.
Ты даешь детальные, полезные оценки с советами и рекомендациями.
Всегда отвечай ТОЛЬКО валидным JSON без дополнительного текста.
Формат ответа: {
    "fair_price_usd": число,
    "is_overpriced": true/false,
    "assessment": "детальная оценка на русском (2-3 предложения)",
    "renovation_state": "отличное/хорошее/среднее/требует ремонта/плохое",
    "recommendations": "полезные советы для покупателя",
    "value_score": число от 1 до 10
}"""
                },
                {"role": "user", "content": prompt}
            ],
            "model": GROQ_MODEL,
            "temperature": 0.2,  # Снижена для более точных оценок
            "max_tokens": 600  # Увеличено для детальных ответов
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
                elif resp.status == 400:
                    # Если модель недоступна, пробуем резервную
                    error_text = await resp.text()
                    log_warning("ai_groq", f"Groq API вернул статус {resp.status}: {error_text[:200]}")
                    if "decommissioned" in error_text.lower() or "not found" in error_text.lower():
                        log_info("ai_groq", f"Пробую резервную модель: {GROQ_FALLBACK_MODEL}")
                        payload["model"] = GROQ_FALLBACK_MODEL
                        async with self.session.post(GROQ_API_URL, json=payload, headers=headers, timeout=timeout) as resp2:
                            if resp2.status == 200:
                                data = await resp2.json()
                                content = data["choices"][0]["message"]["content"]
                                return self._parse_ai_response(content)
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

