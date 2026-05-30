import pytest
from services.telegram_listener import parse_whale_alert, parse_radar_signal

def test_parse_whale_alert_outcome_no_false_positives():
    """Проверяет корректность парсинга YES/NO исхода в parse_whale_alert и отсутствие ложных срабатываний"""
    
    # 1. Обычное слово 'no' в предложении не должно определяться как исход NO
    text_with_no_word = (
        "🐋 High Risk Whale Buy | F...\n"
        "Trump leads in polls. There is no doubt that he is ahead.\n"
        "Link: https://polymarket.com/event/trump-returns"
    )
    result1 = parse_whale_alert(text_with_no_word)
    assert result1["outcome"] is None  # Слово 'no' не должно распознаваться как исход
    
    # 2. Настоящий исход YES
    text_yes = (
        "🐋 Trader bought YES for $50,000 at 45¢\n"
        "Market: https://polymarket.com/event/trump-returns"
    )
    result2 = parse_whale_alert(text_yes)
    assert result2["outcome"] == "YES"
    assert result2["amount_usd"] == 50000.0
    assert result2["price"] == 0.45
    
    # 3. Настоящий исход NO
    text_no = (
        "🐋 Trader bought NO for $15,000 @ 35¢\n"
        "Market: https://polymarket.com/event/trump-returns"
    )
    result3 = parse_whale_alert(text_no)
    assert result3["outcome"] == "NO"
    assert result3["amount_usd"] == 15000.0
    assert result3["price"] == 0.35

def test_parse_radar_signal_outcome_no_false_positives():
    """Проверяет корректность парсинга YES/NO исхода в parse_radar_signal"""
    
    # 1. Сигнал на покупку YES
    text_radar_yes = (
        "Will Trump win?\n"
        "⚡️ Buy Yes\n"
        "├ Amount: $11,136\n"
        "├ Entry: 15¢ → Now: 90¢\n"
        "└ To win: $74,240\n"
    )
    result_yes = parse_radar_signal(text_radar_yes)
    assert result_yes["outcome"] == "YES"
    assert result_yes["amount_usd"] == 11136.0
    assert result_yes["entry_price"] == 0.15
    assert result_yes["current_price"] == 0.90
    
    # 2. Сигнал на покупку NO
    text_radar_no = (
        "Will Trump win?\n"
        "⚡️ Buy No\n"
        "├ Amount: $5,000\n"
        "├ Entry: 20¢ → Now: 80¢\n"
        "└ To win: $25,000\n"
    )
    result_no = parse_radar_signal(text_radar_no)
    assert result_no["outcome"] == "NO"
    assert result_no["amount_usd"] == 5000.0
    assert result_no["entry_price"] == 0.20
    assert result_no["current_price"] == 0.80
