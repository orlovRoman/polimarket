import logging
from typing import Dict, Any, Optional
import config
from core.eval.calibration_store import CalibrationStore

logger = logging.getLogger("NexusPolyBot.ConfigProvider")

class ConfigProvider:
    _cache: Dict[str, Any] = {}
    _store: Optional[CalibrationStore] = None

    @classmethod
    def _get_store(cls) -> CalibrationStore:
        if cls._store is None:
            cls._store = CalibrationStore()
        return cls._store

    @classmethod
    def invalidate_cache(cls) -> None:
        """Сбрасывает локальный кэш конфигураций."""
        cls._cache.clear()
        logger.info("Кэш конфигураций сброшен (invalidate_cache).")

    @classmethod
    def get_min_edge(cls, strategy_type: str = "scout") -> float:
        """
        Возвращает минимальный порог Edge для стратегии (например, scout).
        """
        return cls.get_min_edge_sync(strategy_type)

    @classmethod
    def get_min_edge_sync(cls, strategy_type: str = "scout") -> float:
        """
        Возвращает минимальный порог Edge для стратегии (например, scout) синхронно.
        """
        cache_key = f"min_edge_{strategy_type}"
        if cache_key in cls._cache:
            return cls._cache[cache_key]

        val = cls._get_store().get_latest_applied_value_sync("min_edge", strategy_type)
        if val is None:
            val = getattr(config, "MIN_EDGE_DEFAULT", 0.05)
            
        cls._cache[cache_key] = val
        return val

    @classmethod
    def get_min_spread(cls, strategy_type: str) -> float:
        """
        Возвращает минимальный порог спреда для стратегии.
        """
        return cls.get_min_spread_sync(strategy_type)

    @classmethod
    def get_min_spread_sync(cls, strategy_type: str) -> float:
        """
        Возвращает минимальный порог спреда для стратегии синхронно.
        Допустимые strategy_type: synthetic_corridor, temporal_corridor, cross_platform
        """
        cache_key = f"min_spread_{strategy_type}"
        if cache_key in cls._cache:
            return cls._cache[cache_key]

        val = cls._get_store().get_latest_applied_value_sync("min_spread", strategy_type)
        if val is None:
            val = 0.010  # Дефолт для всех стратегий
                
        cls._cache[cache_key] = val
        return val

    @classmethod
    def get_whale_win_rate_threshold(cls) -> float:
        """
        Возвращает порог win_rate для следования за китами.
        """
        return cls.get_whale_win_rate_threshold_sync()

    @classmethod
    def get_whale_win_rate_threshold_sync(cls) -> float:
        """
        Возвращает порог win_rate для следования за китами синхронно.
        """
        cache_key = "whale_win_rate_threshold"
        if cache_key in cls._cache:
            return cls._cache[cache_key]

        val = cls._get_store().get_latest_applied_value_sync("whale_win_rate_threshold", "whale")
        if val is None:
            val = getattr(config, "WHALE_GATE_MIN_CONFIDENCE", 0.70)
            
        cls._cache[cache_key] = val
        return val

    @classmethod
    def get_swing_min_volume_sync(cls, tag: str = "default") -> float:
        cache_key = f"swing_min_volume_{tag}"
        if cache_key in cls._cache:
            return cls._cache[cache_key]
        vol_by_tag = getattr(config, "SWING_VOLUME_BY_TAG", {})
        val = vol_by_tag.get(tag) or getattr(config, "SWING_MIN_VOLUME_USD", 5000.0)
        cls._cache[cache_key] = val
        return val

    @classmethod
    def get_swing_min_whale_count_sync(cls) -> int:
        if "swing_min_whale" in cls._cache:
            return cls._cache["swing_min_whale"]
        val = getattr(config, "SWING_MIN_WHALE_COUNT", 1)
        cls._cache["swing_min_whale"] = val
        return val

    @classmethod
    def get_sync(cls, key: str, default: Any = None) -> Any:
        """
        Возвращает значение конфигурации по ключу.
        Сначала считывает из переменных окружения (os.getenv), затем из модуля config.
        """
        import os
        # Преобразуем ключ вида "eval.virtual_stake_usd" -> "EVAL_VIRTUAL_STAKE_USD"
        env_key = key.replace(".", "_").upper()
        # Сначала ищем в env
        val = os.getenv(env_key)
        if val is not None:
            return val
        # Иначе ищем в config
        if hasattr(config, env_key):
            return getattr(config, env_key)
        return default

config_provider = ConfigProvider

