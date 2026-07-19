import os

client_file = None
for root, dirs, files in os.walk('/home/orlovrp/polymarket-bot'):
    if 'venv' in root or '__pycache__' in root or '.git' in root:
        continue
    for f in files:
        if f.endswith('.py'):
            filepath = os.path.join(root, f)
            with open(filepath, 'r', encoding='utf-8') as file:
                try:
                    content = file.read()
                    if 'class PolymarketClient' in content:
                        print(f"FOUND in: {filepath}")
                        client_file = filepath
                except Exception:
                    pass
