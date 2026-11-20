import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('69.48.202.100', username='root', password='CyVMhAYs8W3or', timeout=20)

def run(cmd):
    i, o, e = ssh.exec_command(cmd, timeout=60)
    return o.read().decode(errors='replace'), e.read().decode(errors='replace')

print("== patched import line ==")
out, _ = run("grep -n 'from propr import' /opt/propr-bot/bot.py 2>&1")
print(out.strip())

print("\n== patched entry block (lines 294-312) ==")
out, _ = run("sed -n '294,312p' /opt/propr-bot/bot.py 2>&1")
print(out.strip())

ssh.close()
