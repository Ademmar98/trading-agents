"""Baseline daily report data pull from VPS."""
import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('69.48.202.100', username='root', password='CyVMhAYs8W3or', timeout=15)

def run(cmd, t=60):
    _, o, e = ssh.exec_command(cmd, timeout=t)
    return (o.read().decode(errors='replace') + e.read().decode(errors='replace')).strip()

print('=== POSITIONS ===')
print(run('curl -s localhost/api/positions | head -c 1500'))
print('\n=== JOURNAL last 25 ===')
print(run('tail -25 /root/firm/data/logs/journal.jsonl'))
print('\n=== REPORTS DIR ===')
print(run('ls -la /root/firm/data/reports/'))

db_py = (
    "python3 -c 'import sqlite3,glob\n"
    "db=glob.glob(\"/root/firm/data/*.db\")[0]\n"
    "c=sqlite3.connect(db)\n"
    "try:\n"
    " rows=c.execute(\"select strategy,trades,win_rate,pnl from strategy_stats order by pnl desc\").fetchall()\n"
    " print(len(rows),\"strategies with stats\")\n"
    " for r in rows[:15]: print(r)\n"
    "except Exception as ex: print(\"stats:\",ex)\n"
    "try: print(\"closed trades:\",c.execute(\"select count(*) from trades\").fetchone())\n"
    "except Exception as ex: print(\"trades:\",ex)\n'"
)
print('\n=== DB ===')
print(run("ls /root/firm/data/*.db && " + db_py))

print('\n=== SERVICE ERRORS 24h ===')
print(run("journalctl -u trading-firm --since '-24 h' --no-pager | grep -icE 'error|exception|traceback'"))
print(run("journalctl -u trading-firm --since '-24 h' --no-pager | grep -iE 'error|exception|traceback' | grep -viE 'read.?timeout' | tail -8"))
print('\n=== SERVICE STATE ===')
print(run('systemctl is-active trading-firm && systemctl show trading-firm -p ActiveEnterTimestamp'))
print('\n=== CYCLE TIMING ===')
print(run("grep '\"agent\": \"debate\"' /root/firm/data/logs/journal.jsonl | grep 'Debate round complete' | tail -3"))
ssh.close()
