from abc import ABC, abstractmethod
from typing import List
from core.models import Market

class BaseMarketAdapter(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @abstractmethod
    def list_markets(self) -> List[Market]:
        """Получить список активных рынков"""
        pass

    @abstractmethod
    def get_market(self, market_id: str) -> Market:
        """Получить данные конкретного рынка"""
        pass
