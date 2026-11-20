import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('69.48.202.100', username='root', password='CyVMhAYs8W3or', timeout=20)

def run(cmd, t=90):
    i, o, e = ssh.exec_command(cmd, timeout=t)
    return o.read().decode(errors='replace'), e.read().decode(errors='replace')

# Pull a wider trade window by parsing the log for OPENED LONG and CLOSED pairs,
# and also count filled sells by type if the log has order status lines.
print("== count of OPENED LONG per asset ==")
out, _ = run("grep -oP 'OPENED LONG \\K[A-Z0-9]+' /var/log/propr-bot.log 2>&1 | sort | uniq -c | sort -rn")
print(out.strip() if out.strip() else "(none)")

print("\n== every CLOSED line with timestamp (full run) ==")
out, _ = run("grep 'CLOSED ' /var/log/propr-bot.log 2>&1")
print(out.strip() if out.strip() else "(none)")

print("\n== total OPENED LONG count / total CLOSED count ==")
out, _ = run("echo -n 'opened='; grep -c 'OPENED LONG' /var/log/propr-bot.log; "
             "echo -n 'closed='; grep -c 'CLOSED ' /var/log/propr-bot.log; "
             "echo -n 'order_create_failed='; grep -c 'order_create_failed' /var/log/propr-bot.log; "
             "echo -n 'refused?='; grep -c 'refused by the risk gate' /var/log/propr-bot.log")
print(out.strip())

ssh.close()
