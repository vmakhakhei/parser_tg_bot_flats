#!/usr/bin/env python3
"""
Скрипт для запуска тестов проекта parser_tg_bot_flats

Использование:
    python run_tests.py                    # Все тесты
    python run_tests.py --unit             # Только unit-тесты
    python run_tests.py --coverage         # С покрытием кода
    python run_tests.py tests/scrapers/    # Конкретная директория
    python run_tests.py -k kufar           # Тесты с "kufar" в названии
"""
import sys
import subprocess
import os
from pathlib import Path


def check_dependencies():
    """Проверяет наличие необходимых зависимостей"""
    try:
        import pytest
        return True
    except ImportError:
        print("❌ pytest не установлен. Установите зависимости:")
        print("   pip install -r requirements.txt")
        return False


def run_tests(args=None):
    """Запускает тесты с переданными аргументами"""
    if not check_dependencies():
        sys.exit(1)
    
    # Базовые аргументы pytest
    pytest_args = [
        "pytest",
        "-v",  # Подробный вывод
        "--tb=short",  # Короткий traceback
        "--color=yes",  # Цветной вывод
    ]
    
    # Добавляем пользовательские аргументы
    if args:
        pytest_args.extend(args)
    else:
        # По умолчанию запускаем все тесты
        pytest_args.append("tests/")
    
    print("🧪 Запуск тестов...")
    print(f"📋 Команда: {' '.join(pytest_args)}\n")
    
    # Запускаем pytest
    result = subprocess.run(pytest_args)
    
    return result.returncode


def main():
    """Главная функция"""
    # Получаем аргументы командной строки (кроме имени скрипта)
    args = sys.argv[1:] if len(sys.argv) > 1 else None
    
    # Обработка специальных флагов
    if args:
        # Заменяем удобные флаги на аргументы pytest
        if "--unit" in args:
            args.remove("--unit")
            args.extend(["-m", "unit"])
        
        if "--coverage" in args or "--cov" in args:
            if "--coverage" in args:
                args.remove("--coverage")
            if "--cov" in args:
                args.remove("--cov")
            args.extend([
                "--cov=scrapers",
                "--cov-report=html",
                "--cov-report=term-missing"
            ])
            print("📊 Включено покрытие кода\n")
    
    exit_code = run_tests(args)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
