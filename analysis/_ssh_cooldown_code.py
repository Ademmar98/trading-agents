import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('69.48.202.100', username='root', password='CyVMhAYs8W3or', timeout=15)

cmds = [
    ('SCAN_FUNC', 'sed -n "250,345p" /opt/propr-bot/bot.py 2>&1'),
    ('COOLED_REFS', 'grep -n "cooled" /opt/propr-bot/bot.py 2>&1'),
    ('CLOSE_HANDLER', 'sed -n "340,400p" /opt/propr-bot/bot.py 2>&1'),
    ('STATE_LOAD', 'grep -n "cooled\s*=" /opt/propr-bot/bot.py 2>&1; '
                   'sed -n "180,210p" /opt/propr-bot/bot.py 2>&1'),
    ('OPENED_AT_SET', 'grep -n "opened_at\|self.cooled\[" /opt/propr-bot/bot.py 2>&1'),
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
