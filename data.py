import requests
import pandas as pd
import time
import logging
from functools import lru_cache, wraps
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
import random

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Конфигурация
BINANCE_API = "https://data-api.binance.vision"
BYBIT_API = "https://api.bybit.com"
COINGECKO_API = "https://api.coingecko.com/api/v3"

# Кэш для данных (in-memory)
_cache = {}
_cache_ttl = {}  # время истечения

def retry(max_retries=3, delay=1, backoff=2):
    """Декоратор для повторных попыток при ошибках."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            _delay = delay
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_retries - 1:
                        raise
                    logger.warning(f"Retry {attempt+1}/{max_retries} for {func.__name__}: {e}")
                    time.sleep(_delay)
                    _delay *= backoff
            return None
        return wrapper
    return decorator

class DataFetcher:
    """Класс для получения рыночных данных с кэшированием и повторными попытками."""
    
    def __init__(self, cache_ttl: int = 60):  # TTL в секундах
        self.cache_ttl = cache_ttl
    
    @retry(max_retries=3, delay=1)
    def fetch_binance_klines(self, symbol: str, interval: str = '15m', limit: int = 200) -> Optional[pd.DataFrame]:
        """Получает данные с Binance и возвращает DataFrame с колонками OHLCV."""
        url = f"{BINANCE_API}/api/v3/klines"
        params = {
            'symbol': symbol,
            'interval': interval,
            'limit': limit
        }
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        
        if not isinstance(data, list) or len(data) < 10:
            logger.warning(f"Binance: insufficient data for {symbol} ({interval})")
            return None
        
        df = pd.DataFrame(data, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_asset_volume', 'number_of_trades',
            'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
        ])
        # Конвертация типов
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = df[col].astype(float)
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        return df[['open', 'high', 'low', 'close', 'volume']]
    
    @retry(max_retries=2, delay=1)
    def fetch_bybit_klines(self, symbol: str, interval: str = '15', limit: int = 200) -> Optional[pd.DataFrame]:
        """Получает данные с Bybit (интервал в минутах, например '15')."""
        # Bybit использует категорию linear, символ без USDT? Нужно проверить.
        # Для простоты оставим как есть, но преобразуем интервал.
        # В реальности лучше адаптировать.
        url = f"{BYBIT_API}/v5/market/kline"
        params = {
            'category': 'linear',
            'symbol': symbol,
            'interval': interval,
            'limit': limit
        }
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if 'result' not in data or 'list' not in data['result']:
            logger.warning(f"Bybit: no result for {symbol}")
            return None
        rows = data['result']['list']
        if not isinstance(rows, list) or len(rows) < 10:
            return None
        # Bybit возвращает [timestamp, open, high, low, close, volume, turnover]
        df = pd.DataFrame(rows, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
        df['timestamp'] = pd.to_datetime(df['timestamp'].astype(int), unit='ms')
        df.set_index('timestamp', inplace=True)
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = df[col].astype(float)
        return df[['open', 'high', 'low', 'close', 'volume']]
    
    def get_data(self, symbol: str, interval: str = '15m', limit: int = 200) -> Optional[pd.DataFrame]:
        """Получает данные, используя кэш и несколько источников."""
        cache_key = f"{symbol}_{interval}_{limit}"
        # Проверка кэша
        if cache_key in _cache and _cache_ttl.get(cache_key, 0) > time.time():
            logger.debug(f"Cache hit for {cache_key}")
            return _cache[cache_key]
        
        # Попытка получить с Binance
        df = self.fetch_binance_klines(symbol, interval, limit)
        if df is not None:
            _cache[cache_key] = df
            _cache_ttl[cache_key] = time.time() + self.cache_ttl
            return df
        
        # Если не получилось, пробуем Bybit
        # Преобразуем интервал для Bybit (минуты)
        bybit_interval = interval.replace('m', '')
        if bybit_interval.isdigit():
            df = self.fetch_bybit_klines(symbol, bybit_interval, limit)
        else:
            # Если интервал не в минутах, пропускаем
            df = None
        
        if df is not None:
            _cache[cache_key] = df
            _cache_ttl[cache_key] = time.time() + self.cache_ttl
            return df
        
        logger.error(f"Failed to fetch data for {symbol} from all sources")
        return None
    
    def get_multi_timeframe_data(self, symbol: str, intervals: List[str] = ['15m', '1h'], limit: int = 200) -> Dict[str, pd.DataFrame]:
        """Получает данные для нескольких таймфреймов."""
        result = {}
        for iv in intervals:
            df = self.get_data(symbol, iv, limit)
            if df is not None:
                result[iv] = df
        return result

# Глобальный экземпляр для удобства
_fetcher = DataFetcher(cache_ttl=60)

def get_data(symbol: str, interval: str = '15m', limit: int = 200) -> Optional[pd.DataFrame]:
    """Совместимая с предыдущей версией функция: возвращает DataFrame, а не список."""
    return _fetcher.get_data(symbol, interval, limit)

def get_raw_data(symbol: str, interval: str = '15m', limit: int = 200) -> Optional[List]:
    """Возвращает сырые данные в формате списка списков (для совместимости с chart.py)."""
    df = get_data(symbol, interval, limit)
    if df is None:
        return None
    # Преобразуем обратно в список списков (как раньше)
    # Добавим колонку timestamp как ms
    df_reset = df.reset_index()
    df_reset['timestamp_ms'] = df_reset['timestamp'].astype(int) // 10**6
    cols = ['timestamp_ms', 'open', 'high', 'low', 'close', 'volume']
    # Дополнительные колонки для совместимости (заполняем нулями)
    extra = ['close_time', 'quote_asset_volume', 'number_of_trades',
             'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore']
    result = df_reset[cols].values.tolist()
    # Добавляем пустые значения для недостающих колонок
    for row in result:
        row.extend([0]*len(extra))
    return result