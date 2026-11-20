import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('69.48.202.100', username='root', password='CyVMhAYs8W3or', timeout=15)

cmds = [
    ('CFG_DEFS_BLOCK', 'sed -n "80,110p" /opt/propr-bot/propr/config.py 2>&1'),
    ('CFG_DATACLASS', 'grep -n "class Cfg\\|cooldown\\|max_concurrent\\|max_hold\\|sl_atr\\|tp_r" /opt/propr-bot/sim.py 2>&1 | head -40'),
]
for name, c in cmds:
    i, o, e = ssh.exec_command(c, timeout=40)
    out = o.read().decode()
    err = e.read().decode()
    print(f"########## {name} ##########")
    print(out.strip() if out.strip() else "(no output)")
    if err.strip():
        print(f"  [stderr] {err.strip()[:200]}")
    print()

ssh.close()
