import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('69.48.202.100', username='root', password='CyVMhAYs8W3or', timeout=15)

cmds = [
    ('ALL_TRADES', 'cd /opt/propr-bot && .venv/bin/python -c "import propr_sdk,os; " 2>&1; echo skip; '
                   'grep -i -E "pnl=" /dev/null 2>&1; cat recent_trades.py 2>&1'),
    ('RECENT100', 'cd /opt/propr-bot && .venv/bin/python recent_trades.py --n 100 2>&1 | head -120'),
    ('RECENT_MORE', 'cd /opt/propr-bot && .venv/bin/python recent_trades.py 2>&1'),
    ('EQ_FIRST', 'head -1 /var/log/propr-bot.log 2>&1; grep -i -E "equity \\$" /var/log/propr-bot.log 2>&1 | awk "NR==1||NR==200||NR==400||NR==600||NR==800||NR==1000||NR==1200||NR==1400||NR==1550" '),
    ('ALL_THE_WAY', 'grep -i -E "equity \\$" /var/log/propr-bot.log 2>&1 | sed -n "1p;500p;1000p;1500p;2000p;2500p;3000p;3500p;4000p;4500p;5000p"'),
    ('COUNT_OPENED', 'grep -c "OPENED LONG" /var/log/propr-bot.log 2>&1'),
    ('COUNT_CLOSED', 'grep -c "CLOSED " /var/log/propr-bot.log 2>&1'),
    ('COUNT_REFUSED', 'grep -c "refused by the risk gate" /var/log/propr-bot.log 2>&1'),
    ('COUNT_ERR', 'grep -c "ERROR cycle" /var/log/propr-bot.log 2>&1'),
    ('WALL_HITS', 'grep -i -E "DAILY LOSS BREACH|DRAWDOWN|CHALLENGE|target reached|TARGET" /var/log/propr-bot.log 2>&1 | tail -20'),
]
for name, c in cmds:
    i, o, e = ssh.exec_command(c, timeout=40)
    out = o.read().decode()
    err = e.read().decode()
    print(f"########## {name} ##########")
    print(out.strip() if out.strip() else "(no output)")
    if err.strip():
        print(f"  [stderr] {err.strip()[:200]}")
    print()

ssh.close()
