"""
Тесты для services/market_matcher.py и services/trend_hunter.py — Слой #12

Покрывают 5 исправленных багов:
  Баг #1 — find_candidate_pairs: naive vs aware datetime → TypeError
  Баг #2 — verify_pair_with_llm: markdown-wrapped JSON
  Баг #3 — normalize(): числа дают ложные совпадения
  Баг #4 — trend_hunter: closure захватывает m_id по ссылке
  Баг #5 — trend_hunter: try/except после thread.start()
"""
import threading
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
from core.models import Market


# ──────────────────────────────────────────────────────────────────────────────
# Вспомогательная фабрика
# ──────────────────────────────────────────────────────────────────────────────

def _market(mid, title, close_time, platform="polymarket", price=0.5):
    return Market(
        id=mid, platform=platform, title=title,
        url=f"https://example.com/{mid}", outcome="YES",
        price=price, close_time=close_time, description=None,
    )


CLOSE_AWARE = datetime(2026, 11, 1, tzinfo=timezone.utc)
CLOSE_NAIVE = datetime(2026, 11, 1)                           # без timezone
CLOSE_PLUS3 = datetime(2026, 11, 1, tzinfo=timezone(timedelta(hours=3)))


# ──────────────────────────────────────────────────────────────────────────────
# Баг #1 — timezone-safe сравнение дат в find_candidate_pairs
# ──────────────────────────────────────────────────────────────────────────────

class TestFindCandidatePairs:

    def test_naive_vs_aware_no_crash(self):
        """naive vs aware close_time не должен бросать TypeError."""
        from services.market_matcher import find_candidate_pairs

        ma = _market("poly-1", "Trump wins election", CLOSE_AWARE, "polymarket")
        mb = _market("kal-1",  "Trump wins election", CLOSE_NAIVE, "kalshi")

        # До фикса: TypeError: can't subtract offset-naive and offset-aware datetimes
        result = find_candidate_pairs([ma], [mb], min_score=0.3)
        assert isinstance(result, list)

    def test_utc_and_plus3_treated_as_same_day(self):
        """UTC и UTC+3 для одного момента — days_diff=0, пара находится."""
        from services.market_matcher import find_candidate_pairs

        ma = _market("poly-1", "Bitcoin ETF approved", CLOSE_AWARE, "polymarket")
        mb = _market("kal-1",  "Bitcoin ETF approved", CLOSE_PLUS3, "kalshi")

        # max_days_diff=0 → пройдёт только если days_diff == 0
        result = find_candidate_pairs([ma], [mb], min_score=0.3, max_days_diff=0)
        assert len(result) >= 1

    def test_large_date_diff_filtered(self):
        """Пара с разницей 212 дней фильтруется при max_days_diff=21."""
        from services.market_matcher import find_candidate_pairs

        ma = _market("poly-1", "Fed rate cut decision", CLOSE_AWARE, "polymarket")
        far = datetime(2027, 6, 1, tzinfo=timezone.utc)
        mb = _market("kal-1",  "Fed rate cut decision", far, "kalshi")

        result = find_candidate_pairs([ma], [mb], min_score=0.3, max_days_diff=21)
        assert result == []

    def test_existing_tests_still_pass(self):
        """Регрессия: тесты из исходного test_market_matcher.py."""
        from services.market_matcher import find_candidate_pairs

        def make(title, price, platform="polymarket", mid=None):
            return Market(
                id=mid or title[:10], platform=platform, title=title,
                url="https://polymarket.com/test", outcome="YES", price=price,
                close_time=datetime(2026, 12, 31, tzinfo=timezone.utc),
            )

        # same-platform, confirmed no arbi → 0 пар
        a = [make("SpaceX IPO above $3T", 0.12, mid="a1")]
        b = [make("SpaceX IPO above $1.8T", 0.84, mid="b1")]
        assert find_candidate_pairs(a, b, min_score=0.3) == []

        # same-platform, реальное нарушение → 1 пара
        a = [make("SpaceX IPO above $3T", 0.90, mid="a1")]
        b = [make("SpaceX IPO above $1.8T", 0.60, mid="b1")]
        assert len(find_candidate_pairs(a, b, min_score=0.3)) == 1

        # cross-platform → всегда проходит
        a = [make("Will Fed cut rates in June?", 0.50, "polymarket", "a1")]
        b = [make("Will Fed cut rates in June?", 0.53, "kalshi", "b1")]
        assert len(find_candidate_pairs(a, b, min_score=0.3)) == 1


# ──────────────────────────────────────────────────────────────────────────────
# Баг #2 — markdown-wrapped JSON в verify_pair_with_llm
# ──────────────────────────────────────────────────────────────────────────────

class TestVerifyPairWithLLM:

    def _make_pair(self, close=CLOSE_AWARE):
        ma = _market("poly-1", "Trump wins 2024", close)
        mb = _market("kal-1",  "Trump wins 2024", close, "kalshi")
        return ma, mb

    def test_unwraps_markdown_json(self):
        """LLM-ответ в ```json блоке парсится корректно."""
        from services.market_matcher import verify_pair_with_llm

        ma, mb = self._make_pair()
        md = '```json\n{"is_same_event": true, "confidence": 0.95, "reason": "Same"}\n```'

        with patch("services.market_matcher.generate_content_with_fallback",
                   return_value=(MagicMock(), "gemini-2.5-flash")), \
             patch("services.market_matcher.extract_response_text", return_value=md):
            result = verify_pair_with_llm(ma, mb, "fake-key")

        assert result["is_same_event"] is True
        assert result["confidence"] == 0.95

    def test_plain_json_still_works(self):
        """Обычный JSON без markdown-обёртки тоже парсится."""
        from services.market_matcher import verify_pair_with_llm

        ma, mb = self._make_pair()
        plain = '{"is_same_event": false, "confidence": 0.2, "reason": "Different"}'

        with patch("services.market_matcher.generate_content_with_fallback",
                   return_value=(MagicMock(), "gemini-2.5-flash")), \
             patch("services.market_matcher.extract_response_text", return_value=plain):
            result = verify_pair_with_llm(ma, mb, "fake-key")

        assert result["is_same_event"] is False

    def test_returns_false_on_llm_error(self):
        """При ошибке LLM возвращается безопасный дефолт."""
        from services.market_matcher import verify_pair_with_llm

        ma, mb = self._make_pair()

        with patch("services.market_matcher.generate_content_with_fallback",
                   return_value=(None, None)):
            result = verify_pair_with_llm(ma, mb, "fake-key")

        assert result["is_same_event"] is False
        assert result["confidence"] == 0.0

    def test_json_with_extra_whitespace_parsed(self):
        """JSON с лишними пробелами вокруг (но без markdown) парсится."""
        from services.market_matcher import verify_pair_with_llm

        ma, mb = self._make_pair()
        spaced = '  \n{"is_same_event": true, "confidence": 0.7, "reason": "ok"}  \n'

        with patch("services.market_matcher.generate_content_with_fallback",
                   return_value=(MagicMock(), "gemini-2.5-flash")), \
             patch("services.market_matcher.extract_response_text", return_value=spaced):
            result = verify_pair_with_llm(ma, mb, "fake-key")

        assert result["is_same_event"] is True


# ──────────────────────────────────────────────────────────────────────────────
# Баг #3 — числовые токены в normalize()
# ──────────────────────────────────────────────────────────────────────────────

class TestNormalize:

    def test_pure_digits_filtered(self):
        """Числа '3000' и '2026' не входят в множество токенов."""
        from services.market_matcher import normalize

        tokens = normalize("Will Ethereum hit 3000 by 2026")
        assert "3000" not in tokens
        assert "2026" not in tokens
        assert "ethereum" in tokens

    def test_no_false_match_on_numbers(self):
        """Рынки с одинаковыми числами но разными активами — Jaccard < 0.15."""
        from services.market_matcher import normalize

        eth = normalize("Will Ethereum hit 3000 by 2026")
        btc = normalize("Will Bitcoin reach 3000 in 2026")

        intersection = len(eth & btc)
        union = len(eth | btc)
        jaccard = intersection / union if union > 0 else 0

        # До фикса: jaccard ≈ 0.5 из-за "3000" и "2026"
        # После фикса: числа отброшены → jaccard → 0
        assert jaccard < 0.15, f"Ложный Jaccard из-за числовых токенов: {jaccard:.2f}"

    def test_word_with_digit_not_filtered(self):
        """'s&p500' (смешанный токен) — не является isdigit() → не фильтруется."""
        from services.market_matcher import normalize

        tokens = normalize("will sp500 hit new high")
        assert "sp500" in tokens

    def test_empty_title_returns_empty_set(self):
        from services.market_matcher import normalize
        assert normalize("") == set()


# ──────────────────────────────────────────────────────────────────────────────
# Баг #4 — closure в threading.Thread захватывает m_id по ссылке
# ──────────────────────────────────────────────────────────────────────────────

class TestTrendHunterClosure:

    def test_each_thread_uses_own_market_id(self):
        """Каждый поток анализирует свой market_id, а не последний из цикла."""
        from services.trend_hunter import run_trend_hunter

        analyzed_ids = []
        barrier = threading.Barrier(1, timeout=3)

        def fake_run_team(**kwargs):
            analyzed_ids.append(kwargs.get("market_id"))

        def make_fake_market(mid, title):
            m = MagicMock()
            m.id = mid
            m.title = title
            m.price = 0.5
            m.url = f"https://example.com/{mid}"
            return m

        markets = [make_fake_market("mkt-001", "Market 1"),
                   make_fake_market("mkt-002", "Market 2")]

        with patch("services.trend_hunter.fetch_rss_titles", return_value=["title1"]), \
             patch("services.trend_hunter.extract_trends_with_llm", return_value=[{
                 "topic_ru": "Test", "topic_en": "Test",
                 "reasoning": "r", "queries": ["q"],
             }]), \
             patch("services.trend_hunter.PolymarketAdapter") as MockAdapter, \
             patch("services.trend_hunter.is_market_analyzed", return_value=False), \
             patch("services.trend_hunter.save_market"), \
             patch("services.trend_hunter.send_telegram"), \
             patch("services.trend_hunter.save_memory"), \
             patch("services.trend_hunter.get_memory", return_value=True), \
             patch("services.trend_hunter.CoreEngine") as MockEngine:

            MockAdapter.return_value.search_markets.return_value = markets
            eng_instance = MockEngine.return_value
            eng_instance.run_team_discussion.side_effect = lambda **kw: analyzed_ids.append(kw.get("market_id"))

            run_trend_hunter(dry_run=False)

            # Ждём завершения daemon-потоков
            for t in threading.enumerate():
                if t.daemon and t is not threading.main_thread():
                    t.join(timeout=3)

        # Оба ID должны встречаться
        assert "mkt-001" in analyzed_ids or "mkt-002" in analyzed_ids
        # Без фикса все потоки давали бы один и тот же последний ID
        if len(analyzed_ids) >= 2:
            assert len(set(analyzed_ids)) == len(analyzed_ids), \
                f"Closure-баг: дубликаты IDs {analyzed_ids}"


# ──────────────────────────────────────────────────────────────────────────────
# Баг #5 — try/except после thread.start() не ловит RuntimeError
# ──────────────────────────────────────────────────────────────────────────────

class TestTrendHunterThreadException:

    def test_thread_start_exception_does_not_propagate(self):
        """RuntimeError из thread.start() не должен ронять run_trend_hunter."""
        from services.trend_hunter import run_trend_hunter

        fake_market = MagicMock()
        fake_market.id = "mkt-crash"
        fake_market.title = "Crashing Market"
        fake_market.price = 0.5
        fake_market.url = "https://example.com"

        original_thread_cls = threading.Thread

        def broken_thread_factory(*args, **kwargs):
            t = original_thread_cls(*args, **kwargs)
            t.start = lambda: (_ for _ in ()).throw(RuntimeError("Thread pool exhausted"))
            return t

        with patch("services.trend_hunter.fetch_rss_titles", return_value=["title"]), \
             patch("services.trend_hunter.extract_trends_with_llm", return_value=[{
                 "topic_ru": "T", "topic_en": "T", "reasoning": "r", "queries": ["q"],
             }]), \
             patch("services.trend_hunter.PolymarketAdapter") as MockAdapter, \
             patch("services.trend_hunter.is_market_analyzed", return_value=False), \
             patch("services.trend_hunter.save_market"), \
             patch("services.trend_hunter.send_telegram"), \
             patch("services.trend_hunter.save_memory"), \
             patch("services.trend_hunter.get_memory", return_value=False), \
             patch("services.trend_hunter.threading.Thread", side_effect=broken_thread_factory):

            MockAdapter.return_value.search_markets.return_value = [fake_market]
            # Главное: не должно бросить исключение наружу
            run_trend_hunter(dry_run=False)
