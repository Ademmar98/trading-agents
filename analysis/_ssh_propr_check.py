import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('69.48.202.100', username='root', password='CyVMhAYs8W3or', timeout=15)

cmds = [
    ('UNITS', 'systemctl list-units --all 2>&1 | grep -i propr ; echo --- ; ls /etc/systemd/system/ 2>&1 | grep -i -E "propr|dip"'),
    ('PROCS', 'ps aux 2>&1 | grep -i -E "propr|dip_runner" | grep -v grep'),
    ('PROPR_DIR', 'ls -la /root/firm/core/exchange/propr/ 2>&1'),
    ('LOGS', 'ls -la /var/log/ 2>&1 | grep -i -E "propr|dip" ; echo --- ; find /root/firm -name "*.log" 2>&1 | head'),
    ('TRADES_JSON', 'python3 -c "import json; d=json.load(open(\'/root/firm/analysis/propr_data/propr_trades.json\')); print(len(d))"'),
    ('FIND', 'find /root/firm -name "*.json" -newer /root/firm/analysis/propr_data 2>&1 | grep -i -E "trade|propr" | head -20'),
    ('PYPROC', 'pgrep -af python3 2>&1 | head -20'),
    ('DOTENV_KEY', 'grep -c PROPR /root/firm/.env 2>&1'),
]
for name, c in cmds:
    i, o, e = ssh.exec_command(c, timeout=30)
    out = o.read().decode()
    err = e.read().decode()
    print(f"### {name}\n{out.strip()}" if out.strip() else f"### {name}\n(no output)")
    if err.strip():
        print(f"  [stderr] {err.strip()[:300]}")
    print()

ssh.close()
