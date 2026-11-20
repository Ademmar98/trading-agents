import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('69.48.202.100', username='root', password='CyVMhAYs8W3or', timeout=15)

cmds = [
    ('MARKET_BUY_DEF', 'sed -n "400,460p" /opt/propr-bot/propr/propr_sdk.py 2>&1'),
    ('ERR_HANDLING', 'grep -n "raise\\|def _request\\|def _post\\|13051\\|forbidden\\|error\\|return.*None\\|response" /opt/propr-bot/propr/propr_sdk.py 2>&1 | head -40'),
    ('REQUEST_METHOD', 'grep -n "def " /opt/propr-bot/propr/propr_sdk.py 2>&1 | head -40'),
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
