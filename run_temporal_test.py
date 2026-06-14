import logging
import asyncio
from services.temporal_corridor_scanner import run_temporal_corridor_scan

logging.basicConfig(level=logging.INFO)
# Also enable debug for detector
logging.getLogger("NexusPolyBot.TemporalCorridor").setLevel(logging.DEBUG)
logging.getLogger("NexusPolyBot.TemporalCorridor.loader").setLevel(logging.DEBUG)

def main():
    run_temporal_corridor_scan()

if __name__ == "__main__":
    main()
