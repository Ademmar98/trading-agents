import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('69.48.202.100', username='root', password='CyVMhAYs8W3or', timeout=20)

def run(cmd, t=60):
    i, o, e = ssh.exec_command(cmd, timeout=t)
    return o.read().decode(errors='replace'), e.read().decode(errors='replace')

print("== OPENED LONG / closed trade lines (all) ==")
out, _ = run("grep -E 'OPENED LONG|CLOSED ' /var/log/propr-bot.log 2>&1")
print(out.strip() if out.strip() else "(none)")

print("\n== TP/stop fill mentions ==")
out, _ = run("grep -iE 'take_profit_market|stop_market .*status|filled|LEVERAGE|TP hit|target' /var/log/propr-bot.log 2>&1 | grep -iv 'profit_pct' | tail -40")
print(out.strip() if out.strip() else "(none)")

ssh.close()
