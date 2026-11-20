import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('69.48.202.100', username='root', password='CyVMhAYs8W3or', timeout=15)

cmds = [
    ('BOT_DIR', 'ls -la /opt/propr-bot/ 2>&1 | head -40'),
    ('BOT_FILES', 'find /opt/propr-bot -maxdepth 2 -name "*.py" 2>&1 | head -30'),
    ('LOG_LAST', 'tail -60 /var/log/propr-bot.log 2>&1'),
    ('LOG_LINES', 'wc -l /var/log/propr-bot.log 2>&1'),
    ('LOG_GREP_TRADE', 'grep -i -E "closed|filled|tp|stop|exit|pnl|profit" /var/log/propr-bot.log 2>&1 | tail -30'),
    ('CONFIG', 'grep -i -E "symbol|target|sl|tp|risk|source|wallet|signal|long" /opt/propr-bot/*.py 2>&1 | head -40'),
]
for name, c in cmds:
    i, o, e = ssh.exec_command(c, timeout=30)
    out = o.read().decode()
    err = e.read().decode()
    print(f"########## {name} ##########")
    print(out.strip() if out.strip() else "(no output)")
    if err.strip():
        print(f"  [stderr] {err.strip()[:200]}")
    print()

ssh.close()
