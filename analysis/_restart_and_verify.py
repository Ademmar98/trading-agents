import sys, io, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('69.48.202.100', username='root', password='CyVMhAYs8W3or', timeout=20)

def run(cmd):
    i, o, e = ssh.exec_command(cmd, timeout=90)
    return o.read().decode(errors='replace'), e.read().decode(errors='replace')

print("== state BEFORE restart ==")
out, _ = run("systemctl is-active propr-bot.service; echo ---; "
             "systemctl show propr-bot.service -p ActiveState,SubState,ExecMainStartTimestamp 2>&1")
print(out.strip())

print("\n== capturing log size to detect new output ==")
out, _ = run("wc -c < /var/log/propr-bot.log")
print(out.strip())

print("\n== restarting service ==")
out, err = run("systemctl restart propr-bot.service 2>&1; echo RESTART_EXIT=$?")
print(out.strip())

print("\n== waiting 15s for boot ==")
time.sleep(15)

print("== state AFTER restart ==")
out, _ = run("systemctl is-active propr-bot.service; echo ---; "
             "systemctl show propr-bot.service -p ActiveState,SubState,NSpawn,ExecMainPID 2>&1; echo ---; "
             "journalctl -u propr-bot.service -n 25 --no-pager 2>&1 | tail -25")
print(out.strip())

ssh.close()
