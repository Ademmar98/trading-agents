import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('69.48.202.100', username='root', password='CyVMhAYs8W3or', timeout=15)

cmds = [
    ('CFG_DEFS', 'grep -n "CFG_PATIENT\\|CFG =\|cooldown" /opt/propr-bot/propr/config.py 2>&1'),
    ('LIVE_CFG_SEL', 'grep -n "CFG =\|CFG_PATIENT\\|CFG_AGGRESSIVE\\|CFG_BALANCED\\|DEADLINE" /opt/propr-bot/bot.py 2>&1 | head -30'),
    ('SERVICE_CMD', 'systemctl show propr-bot.service -p ExecStart 2>&1; '
                    'grep -rn "cooldown" /opt/propr-bot/propr/config.py 2>&1'),
    ('BOT_ARGS', 'cat /etc/systemd/system/propr-bot.service 2>&1'),
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
