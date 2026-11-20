import sys, io, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('69.48.202.100', username='root', password='CyVMhAYs8W3or', timeout=20)

def run(cmd, t=60):
    i, o, e = ssh.exec_command(cmd, timeout=t)
    return o.read().decode(errors='replace'), e.read().decode(errors='replace')

print("== wait 40s for first scan cycles ==")
time.sleep(40)

print("== log tail after restart (last 30 lines) ==")
out, _ = run("tail -n 30 /var/log/propr-bot.log 2>&1")
print(out.strip())

print("\n== error scan since restart (order_create_failed / cycle errors) ==")
out, _ = run("grep -n 'order_create_failed\\|ERROR cycle\\|Traceback\\|self-halt' /var/log/propr-bot.log 2>&1 | tail -10")
print(out.strip() if out.strip() else "(none recent)")

print("\n== most recent status ping ==")
out, _ = run("grep -E '\\[1\\] equity|\\[2\\] equity|profit ' /var/log/propr-bot.log 2>&1 | tail -5")
print(out.strip())

ssh.close()
