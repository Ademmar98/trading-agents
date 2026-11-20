import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('69.48.202.100', username='root', password='CyVMhAYs8W3or', timeout=15)

cmds = [
    ('BOT_ENTRY_BLOCK', 'sed -n "294,316p" /opt/propr-bot/bot.py 2>&1'),
    ('OPEN_LONG_BODY', 'sed -n "300,360p" /opt/propr-bot/propr/client.py 2>&1'),
    ('MARKET_BUY', 'grep -n -A25 "def market_buy" /opt/propr-bot/propr/sdk.py 2>&1 | head -35'),
    ('MARKET_BUY_EXC', 'grep -n "13051\\|order_create_failed\\|raise\\|class .*Error\\|def _request\\|def request" /opt/propr-bot/propr/sdk.py 2>&1 | head -30'),
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
