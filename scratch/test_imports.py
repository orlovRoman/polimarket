try:
    from agents.polymarket_insider_agent.src.agent import ShadowAgent
    print("SHADOW: OK")
except Exception as e:
    import traceback
    print("SHADOW: ERROR")
    traceback.print_exc()

try:
    from agents.polymarket_news_agent.src.agent import HeraldAgent
    print("HERALD: OK")
except Exception as e:
    import traceback
    print("HERALD: ERROR")
    traceback.print_exc()

try:
    from agents.polymarket_mispricing_agent.src.agent import ScoutAgent
    print("SCOUT: OK")
except Exception as e:
    import traceback
    print("SCOUT: ERROR")
    traceback.print_exc()

try:
    from agents.orchestrator.src.agent import NexusAgent
    print("NEXUS: OK")
except Exception as e:
    import traceback
    print("NEXUS: ERROR")
    traceback.print_exc()
