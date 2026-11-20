import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('69.48.202.100', username='root', password='CyVMhAYs8W3or', timeout=20)

def run(cmd, t=60):
    i, o, e = ssh.exec_command(cmd, timeout=t)
    return o.read().decode(errors='replace'), e.read().decode(errors='replace')

print("== DEADLINE_DAYS line ==")
out, _ = run("grep -n 'DEADLINE_DAYS' /opt/propr-bot/bot.py 2>&1")
print(out.strip())

print("\n== surrounding comment block (lines 60-72) ==")
out, _ = run("sed -n '60,72p' /opt/propr-bot/bot.py 2>&1")
print(out.strip())

print("\n== current running config (from latest boot line) ==")
out, _ = run("grep 'Propr runner' -A0 /var/log/propr-bot.log 2>&1 | tail -1; "
             "grep 'risk0.5%\\|risk1.0%' /var/log/propr-bot.log 2>&1 | tail -2")
print(out.strip())

ssh.close()
