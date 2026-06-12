from agents.shared.utils.parsers import parse_numeric_level, _mask_years

def test_parse_numeric_level():
    assert parse_numeric_level("Will ETH hit $4K today?") == (4.0, "K")
    assert parse_numeric_level("Will BTC hit $60,000 in February?") == (60000.0, "points")
    assert parse_numeric_level("IPL: 2023-05-19") is None
    assert parse_numeric_level("Will Russia lose UNSC status by April 30, 2022?") is None
    assert parse_numeric_level("Champions League (02/22/2023)") is None
    assert parse_numeric_level("Will S&P 500 hit 6000?") == (6000.0, "points")
    assert parse_numeric_level("Will unemployment reach 5%?") == (5.0, "%")
    assert parse_numeric_level("Will Anthropic hit $1.5T valuation?") == (1.5, "T")
    assert parse_numeric_level("NFL 2023 season") is None
    assert parse_numeric_level("Super Bowl LVII (2023)") is None
    assert parse_numeric_level("Will BTC hit $60,000 in February 2025?") == (60000.0, "points")
    assert parse_numeric_level("2026 US Midterms winner?") is None
    assert parse_numeric_level("Will S&P 500 reach all-time high?") is None

    # Дополнительные сложные тест-кейсы
    assert parse_numeric_level("Will S&P 500 hit 6000 in December 2024?") == (6000.0, "points")
    assert parse_numeric_level("Will Anthropic hit $1.5T valuation by 2026?") == (1.5, "T")
    assert parse_numeric_level("Will unemployment reach 5% in 2023?") == (5.0, "%")
    assert parse_numeric_level("2024 Election: Will Trump win?") is None

def test_mask_years():
    assert "YEAR_MASKED" in _mask_years("by April 2022")
    assert "YEAR_MASKED" in _mask_years("2023 season")
    assert "YEAR_MASKED" in _mask_years("Super Bowl (2025)")
    assert "YEAR_MASKED" in _mask_years("2023-05-19")
    assert "YEAR_MASKED" in _mask_years("2026 US Midterms")

