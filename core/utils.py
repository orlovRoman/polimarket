import inspect
import functools

@functools.lru_cache(maxsize=128)
def _callback_accepts_reply_markup(func) -> bool:
    """Кэширует результат проверки наличия reply_markup в сигнатуре колбэка."""
    try:
        sig = inspect.signature(func)
        return "reply_markup" in sig.parameters
    except (ValueError, TypeError):
        return False

def calc_compound_pnl(stake: float, entry_price: float, exit_price: float, fee_pct: float = 0.02) -> float:
    """Универсальный расчет PnL для сделок Favourite Compounding."""
    if entry_price <= 0:
        return 0.0
    contracts = stake / entry_price
    gross_pnl = contracts * (exit_price - entry_price)
    if gross_pnl > 0:
        gross_pnl *= (1.0 - fee_pct)
    return round(gross_pnl, 2)
