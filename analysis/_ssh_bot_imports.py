import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('69.48.202.100', username='root', password='CyVMhAYs8W3or', timeout=15)

cmds = [
    ('BOT_IMPORTS', 'sed -n "1,30p" /opt/propr-bot/bot.py 2>&1'),
    ('ENTRY_NUMS', 'grep -n "open_long\\|refused by the risk gate\\|DRY RUN" /opt/propr-bot/bot.py 2>&1'),
    ('PROPRAPIERR', 'sed -n "33,45p" /opt/propr-bot/propr/propr_sdk.py 2>&1'),
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
