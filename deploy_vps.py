"""One-shot VPS deploy + full reset for the trading firm.

Usage:
    py -3 deploy_vps.py <ssh_password> [user] [host]

What it does (all on the VPS, via SSH):
  1. Finds the app directory (git repo containing main.py)
  2. git fetch + reset --hard origin/main   (exact code from GitHub)
  3. Installs deps if requirements.txt changed
  4. Stops the bot, backs up data/ to data_backups/, wipes it  (FULL RESET)
  5. Restarts the bot (systemd > docker-compose > nohup, auto-detected)
  6. Verifies via the dashboard API that equity is back to fresh capital
"""
import sys, time, json, urllib.request

HOST = "69.48.202.100"
USER = "root"

def run(ssh, cmd, timeout=120):
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode(errors="replace").strip()
    err = stderr.read().decode(errors="replace").strip()
    code = stdout.channel.recv_exit_status()
    return code, out, err

def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    pw = sys.argv[1]
    user = sys.argv[2] if len(sys.argv) > 2 else USER
    host = sys.argv[3] if len(sys.argv) > 3 else HOST

    import paramiko
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(host, username=user, password=pw, timeout=15)
    print(f"[1/6] Connected to {user}@{host}")

    # 2. Locate app dir
    code, out, _ = run(ssh, "for d in /root/trading-agents /opt/trading-agents /app /srv/trading-agents ~/trading-agents; do [ -f $d/main.py ] && echo $d; done")
    app = out.splitlines()[0] if out else ""
    if not app:
        code, out, _ = run(ssh, "find /root /opt /srv /home -maxdepth 3 -name main.py -path '*trading*' 2>/dev/null | head -1")
        app = out.rsplit("/", 1)[0] if out else ""
    if not app:
        print("FATAL: could not find the app directory on the VPS"); sys.exit(1)
    print(f"[2/6] App dir: {app}")

    # 3. Update code to exact GitHub main
    code, out, err = run(ssh, f"cd {app} && git fetch origin && git reset --hard origin/main && git log --oneline -1")
    print(f"[3/6] Code updated: {out.splitlines()[-1] if out else err}")
    if code != 0:
        print("FATAL: git update failed:", err); sys.exit(1)
    run(ssh, f"cd {app} && (command -v pip3 >/dev/null && pip3 install -q -r requirements.txt || true)", timeout=300)

    # 4. Stop bot, backup + wipe data
    ts = time.strftime("%Y%m%d_%H%M%S")
    run(ssh, "systemctl stop trading-agents trading 2>/dev/null; "
             f"cd {app} && (docker compose down 2>/dev/null || docker-compose down 2>/dev/null || true); "
             "pkill -f 'python.*main.py' 2>/dev/null; pkill -f prod_run 2>/dev/null; sleep 2; true")
    code, out, err = run(ssh, f"cd {app} && mkdir -p data_backups && "
                              f"[ -d data ] && cp -r data data_backups/pre_reset_{ts} || true; "
                              "rm -rf data && mkdir -p data/analyses data/decisions data/orders data/reports data/logs data/candles && echo WIPED")
    print(f"[4/6] Data backed up to data_backups/pre_reset_{ts} and wiped: {out}")

    # 5. Restart bot (systemd > docker > nohup)
    code, out, _ = run(ssh, "systemctl list-units --all 2>/dev/null | grep -iE 'trading' | head -2")
    if out:
        svc = out.split()[0].replace("●", "").strip()
        code, o, e = run(ssh, f"systemctl start {svc} && sleep 3 && systemctl is-active {svc}")
        print(f"[5/6] Restarted systemd service '{svc}': {o or e}")
    else:
        code, out, _ = run(ssh, f"[ -f {app}/docker-compose.yml ] && echo has_docker")
        if out:
            code, o, e = run(ssh, f"cd {app} && (docker compose up -d --build || docker-compose up -d --build)", timeout=600)
            print(f"[5/6] Restarted via docker compose: {(o or e)[-200:]}")
        else:
            code, o, e = run(ssh, f"cd {app} && HEADLESS=true nohup python3 prod_run.py > data/logs/nohup.log 2>&1 & sleep 3; pgrep -fa 'prod_run|main.py' | head -2")
            print(f"[5/6] Restarted via nohup: {o or e}")

    # 6. Verify fresh state via API
    time.sleep(15)
    try:
        s = json.loads(urllib.request.urlopen(f"http://{host}/api/summary", timeout=15).read())
        print(f"[6/6] VERIFY: equity=${s['equity']:,.2f} trades={s['total_trades']} open={s['open_positions']}")
        if s["total_trades"] == 0 and abs(s["equity"] - s["initial_balance"]) < 1:
            print("SUCCESS: VPS is on fresh code with a clean book.")
        else:
            print("WARNING: bot answers but state is not fresh — check data reset.")
    except Exception as ex:
        print(f"[6/6] WARNING: dashboard not answering yet ({ex}). Check in a minute: http://{host}/api/summary")
    ssh.close()

if __name__ == "__main__":
    main()
