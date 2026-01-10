"""
Модуль парсеров для различных сайтов недвижимости
"""
from scrapers.base import BaseScraper, Listing
from scrapers.kufar import KufarScraper
from scrapers.realt import RealtByScraper
from scrapers.domovita import DomovitaScraper
from scrapers.onliner import OnlinerRealtScraper
from scrapers.gohome import GoHomeScraper
from scrapers.hata import HataScraper
from scrapers.etagi import EtagiScraper

__all__ = [
    'BaseScraper',
    'Listing',
    'KufarScraper',
    'RealtByScraper', 
    'DomovitaScraper',
    'OnlinerRealtScraper',
    'GoHomeScraper',
    'HataScraper',
    'EtagiScraper',
]

