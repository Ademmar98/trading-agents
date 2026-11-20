import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('69.48.202.100', username='root', password='CyVMhAYs8W3or', timeout=20)

def run(cmd, t=90):
    i, o, e = ssh.exec_command(cmd, timeout=t)
    return o.read().decode(errors='replace'), e.read().decode(errors='replace')

print("== recent_trades.py ==")
out, _ = run("cd /opt/propr-bot && .venv/bin/python recent_trades.py 2>&1")
print(out.strip() if out.strip() else "(no output)")

print("\n== acct_check.py ==")
out, _ = run("cd /opt/propr-bot && .venv/bin/python acct_check.py 2>&1")
print(out.strip() if out.strip() else "(no output)")

ssh.close()
