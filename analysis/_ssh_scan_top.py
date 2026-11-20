import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('69.48.202.100', username='root', password='CyVMhAYs8W3or', timeout=15)

cmds = [
    ('SCAN_TOP', 'sed -n "228,258p" /opt/propr-bot/bot.py 2>&1'),
    ('GET_OPEN_POS', 'grep -n -A20 "def get_open_positions" /opt/propr-bot/propr/client.py 2>&1 | head -30'),
    ('CFG_PATIENT_DEF', 'sed -n "80,98p" /opt/propr-bot/propr/config.py 2>&1'),
    ('CFG_PATIENT_ACTUAL', 'grep -n -A6 "CFG_PATIENT = sim.Cfg" /opt/propr-bot/propr/config.py 2>&1'),
    ('CFG_PATIENT_RAW', 'sed -n "55,99p" /opt/propr-bot/propr/config.py 2>&1'),
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
