import subprocess
out = subprocess.check_output(['sudo', 'journalctl', '-u', 'polymarket-bot.service', '--since', '4 hours ago', '--no-pager'], text=True)
for line in out.splitlines():
    if 'SCOUT' in line:
        print(line)
