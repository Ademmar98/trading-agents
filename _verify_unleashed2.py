"""Verify the CURRENT process runs unleashed: latest regime line, trades, executions."""
import paramiko, time

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('69.48.202.100', username='root', password='CyVMhAYs8W3or', timeout=15)

def run(cmd, t=60):
    _, o, e = ssh.exec_command(cmd, timeout=t)
    return (o.read().decode(errors='replace') + e.read().decode(errors='replace')).strip()

print('=== process start ===')
print(run("ps -o pid,lstart,cmd -C python | head -3; ps aux | grep main.py | grep -v grep | awk '{print $2, $9}'"))

print('=== latest regime/deploy lines (with UTC time) ===')
out = run("grep 'Regime scan' /root/firm/data/logs/journal.jsonl | tail -3")
for line in out.splitlines():
    import json
    try:
        d = json.loads(line)
        print(time.strftime('%H:%M:%S', time.gmtime(d['time'])), 'UTC |', d['message'][-90:])
    except Exception:
        print(line)

print('=== compliance/execution last 6 ===')
print(run("grep -E 'Compliance:|Execution plan|Trade opened|OPENED|Position opened' /root/firm/data/logs/journal.jsonl | tail -6"))

print('=== summary ===')
print(run('curl -s localhost/api/summary'))

print('=== trades today ===')
print(run("curl -s localhost/api/trades | python3 -c \"import json,sys; t=json.load(sys.stdin); print('closed:', len(t)); [print(x.get('symbol'), x.get('pnl'), x.get('strategy') or x.get('strategies')) for x in t[-8:]]\""))

print('=== open positions ===')
print(run("curl -s localhost/api/positions | python3 -c \"import json,sys; p=json.load(sys.stdin); print('open:', len(p)); [print(x['symbol'], round(x['pnl_pct'],2), '%') for x in p]\""))
ssh.close()
