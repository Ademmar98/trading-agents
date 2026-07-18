"""Check why SCOUT vars didn't take effect on the VPS."""
import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('69.48.202.100', username='root', password='CyVMhAYs8W3or', timeout=15)

def run(cmd, t=60):
    _, o, e = ssh.exec_command(cmd, timeout=t)
    return (o.read().decode(errors='replace') + e.read().decode(errors='replace')).strip()

print('=== process env ===')
print(run("cat /proc/188848/environ | tr '\\0' '\\n' | grep -E 'SCOUT|RISK_PER_TRADE|SMA200|MAX_TRADES' | head -10"))
print('=== .env tail ===')
print(run('tail -32 /root/firm/.env'))
print('=== .env duplicates of SCOUT ===')
print(run("grep -n 'SCOUT' /root/firm/.env"))
print('=== config read test inside venv ===')
print(run("cd /root/firm && venv/bin/python -c \"import config; print('scout', config.SCOUT_MODE_ENABLED, config.SCOUT_MAX_DEPLOY_PCT, config.SCOUT_RISK_PER_TRADE_PCT); print('risk', config.RISK_PER_TRADE_PCT); print('day', config.MAX_TRADES_PER_DAY)\""))
ssh.close()
