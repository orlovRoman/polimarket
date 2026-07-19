import sys
sys.path.append('/home/orlovrp/polymarket-bot')
from services.outcome_tracker import run_resolution_cycle

if __name__ == '__main__':
    run_resolution_cycle()
