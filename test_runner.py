import asyncio
import logging

logging.basicConfig(level=logging.INFO)

async def test():
    import main
    print("Testing start_system...")
    try:
        await main.start_system()
    except Exception as e:
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test())
