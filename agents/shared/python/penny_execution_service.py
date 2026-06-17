# agents/shared/python/penny_execution_service.py
"""
Сервисный слой исполнения для Penny Stocks.
Управляет фильтрацией рынков, расчетом размера ставки и проверкой лимитов перед входом.
"""
import logging
from datetime import datetime, timezone
from agents.shared.python.db import get_connection, buy_virtual_penny_stock
from agents.shared.python.penny_settings_db import get_penny_stocks_config, PennyStocksConfig
from agents.shared.python.penny_settings_service import run_penny_preflight

logger = logging.getLogger("NexusPolyBot.PennyExecutionService")

def should_skip_penny_scan(cfg: PennyStocksConfig) -> bool:
    """Возвращает True, если сканирование заблокировано (kill_switch)."""
    return cfg.kill_switch

def passes_penny_filters(market, cfg: PennyStocksConfig) -> bool:
    """
    Первичный фильтр рынка на этапе сканирования (до LLM-анализа).
    Проверяет, подходит ли цена, объем и время закрытия рынка.
    """
    # 1. Проверка цены исхода YES (или обратной цены NO) под дешевые рынки
    price = getattr(market, 'price', 0.5)
    effective_cheap = (price <= cfg.max_probability) or (price >= (1.0 - cfg.max_probability))
    if not effective_cheap:
        return False

    # 2. Проверка объема за 24 часа
    volume = getattr(market, 'volume_24h', 0.0) or getattr(market, 'volume', 0.0)
    if volume < cfg.min_volume_24h:
        return False

    # 3. Проверка времени закрытия
    close_time = getattr(market, 'close_time', None)
    if close_time:
        now = datetime.now(timezone.utc)
        diff_hours = (close_time - now).total_seconds() / 3600.0
        if not (cfg.min_hours_to_close <= diff_hours <= cfg.max_hours_to_close):
            return False
            
    return True

def passes_signal_filters(signal: dict, cfg: PennyStocksConfig) -> bool:
    """
    Фильтрует уже сформированные сигналы после LLM-оценки.
    Проверяет конкретное направление, вероятность исхода и уверенность.
    """
    outcome = signal.get("target_outcome")
    if not outcome:
        return False
        
    prob = signal.get("probability", 0.5)
    # Если исход NO, вероятность исхода NO = 1.0 - prob_YES
    if outcome == "NO":
        prob = 1.0 - prob

    if not (cfg.min_probability <= prob <= cfg.max_probability):
        return False
        
    confidence = signal.get("confidence", 0.5)
    if confidence < cfg.min_confidence_score:
        return False
        
    return True

def get_active_positions_count() -> int:
    """Возвращает число текущих активных виртуальных позиций Penny Stocks."""
    with get_connection() as conn:
        row = conn.execute("""
            SELECT COUNT(*) FROM penny_stocks_monitoring 
            WHERE virtual_bought_price IS NOT NULL
        """).fetchone()
        return row[0] if row else 0

def get_today_spent_budget() -> float:
    """Возвращает сумму ставок, сделанных сегодня по стратегии Penny Stocks."""
    today_date = datetime.now().strftime("%Y-%m-%d")
    with get_connection() as conn:
        # 1. Суммируем закрытые сделки за сегодня
        row_hist = conn.execute("""
            SELECT SUM(COALESCE(bet_size_usdc, 1.0)) FROM penny_virtual_trades_history 
            WHERE bought_at LIKE ?
        """, (f"{today_date}%",)).fetchone()
        spent_hist = row_hist[0] if row_hist and row_hist[0] is not None else 0.0
        
        # 2. Суммируем открытые сегодня позиции
        row_active = conn.execute("""
            SELECT SUM(COALESCE(bet_size_usdc, 1.0)) FROM penny_stocks_monitoring
            WHERE virtual_bought_at LIKE ?
        """, (f"{today_date}%",)).fetchone()
        spent_active = row_active[0] if row_active and row_active[0] is not None else 0.0
        
        return float(spent_hist + spent_active)

def compute_penny_bet_size(signal: dict, cfg: PennyStocksConfig) -> float:
    """
    Вычисляет размер ставки с масштабированием по confidence и учетом лимитов бюджета.
    """
    confidence = signal.get("confidence", 0.5)
    min_conf = cfg.min_confidence_score
    
    if confidence < min_conf:
        return 0.0
        
    base = cfg.bet_size_usdc
    if min_conf < 1.0:
        # Линейное масштабирование от base до base * 2
        scaled = base * (1.0 + (confidence - min_conf) / (1.0 - min_conf))
    else:
        scaled = base
        
    # Ограничиваем сверху максимальной ставкой
    bet = min(scaled, cfg.max_bet_size_usdc)
    
    # Ограничиваем остатком суточного бюджета
    today_spent = get_today_spent_budget()
    remaining_budget = max(0.0, cfg.daily_budget_usdc - today_spent)
    
    return min(bet, remaining_budget)

def can_execute_penny_trade(signal: dict, cfg: PennyStocksConfig) -> bool:
    """
    Проверяет все лимиты и ограничения перед совершением автопокупки.
    """
    # 1. Проверка kill switch и автопокупки
    if cfg.kill_switch:
        logger.info("Сделка отклонена: активен Kill Switch.")
        return False
    if not cfg.auto_buy_enabled:
        logger.info("Сделка отклонена: автопокупка выключена.")
        return False
        
    # 2. Проверка соответствия лимитов
    active_count = get_active_positions_count()
    if active_count >= cfg.max_open_positions:
        logger.info(f"Сделка отклонена: лимит открытых позиций превышен ({active_count} >= {cfg.max_open_positions}).")
        return False
        
    bet_size = compute_penny_bet_size(signal, cfg)
    if bet_size <= 0.0:
        logger.info("Сделка отклонена: размер ставки равен 0.0.")
        return False

    # 3. Проверка preflight check
    if cfg.require_preflight_for_autobuy:
        preflight = run_penny_preflight()
        if not preflight["ok"]:
            logger.warning(f"Сделка отклонена: preflight check провален. Ошибки: {preflight['errors']}")
            return False
            
    return True

def execute_penny_trade(market_id: str, signal: dict, cfg: PennyStocksConfig) -> bool:
    """
    Исполняет сделку: открывает виртуальную позицию.
    В будущем здесь будет вызов LivePolymarketProvider.
    """
    if not can_execute_penny_trade(signal, cfg):
        return False
        
    # Вычисляем размер ставки
    bet_size = compute_penny_bet_size(signal, cfg)
    if bet_size <= 0:
        logger.warning(f"Размер ставки 0 USDC для {market_id}, отмена сделки.")
        return False

    # Определяем цену исхода
    price = signal.get("price", 0.5)
    
    try:
        # Совершаем виртуальную покупку
        buy_virtual_penny_stock(market_id, price, bet_size)
        logger.info(f"Успешно открыта виртуальная позиция для рынка {market_id} по цене {price} со ставкой {bet_size} USDC")
        return True
    except Exception as e:
        logger.error(f"Ошибка при исполнении сделки для рынка {market_id}: {e}")
        return False
