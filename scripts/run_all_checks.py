import subprocess
import sys
import os

def run_cmd(cmd: str):
    print(f"=== RUNNING: {cmd} ===")
    result = subprocess.run(cmd, shell=True)
    if result.returncode != 0:
        print(f"FAIL: {cmd}")
        sys.exit(1)
    print("SUCCESS\n")

def main():
    os.chdir(os.path.join(os.path.dirname(__file__), '..'))
    
    print("Starting Polymarket Bot Checks...\n")
    
    # 1. Проверка синтаксиса критичных файлов
    critical_files = [
        "main.py",
        "core/engine.py",
        "core/workflow.py",
        "agents/shared/python/db.py",
        "scripts/resolve_markets.py"
    ]
    for f in critical_files:
        run_cmd(f"{sys.executable} -m py_compile {f}")
        
    # 2. Запуск Unit и Integration тестов
    run_cmd(f"{sys.executable} -m unittest discover -s tests")
    
    print("All checks passed!")

if __name__ == "__main__":
    main()
