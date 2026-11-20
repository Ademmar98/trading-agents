import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('69.48.202.100', username='root', password='CyVMhAYs8W3or', timeout=15)

cmds = [
    ('LOG_START', 'head -40 /var/log/propr-bot.log 2>&1'),
    ('LOG_SIGNAL', 'grep -i -E "SIGNAL|LONG|closed|exit|filled|TARGET|pass|fail|hit" /var/log/propr-bot.log 2>&1 | head -80'),
    ('LOG_BOOT', 'grep -i -E "strategy|risk|sl_atr|tp_r|universe|target|walls|starting|equity \"\$|dor|sleep" /var/log/propr-bot.log 2>&1 | head -40'),
    ('BOT_HEAD', 'head -120 /opt/propr-bot/bot.py 2>&1'),
    ('BOT_CFG', 'grep -n -E "class .*Cfg|strat|risk_pct|sl_atr|tp_r|SIGNALS|universe|PATIENT|DEADLINE|name" /opt/propr-bot/bot.py 2>&1 | head -60'),
    ('SIM_SIGNALS', 'grep -n -E "SIGNALS|def |Patient|Deadline|class.*Cfg|risk_pct|sl_atr|tp_r" /opt/propr-bot/sim.py 2>&1 | head -80'),
    ('RECENT', 'grep -i -E "closed|exit|tp_|stop_loss|take_profit|fill" /opt/propr-bot/recent_trades.py 2>&1 | head'),
    ('RECENT_RUN', 'cd /opt/propr-bot && .venv/bin/python recent_trades.py 2>&1 | head -60'),
    ('ACCT', 'cd /opt/propr-bot && .venv/bin/python acct_check.py 2>&1 | head -40'),
    ('PROPR_CFG', 'sed -n "1,120p" /opt/propr-bot/propr/config.py 2>&1'),
]
for name, c in cmds:
    i, o, e = ssh.exec_command(c, timeout=40)
    out = o.read().decode()
    err = e.read().decode()
    print(f"########## {name} ##########")
    print(out.strip() if out.strip() else "(no output)")
    if err.strip():
        print(f"  [stderr] {err.strip()[:300]}")
    print()

ssh.close()
