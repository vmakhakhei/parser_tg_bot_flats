#!/usr/bin/env python3
"""
Скрипт для чтения и анализа логов бота
"""
import os
import sys
import re
from datetime import datetime
from collections import defaultdict
from typing import List, Dict, Optional
import argparse


class LogReader:
    """Класс для чтения и анализа логов"""
    
    # ANSI цвета для терминала
    COLORS = {
        'ERROR': '\033[91m',      # Красный
        'WARNING': '\033[93m',    # Желтый
        'INFO': '\033[94m',        # Синий
        'DEBUG': '\033[90m',      # Серый
        'RESET': '\033[0m',       # Сброс
        'BOLD': '\033[1m',        # Жирный
        'GREEN': '\033[92m',      # Зеленый
    }
    
    def __init__(self, log_file: str = 'bot.log'):
        self.log_file = log_file
        self.logs: List[Dict] = []
    
    def read_logs(self, lines: Optional[int] = None) -> bool:
        """Читает логи из файла"""
        if not os.path.exists(self.log_file):
            print(f"{self.COLORS['WARNING']}⚠️  Файл {self.log_file} не найден{self.COLORS['RESET']}")
            print(f"{self.COLORS['INFO']}💡 Бот еще не создал файл логов. Запустите бота для создания логов.{self.COLORS['RESET']}")
            return False
        
        try:
            with open(self.log_file, 'r', encoding='utf-8') as f:
                all_lines = f.readlines()
            
            # Если указано количество строк, берем последние N
            if lines:
                all_lines = all_lines[-lines:]
            
            # Парсим логи
            log_pattern = re.compile(
                r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) - ([^-]+) - (\w+) - (.+)'
            )
            
            for line in all_lines:
                line = line.strip()
                if not line:
                    continue
                
                match = log_pattern.match(line)
                if match:
                    timestamp, name, level, message = match.groups()
                    self.logs.append({
                        'timestamp': timestamp,
                        'name': name.strip(),
                        'level': level,
                        'message': message,
                        'raw': line
                    })
                else:
                    # Многострочные сообщения или нестандартный формат
                    if self.logs:
                        self.logs[-1]['message'] += '\n' + line
                    else:
                        self.logs.append({
                            'timestamp': '',
                            'name': 'unknown',
                            'level': 'INFO',
                            'message': line,
                            'raw': line
                        })
            
            return True
        except Exception as e:
            print(f"{self.COLORS['ERROR']}❌ Ошибка при чтении файла: {e}{self.COLORS['RESET']}")
            return False
    
    def filter_by_level(self, level: str) -> List[Dict]:
        """Фильтрует логи по уровню"""
        return [log for log in self.logs if log['level'] == level.upper()]
    
    def filter_by_source(self, source: str) -> List[Dict]:
        """Фильтрует логи по источнику (name)"""
        return [log for log in self.logs if source.lower() in log['name'].lower()]
    
    def search_in_message(self, query: str) -> List[Dict]:
        """Ищет логи по тексту в сообщении"""
        query_lower = query.lower()
        return [log for log in self.logs if query_lower in log['message'].lower()]
    
    def get_stats(self) -> Dict:
        """Возвращает статистику логов"""
        stats = {
            'total': len(self.logs),
            'by_level': defaultdict(int),
            'by_source': defaultdict(int),
            'errors': [],
            'warnings': []
        }
        
        for log in self.logs:
            stats['by_level'][log['level']] += 1
            stats['by_source'][log['name']] += 1
            
            if log['level'] == 'ERROR':
                stats['errors'].append(log)
            elif log['level'] == 'WARNING':
                stats['warnings'].append(log)
        
        return stats
    
    def print_log(self, log: Dict, show_timestamp: bool = True, show_source: bool = True):
        """Выводит один лог с цветами"""
        level = log['level']
        color = self.COLORS.get(level, self.COLORS['RESET'])
        
        parts = []
        if show_timestamp and log['timestamp']:
            parts.append(f"{self.COLORS['DEBUG']}{log['timestamp']}{self.COLORS['RESET']}")
        if show_source:
            parts.append(f"{self.COLORS['DEBUG']}[{log['name']}]{self.COLORS['RESET']}")
        
        level_str = f"{color}{self.COLORS['BOLD']}{level:8}{self.COLORS['RESET']}"
        parts.append(level_str)
        parts.append(log['message'])
        
        print(' - '.join(parts))
    
    def print_logs(self, logs: Optional[List[Dict]] = None, limit: Optional[int] = None):
        """Выводит логи"""
        if logs is None:
            logs = self.logs
        
        if limit:
            logs = logs[-limit:]
        
        if not logs:
            print(f"{self.COLORS['INFO']}📭 Логов не найдено{self.COLORS['RESET']}")
            return
        
        for log in logs:
            self.print_log(log)
    
    def print_stats(self):
        """Выводит статистику"""
        stats = self.get_stats()
        
        print(f"\n{self.COLORS['BOLD']}{'='*60}{self.COLORS['RESET']}")
        print(f"{self.COLORS['BOLD']}📊 СТАТИСТИКА ЛОГОВ{self.COLORS['RESET']}")
        print(f"{self.COLORS['BOLD']}{'='*60}{self.COLORS['RESET']}\n")
        
        print(f"{self.COLORS['GREEN']}Всего записей:{self.COLORS['RESET']} {stats['total']}")
        print(f"\n{self.COLORS['BOLD']}По уровням:{self.COLORS['RESET']}")
        for level, count in sorted(stats['by_level'].items()):
            color = self.COLORS.get(level, '')
            print(f"  {color}{level:8}{self.COLORS['RESET']}: {count}")
        
        print(f"\n{self.COLORS['BOLD']}По источникам (топ-10):{self.COLORS['RESET']}")
        sorted_sources = sorted(stats['by_source'].items(), key=lambda x: x[1], reverse=True)[:10]
        for source, count in sorted_sources:
            print(f"  {self.COLORS['DEBUG']}{source}{self.COLORS['RESET']}: {count}")
        
        if stats['errors']:
            print(f"\n{self.COLORS['ERROR']}{self.COLORS['BOLD']}❌ Ошибок:{self.COLORS['RESET']} {len(stats['errors'])}")
        
        if stats['warnings']:
            print(f"{self.COLORS['WARNING']}{self.COLORS['BOLD']}⚠️  Предупреждений:{self.COLORS['RESET']} {len(stats['warnings'])}")
        
        print()


def main():
    """Главная функция"""
    parser = argparse.ArgumentParser(
        description='Чтение и анализ логов бота',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры использования:
  python3 read_debug_logs.py                    # Показать все логи
  python3 read_debug_logs.py -n 50              # Последние 50 строк
  python3 read_debug_logs.py -l ERROR           # Только ошибки
  python3 read_debug_logs.py -l WARNING         # Только предупреждения
  python3 read_debug_logs.py -s bot             # Логи от модуля bot
  python3 read_debug_logs.py -g "ошибка"        # Поиск по тексту
  python3 read_debug_logs.py --stats            # Только статистика
  python3 read_debug_logs.py -f custom.log      # Другой файл логов
        """
    )
    
    parser.add_argument(
        '-f', '--file',
        default='bot.log',
        help='Путь к файлу логов (по умолчанию: bot.log)'
    )
    
    parser.add_argument(
        '-n', '--lines',
        type=int,
        help='Количество последних строк для чтения'
    )
    
    parser.add_argument(
        '-l', '--level',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
        help='Фильтр по уровню логирования'
    )
    
    parser.add_argument(
        '-s', '--source',
        help='Фильтр по источнику (имя модуля)'
    )
    
    parser.add_argument(
        '-g', '--grep',
        help='Поиск по тексту в сообщении'
    )
    
    parser.add_argument(
        '--stats',
        action='store_true',
        help='Показать только статистику'
    )
    
    parser.add_argument(
        '--no-color',
        action='store_true',
        help='Отключить цветной вывод'
    )
    
    args = parser.parse_args()
    
    # Отключаем цвета если нужно
    if args.no_color:
        LogReader.COLORS = {k: '' for k in LogReader.COLORS}
    
    reader = LogReader(args.file)
    
    # Читаем логи
    if not reader.read_logs(args.lines):
        sys.exit(1)
    
    # Если только статистика
    if args.stats:
        reader.print_stats()
        return
    
    # Применяем фильтры
    filtered_logs = reader.logs
    
    if args.level:
        filtered_logs = reader.filter_by_level(args.level)
    
    if args.source:
        filtered_logs = [log for log in filtered_logs if args.source.lower() in log['name'].lower()]
    
    if args.grep:
        filtered_logs = reader.search_in_message(args.grep)
    
    # Выводим логи
    if filtered_logs:
        print(f"\n{LogReader.COLORS['BOLD']}{'='*60}{LogReader.COLORS['RESET']}")
        print(f"{LogReader.COLORS['BOLD']}📋 ЛОГИ{LogReader.COLORS['RESET']}")
        print(f"{LogReader.COLORS['BOLD']}{'='*60}{LogReader.COLORS['RESET']}\n")
        
        reader.print_logs(filtered_logs)
        
        # Показываем статистику в конце
        if not args.level and not args.source and not args.grep:
            reader.print_stats()
    else:
        print(f"{LogReader.COLORS['WARNING']}⚠️  Логи не найдены по заданным критериям{LogReader.COLORS['RESET']}")


if __name__ == "__main__":
    main()
