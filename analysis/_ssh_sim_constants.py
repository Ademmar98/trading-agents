import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('69.48.202.100', username='root', password='CyVMhAYs8W3or', timeout=20)

def run(cmd, t=60):
    i, o, e = ssh.exec_command(cmd, timeout=t)
    return o.read().decode(errors='replace'), e.read().decode(errors='replace')

print("== constants (HISTORY_DAYS, UNIVERSE_N, BARS, load) ==")
out, _ = run("grep -n 'HISTORY_DAYS\\|UNIVERSE_N\\|def load\\|UNIVERSE\\|SYMBOLS\\|^COOLOFF\\|^RISK\\|^CFG' /opt/propr-bot/sim.py 2>&1 | head -30")
print(out.strip())

print("\n== simulate() signature + head ==")
out, _ = run("sed -n '244,330p' /opt/propr-bot/sim.py 2>&1")
print(out.strip())

ssh.close()
