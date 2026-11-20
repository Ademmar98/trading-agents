import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('69.48.202.100', username='root', password='CyVMhAYs8W3or', timeout=20)

def run(cmd, t=60):
    i, o, e = ssh.exec_command(cmd, timeout=t)
    return o.read().decode(errors='replace'), e.read().decode(errors='replace')

print("== sig_mom (190-243) ==")
out, _ = run("sed -n '190,243p' /opt/propr-bot/sim.py 2>&1")
print(out.strip())

print("\n== cmd_marathon (698-757) ==")
out, _ = run("sed -n '698,757p' /opt/propr-bot/sim.py 2>&1")
print(out.strip())

print("\n== cmd_horizon head (823-880) ==")
out, _ = run("sed -n '823,880p' /opt/propr-bot/sim.py 2>&1")
print(out.strip())

ssh.close()
