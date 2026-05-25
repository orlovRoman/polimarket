import sys

with open('run_team.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_lines = []
in_loop = False
for i, line in enumerate(lines):
    if line.startswith('    for i, m in enumerate(markets):'):
        new_lines.append('    for i, m in enumerate(markets):\n')
        new_lines.append('        try:\n')
        in_loop = True
        continue
    
    if in_loop:
        if line.startswith('    if not new_markets_found:'):
            # End of loop
            in_loop = False
            new_lines.append('        except Exception as e:\n')
            new_lines.append('            log(f"[ОШИБКА] Рынок {m.title}: {e}. Пропускаем.")\n')
            new_lines.append('            continue\n\n')
            new_lines.append(line)
        else:
            if line.strip() == '':
                new_lines.append(line)
            else:
                new_lines.append('    ' + line)
    else:
        new_lines.append(line)

with open('run_team.py', 'w', encoding='utf-8') as f:
    f.writelines(new_lines)
print('Done!')
