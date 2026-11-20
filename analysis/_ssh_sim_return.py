import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('69.48.202.100', username='root', password='CyVMhAYs8W3or', timeout=20)

def run(cmd, t=60):
    i, o, e = ssh.exec_command(cmd, timeout=t)
    return o.read().decode(errors='replace'), e.read().decode(errors='replace')

print("== def prep ==")
out, _ = run("grep -n 'def prep\\|def describe\\|def windows\\|def grid' /opt/propr-bot/sim.py 2>&1")
print(out.strip())

print("\n== simulate() return dict (lines 330-460) ==")
out, _ = run("sed -n '330,460p' /opt/propr-bot/sim.py 2>&1")
print(out.strip())

ssh.close()
