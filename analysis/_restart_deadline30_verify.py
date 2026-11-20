import sys, io, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('69.48.202.100', username='root', password='CyVMhAYs8W3or', timeout=20)

def run(cmd, t=90):
    i, o, e = ssh.exec_command(cmd, timeout=t)
    return o.read().decode(errors='replace'), e.read().decode(errors='replace')

print("== restarting ==")
out, _ = run("systemctl restart propr-bot.service 2>&1; echo RESTART_EXIT=$?")
print(out.strip())

print("\n== waiting 20s ==")
time.sleep(20)

print("== service state ==")
out, _ = run("systemctl is-active propr-bot.service; echo ---; "
             "systemctl show propr-bot.service -p ActiveState,SubState,ExecMainPID 2>&1")
print(out.strip())

print("\n== boot lines (config) ==")
out, _ = run("tail -n 14 /var/log/propr-bot.log 2>&1")
print(out.strip())

print("\n== deadline warning line ==")
out, _ = run("grep 'deadline\\|NOT a 30-day' /var/log/propr-bot.log 2>&1 | tail -3")
print(out.strip())

print("\n== error scan since restart ==")
out, _ = run("awk '/^2026-08-29 23:/{f=1} f' /var/log/propr-bot.log 2>&1 | grep -iE 'ERROR cycle|Traceback|self-halt|order_create_failed' | tail -5")
print(out.strip() if out.strip() else "(none since restart)")

ssh.close()
