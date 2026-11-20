import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('69.48.202.100', username='root', password='CyVMhAYs8W3or', timeout=15)

cmds = [
    ('FIND_SDK', 'ls /opt/propr-bot/propr/ 2>&1; echo ---; grep -rn "def market_buy" /opt/propr-bot/ --include=*.py 2>&1 | grep -v ".venv" | head'),
    ('FIND_13051', 'grep -rn "13051\\|order_create_failed" /opt/propr-bot/ --include=*.py 2>&1 | grep -v ".venv" | head'),
    ('FIND_SDK_CLASS', 'grep -rn "class ProprSDK\\|self.sdk\\|sdk =\\|market_buy" /opt/propr-bot/bot.py /opt/propr-bot/propr/client.py 2>&1 | head'),
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
