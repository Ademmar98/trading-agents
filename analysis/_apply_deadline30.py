import sys, io, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import paramiko

HOST, USER, PWD = '69.48.202.100', 'root', 'CyVMhAYs8W3or'
PATH = '/opt/propr-bot/bot.py'
CRLF = '\r\n'

OLD = "DEADLINE_DAYS = 90" + CRLF
NEW = "DEADLINE_DAYS = 30" + CRLF

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(HOST, username=USER, password=PWD, timeout=20)
sftp = ssh.open_sftp()

data = sftp.open(PATH, 'rb').read()
text = data.decode('utf-8')
orig_len = len(data)

n = text.count(OLD)
print(f"DEADLINE_DAYS = 90 occurrences: {n}")
if n != 1:
    print("ERROR: target line not found exactly once. Aborting WITHOUT write.")
    ssh.close()
    sys.exit(1)
text = text.replace(OLD, NEW, 1)

ts = time.strftime('%Y%m%d-%H%M%S')
bak = f"{PATH}.deadline30.bak-{ts}"
sftp.rename(PATH, bak)
print(f"backup -> {bak}")

new_bytes = text.encode('utf-8')
sftp.open(PATH, 'wb').write(new_bytes)
print(f"wrote {PATH} ({len(new_bytes)} bytes vs {orig_len} orig)")

def run(cmd):
    i, o, e = ssh.exec_command(cmd, timeout=60)
    return o.read().decode(errors='replace'), e.read().decode(errors='replace')

print("\n== confirm change ==")
out, _ = run("grep -n 'DEADLINE_DAYS = ' /opt/propr-bot/bot.py 2>&1")
print(out.strip())

print("\n== py_compile syntax check ==")
out, _ = run("cd /opt/propr-bot && .venv/bin/python -m py_compile bot.py 2>&1; echo EXIT=$?")
print(out.strip())

sftp.close()
ssh.close()
print("\nService NOT restarted yet - verify syntax before restarting.")
