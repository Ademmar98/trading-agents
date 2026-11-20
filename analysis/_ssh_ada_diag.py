import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('69.48.202.100', username='root', password='CyVMhAYs8W3or', timeout=15)

cmds = [
    ('ADA_ERR_CONTEXT', 'grep -n "order_create_failed" /var/log/propr-bot.log 2>&1 | head -5 ; '
                        'echo "---count by asset---"; grep "order_create_failed" /var/log/propr-bot.log 2>&1 | wc -l'),
    ('ADA_OPEN', 'grep -n -A3 -B3 "SIGNAL ADA" /var/log/propr-bot.log 2>&1 | head -40'),
    ('ADA_13051', 'grep -n -B2 -A1 "2\\|1" /dev/null 2>&1; '
                  'grep -n "CLOSED ADA" /var/log/propr-bot.log 2>&1'),
    ('BOT_POSITION_CHECK', 'sed -n "200,290p" /opt/propr-bot/bot.py 2>&1'),
    ('OPEN_LONG', 'grep -n -A30 "def open_long" /opt/propr-bot/propr/client.py 2>&1 | head -60'),
    ('ERR_FULL', 'grep -n "order_create_failed" /var/log/propr-bot.log 2>&1 | sed -n "1,3p;40,45p"'),
    ('ADA_POSITIONS_AT_TIME', 'grep -n "open 2\\|open 1\\|open 0" /var/log/propr-bot.log 2>&1 | sed -n "1,3p"'),
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
