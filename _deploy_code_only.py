"""Code-only VPS deploy (NO data wipe): git reset to origin/main + restart."""
import sys, time

HOST, USER, APP, SVC = "69.48.202.100", "root", "/root/firm", "trading-firm"

def run(ssh, cmd, timeout=120):
    _, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode(errors="replace").strip()
    err = stderr.read().decode(errors="replace").strip()
    return stdout.channel.recv_exit_status(), out, err

pw = sys.argv[1]
import paramiko
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(HOST, username=USER, password=pw, timeout=15)
print("connected")

code, out, err = run(ssh, f"cd {APP} && git fetch origin && git reset --hard origin/main && git log --oneline -1")
print("code:", out or err)
if code != 0:
    sys.exit(1)

code, out, err = run(ssh, f"systemctl restart {SVC} && sleep 4 && systemctl is-active {SVC}")
print("service:", out or err)
ssh.close()
