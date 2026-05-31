"""
Тесты для NewsProcessor — проверка двухэтапного процесса:
  Этап 1: LLM → ключевые слова → Polymarket search API → кандидаты
  Этап 2: LLM → валидация релевантности кандидатов к новости
"""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta

# Добавляем корень проекта в sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.orchestrator.src.news_processor import NewsProcessor
from core.models import Market


def _make_market(market_id: str, title: str, price: float = 0.50) -> Market:
    """Хелпер: создаёт объект Market для тестов."""
    return Market(
        id=market_id,
        title=title,
        url=f"https://polymarket.com/event/{market_id}",
        platform="polymarket",
        outcome="Will it happen?",
        price=price,
        volume=10000,
        close_time=datetime.now(timezone.utc) + timedelta(days=30),
        tokens=["token_yes", "token_no"],
    )


class TestValidateRelevance:
    """Тесты для _validate_relevance() — LLM-валидация релевантности."""

    @patch("agents.orchestrator.src.news_processor.generate_content_with_fallback")
    @patch("agents.orchestrator.src.news_processor.extract_response_text")
    def test_filters_unrelated_markets(self, mock_extract, mock_generate):
        """_validate_relevance должна отсеять нерелевантные рынки."""
        # LLM отвечает: только рынок 1 релевантен
        mock_generate.return_value = ({"candidates": [{"content": {}}]}, "model")
        mock_extract.return_value = '{"relevant_indices": [1]}'

        np = NewsProcessor(api_key="test-key")

        uranium_market = _make_market("m1", "US Uranium sanctions impact")
        gta_market = _make_market("m2", "Will Jesus Christ return before GTA VI?")
        markets = [uranium_market, gta_market]

        result = np._validate_relevance("США заключили урановую сделку", markets)

        assert len(result) == 1
        assert result[0].id == "m1"
        assert result[0].title == "US Uranium sanctions impact"

    @patch("agents.orchestrator.src.news_processor.generate_content_with_fallback")
    @patch("agents.orchestrator.src.news_processor.extract_response_text")
    def test_returns_empty_when_none_relevant(self, mock_extract, mock_generate):
        """Если ни один рынок не релевантен — пустой список."""
        mock_generate.return_value = ({"candidates": [{"content": {}}]}, "model")
        mock_extract.return_value = '{"relevant_indices": []}'

        np = NewsProcessor(api_key="test-key")
        markets = [
            _make_market("m1", "Will Jesus Christ return before GTA VI?"),
            _make_market("m2", "Next US president party?"),
        ]

        result = np._validate_relevance("Уран: новая сделка между США и Казахстаном", markets)

        assert result == []

    @patch("agents.orchestrator.src.news_processor.generate_content_with_fallback")
    @patch("agents.orchestrator.src.news_processor.extract_response_text")
    def test_keeps_all_relevant(self, mock_extract, mock_generate):
        """Если все рынки релевантны — вернуть все."""
        mock_generate.return_value = ({"candidates": [{"content": {}}]}, "model")
        mock_extract.return_value = '{"relevant_indices": [1, 2]}'

        np = NewsProcessor(api_key="test-key")
        markets = [
            _make_market("m1", "TSMC stock below $100?"),
            _make_market("m2", "US sanctions on TSMC by 2026?"),
        ]

        result = np._validate_relevance("США вводят санкции против TSMC", markets)

        assert len(result) == 2

    @patch("agents.orchestrator.src.news_processor.generate_content_with_fallback")
    def test_llm_failure_returns_empty(self, mock_generate):
        """При ошибке LLM — safe fallback, пустой список (не мусор)."""
        mock_generate.return_value = (None, "model")

        np = NewsProcessor(api_key="test-key")
        markets = [_make_market("m1", "Some market")]

        result = np._validate_relevance("Любой текст", markets)

        assert result == []

    @patch("agents.orchestrator.src.news_processor.generate_content_with_fallback")
    @patch("agents.orchestrator.src.news_processor.extract_response_text")
    def test_invalid_indices_ignored(self, mock_extract, mock_generate):
        """Невалидные индексы (0, -1, 999) должны быть проигнорированы."""
        mock_generate.return_value = ({"candidates": [{"content": {}}]}, "model")
        mock_extract.return_value = '{"relevant_indices": [0, -1, 999, 1]}'

        np = NewsProcessor(api_key="test-key")
        markets = [
            _make_market("m1", "Valid market"),
            _make_market("m2", "Another market"),
        ]

        result = np._validate_relevance("Текст", markets)

        assert len(result) == 1
        assert result[0].id == "m1"

    def test_empty_markets_returns_empty(self):
        """Пустой входной список → пустой результат без вызова LLM."""
        np = NewsProcessor(api_key="test-key")
        result = np._validate_relevance("Текст", [])
        assert result == []


class TestFindRelevantMarkets:
    """Тесты для find_relevant_markets() — полный двухэтапный пайплайн."""

    @patch("agents.orchestrator.src.news_processor.generate_content_with_fallback")
    @patch("agents.orchestrator.src.news_processor.extract_response_text")
    def test_end_to_end_filters_irrelevant(self, mock_extract, mock_generate):
        """
        Полный E2E тест: LLM извлекает ключевые слова, search возвращает
        смешанные рынки, валидация отсеивает нерелевантные.
        """
        uranium_market = _make_market("m1", "US Uranium Deal outcome")
        gta_market = _make_market("m2", "Will Jesus Christ return before GTA VI?")

        # Первый вызов LLM — извлечение ключевых слов
        # Второй вызов LLM — валидация релевантности
        mock_generate.side_effect = [
            ({"candidates": [{"content": {}}]}, "model"),  # keywords
            ({"candidates": [{"content": {}}]}, "model"),  # validate
        ]
        mock_extract.side_effect = [
            '{"keywords": ["uranium", "US deal"]}',        # keywords
            '{"relevant_indices": [1]}',                    # validate: только m1
        ]

        np = NewsProcessor(api_key="test-key")
        # Мокаем search_markets чтобы он возвращал наши рынки
        np.adapter.search_markets = MagicMock(side_effect=[
            [uranium_market],    # по "uranium"
            [gta_market],        # по "US deal"
        ])

        result = np.find_relevant_markets("США и Казахстан заключили урановую сделку")

        assert len(result) == 1
        assert result[0].id == "m1"
        # search_markets должен быть вызван по каждому ключевому слову
        assert np.adapter.search_markets.call_count == 2

    @patch("agents.orchestrator.src.news_processor.generate_content_with_fallback")
    @patch("agents.orchestrator.src.news_processor.extract_response_text")
    def test_returns_empty_on_no_keywords(self, mock_extract, mock_generate):
        """Если LLM не находит ключевых слов — пустой список, без поиска."""
        mock_generate.return_value = ({"candidates": [{"content": {}}]}, "model")
        mock_extract.return_value = '{"keywords": []}'

        np = NewsProcessor(api_key="test-key")
        np.adapter.search_markets = MagicMock()

        result = np.find_relevant_markets("Просто спам без смысла")

        assert result == []
        np.adapter.search_markets.assert_not_called()

    @patch("agents.orchestrator.src.news_processor.generate_content_with_fallback")
    @patch("agents.orchestrator.src.news_processor.extract_response_text")
    def test_returns_empty_when_validation_rejects_all(self, mock_extract, mock_generate):
        """
        Если search находит рынки, но валидация отклоняет все — пустой список.
        Это ключевой сценарий: лучше НЕ анализировать, чем анализировать мусор.
        """
        gta_market = _make_market("m1", "Will Jesus Christ return before GTA VI?")

        mock_generate.side_effect = [
            ({"candidates": [{"content": {}}]}, "model"),  # keywords
            ({"candidates": [{"content": {}}]}, "model"),  # validate
        ]
        mock_extract.side_effect = [
            '{"keywords": ["uranium"]}',
            '{"relevant_indices": []}',     # валидация отклоняет все
        ]

        np = NewsProcessor(api_key="test-key")
        np.adapter.search_markets = MagicMock(return_value=[gta_market])

        result = np.find_relevant_markets("США и Казахстан заключили урановую сделку")

        assert result == []

    @patch("agents.orchestrator.src.news_processor.generate_content_with_fallback")
    def test_llm_extraction_failure_returns_empty(self, mock_generate):
        """При ошибке LLM на первом этапе — пустой список."""
        mock_generate.return_value = (None, "model")
        np = NewsProcessor(api_key="test-key")
        result = np.find_relevant_markets("Любой текст")

        assert result == []


class TestExtractMarketsFromUrls:
    """Тесты для Этапа 0 — извлечение прямых ссылок на Polymarket."""

    def test_extracts_nothing_when_no_urls(self):
        np = NewsProcessor(api_key="test-key")
        result = np._extract_markets_from_urls("Просто новость без ссылок")
        assert result == []

    @patch("agents.shared.adapters.polymarket.PolymarketAdapter.get_event_by_slug")
    def test_extracts_markets_from_valid_url(self, mock_get_event):
        np = NewsProcessor(api_key="test-key")
        market = _make_market("m1", "Will Gabriel Attal win the 2027 French presidential election?")
        mock_get_event.return_value = [market]

        text = "Top Holder Activity\n\nWill Gabriel Attal win the 2027 French presidential election? (https://polymarket.com/event/next-french-presidential-election) · Vol: $1.4M"
        result = np._extract_markets_from_urls(text)

        mock_get_event.assert_called_once_with("next-french-presidential-election")
        assert len(result) == 1
        assert result[0].id == "m1"

    @patch("agents.shared.adapters.polymarket.PolymarketAdapter.get_event_by_slug")
    @patch("agents.orchestrator.src.news_processor.generate_content_with_fallback")
    def test_find_relevant_markets_skips_llm_when_urls_found(self, mock_generate, mock_get_event):
        np = NewsProcessor(api_key="test-key")
        market = _make_market("m1", "French election")
        mock_get_event.return_value = [market]

        text = "https://polymarket.com/event/next-french-presidential-election"
        result = np.find_relevant_markets(text)

        mock_get_event.assert_called_once_with("next-french-presidential-election")
        mock_generate.assert_not_called()
        assert len(result) == 1
        assert result[0].id == "m1"


class TestNewsProcessorDiagnosticsAndWhales:
    """Тесты для новой диагностики закрытых рынков и автоматического извлечения китов."""

    @patch("agents.shared.python.db.upsert_known_whale")
    def test_extract_whale_from_post_success(self, mock_upsert):
        """Проверяет успешное извлечение кита из структурированного текста."""
        np = NewsProcessor(api_key="test-key")
        text = (
            "Insider Entry [VARsenal](https://polymarket.com/profile/0x5c3a1a602848565bb16165fcd460b00c3d43020b)\n"
            "Win Rate: 87%\n"
            "P&L: +$1,644,844\n"
            "Insider Probability: 45%\n"
            "Buy No · $10,600 · Entry: 53¢"
        )
        np._extract_whale_from_post(text)
        mock_upsert.assert_called_once_with(
            "0x5c3a1a602848565bb16165fcd460b00c3d43020b", "VARsenal", 0.87
        )

    @patch("agents.shared.python.db.upsert_known_whale")
    def test_extract_whale_from_post_no_alias(self, mock_upsert):
        """Проверяет извлечение кита, если alias отсутствует."""
        np = NewsProcessor(api_key="test-key")
        text = (
            "https://polymarket.com/profile/0x5c3a1a602848565bb16165fcd460b00c3d43020b\n"
            "Win Rate: 72%"
        )
        np._extract_whale_from_post(text)
        mock_upsert.assert_called_once_with(
            "0x5c3a1a602848565bb16165fcd460b00c3d43020b", "0x5c3a1a", 0.72
        )

    @patch("agents.shared.adapters.polymarket.PolymarketAdapter.get_event_by_slug")
    def test_closed_market_diagnostics_direct_links(self, mock_get_event):
        """Проверяет выставление MARKET_CLOSED для прямых ссылок."""
        np = NewsProcessor(api_key="test-key")
        closed_market = _make_market("m1", "Closed election")
        closed_market.close_time = datetime.now(timezone.utc) - timedelta(days=1)
        mock_get_event.return_value = [closed_market]

        text = "https://polymarket.com/event/closed-election"
        result = np.find_relevant_markets(text)

        assert result == []
        assert np.failure_reason == "MARKET_CLOSED"
        assert len(np.closed_markets) == 1
        assert np.closed_markets[0].id == "m1"
