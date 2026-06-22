import ast
import os
import sys

def find_magic_numbers_in_file(filepath):
    results = []
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        tree = ast.parse(content, filename=filepath)
    except Exception as e:
        return results

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            val = node.value
            # Игнорируем частые не "магические" числа: 0, 1, 2, -1
            if val not in [0, 1, 2, -1, 0.0, 1.0, -1.0, 100]:
                # Игнорируем если это часть конфига/датакласса (приблизительно)
                # или просто выводим всё для ручного ревью
                results.append((node.lineno, val))
    return results

def main():
    ignore_dirs = ['venv', '.git', '__pycache__', 'tests', 'migrations', 'scripts', 'ui_config', 'penny_config', 'whale_config', 'calibration_config', 'infra_config']
    
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    report_lines = ["# Отчет по потенциальным магическим числам\n"]
    
    for root, dirs, files in os.walk(root_dir):
        dirs[:] = [d for d in dirs if d not in ignore_dirs]
        for file in files:
            if file.endswith('.py') and not file.endswith('config.py'):
                filepath = os.path.join(root, file)
                numbers = find_magic_numbers_in_file(filepath)
                if numbers:
                    rel_path = os.path.relpath(filepath, root_dir)
                    report_lines.append(f"## {rel_path}")
                    for lineno, val in numbers:
                        report_lines.append(f"- Строка {lineno}: Значение `{val}`")
                    report_lines.append("")
                    
    report_path = os.path.join(root_dir, 'magic_numbers_report.md')
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write("\n".join(report_lines))
        
    print(f"Отчет сохранен в {report_path}")

if __name__ == '__main__':
    main()
