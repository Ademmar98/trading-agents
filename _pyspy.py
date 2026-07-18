"""py-spy stack dump of the spinning process."""
import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('69.48.202.100', username='root', password='CyVMhAYs8W3or', timeout=15)

def run(cmd, t=120):
    _, o, e = ssh.exec_command(cmd, timeout=t)
    return (o.read().decode(errors='replace') + e.read().decode(errors='replace')).strip()

print(run("command -v py-spy || /root/firm/venv/bin/pip install -q py-spy 2>&1 | tail -1; command -v py-spy || echo /root/firm/venv/bin/py-spy"))
print('=== stack dump x2 (2s apart) ===')
print(run("py-spy dump --pid 188848 2>&1 || /root/firm/venv/bin/py-spy dump --pid 188848 2>&1"))
import time; time.sleep(2)
print(run("py-spy dump --pid 188848 2>&1 || /root/firm/venv/bin/py-spy dump --pid 188848 2>&1"))
ssh.close()
