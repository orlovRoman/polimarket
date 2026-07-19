import sys
sys.path.insert(0, '/home/orlovrp/polymarket-bot')
from agents.shared.python.whale_portfolio_service import get_whale_radar_summary
import json

try:
    print("RUNNING RADAR SUMMARY...")
    data = get_whale_radar_summary(1)
    print("SUMMARY LENGTH:", len(data))
    for item in data[:20]:
        print(item)
except Exception as e:
    print("ERROR:", e)
