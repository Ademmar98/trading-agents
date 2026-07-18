"""Diagnose the stuck trading-firm process since 13:07 UTC."""
import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('69.48.202.100', username='root', password='CyVMhAYs8W3or', timeout=15)

def run(cmd, t=60):
    _, o, e = ssh.exec_command(cmd, timeout=t)
    return (o.read().decode(errors='replace') + e.read().decode(errors='replace')).strip()

print('=== CPU/state ===')
print(run("ps -o pid,stat,%cpu,etime,time,wchan:30,cmd -p 188848"))
print('\n=== journalctl since 13:07 (last 40) ===')
print(run("journalctl -u trading-firm --since '13:07:00' --no-pager | tail -40"))
print('\n=== journalctl errors since 13:07 ===')
print(run("journalctl -u trading-firm --since '13:07:00' --no-pager | grep -iE 'error|exception|traceback|stuck' | tail -15"))
print('\n=== thread stacks (names) ===')
print(run("ls /proc/188848/task | wc -l; for tid in $(ls /proc/188848/task | head 20); do echo -n \"$tid \"; cat /proc/188848/task/$tid/comm; done"))
ssh.close()
