import os

def search_client():
    search_dir = '/home/orlovrp/polymarket-bot'
    for root, dirs, files in os.walk(search_dir):
        if 'venv' in root or '__pycache__' in root or '.git' in root:
            continue
        for f in files:
            if f.endswith('.py'):
                path = os.path.join(root, f)
                try:
                    with open(path, 'r', encoding='utf-8') as file:
                        if 'Ошибка при получении резолюции для' in file.read():
                            print(f"FOUND: {path}")
                            return path
                except Exception:
                    pass
    return None

if __name__ == '__main__':
    search_client()
