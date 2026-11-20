# -*- coding: utf-8 -*-
"""Watchdog (5K DEMO #2): owns THIS deployment's bot process; restarts it if it
dies OR wedges (silent >15 min while the API is reachable). Posts a Telegram
notice on every restart.

Scoped to propr-bot-5k2 by absolute path, so the beta and 5K deployments are
never touched. Exits quietly (no restart loop, no Telegram spam) while the
folder is not yet configured, which makes it safe to leave in the Startup list
before the account credentials exist.
"""
import os
import socket
import subprocess
import sys
import time

D = r"C:\Users\DELL\OneDrive\1m\propr-bot-5k2"
sys.path.insert(0, D)
os.chdir(D)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(D, ".env"), override=True)
except ImportError:
    pass

import notify

LOG = os.path.join(D, "live_run.log")
ERR = os.path.join(D, "live_run.err")
STALE_MIN = 15
TICK = 30
WATCHLOG = os.path.join(D, "watchdog.log")
HOST, PORT = "api.propr.xyz", 443
TAG = "WATCHDOG-5K2"


def log(msg):
    with open(WATCHLOG, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")


def configured():
    """True only when .env holds real credentials. Placeholders must not spawn
    a bot that would fail on every restart."""
    key = os.getenv("PROPR_API_KEY", "").strip()
    acct = os.getenv("PROPR_ACCOUNT_ID", "").strip()
    if not key.startswith(("pk_live_", "pk_beta_")) or len(key) < 30:
        return False, "PROPR_API_KEY missing or placeholder"
    if not acct.startswith("urn:prp-account:") or "PASTE" in acct.upper():
        return False, "PROPR_ACCOUNT_ID missing or placeholder"
    if not os.getenv("CHALLENGE_START", "").strip():
        return False, "CHALLENGE_START missing"
    return True, ""


def api_up(timeout=6):
    try:
        s = socket.create_connection((HOST, PORT), timeout=timeout)
        s.close()
        return True
    except OSError:
        return False


def tg(msg, lines):
    try:
        notify.send(TAG, msg, lines, why="watchdog")
    except Exception as e:
        log(f"tg failed: {e}")


def kill_stray():
    """Kill only THIS deployment's bot processes.

    Matches python.exe AND pythonw.exe: the bot is spawned with sys.executable,
    which is pythonw.exe when the watchdog itself runs windowless, so a filter
    on python.exe alone would leave an orphan trading alongside the new one.
    """
    marker = os.path.join(D, "fund_bot.py")
    ps = (
        "Get-CimInstance Win32_Process -Filter "
        "\"name='python.exe' OR name='pythonw.exe'\" | "
        f"Where-Object {{ $_.CommandLine -like '*{marker}*' -and "
        "$_.ProcessId -ne $PID } | ForEach-Object { $_.ProcessId }")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, timeout=20).stdout
        pids = [ln.strip() for ln in out.splitlines() if ln.strip().isdigit()]
        for pid in pids:
            try:
                subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     f"Stop-Process -Id {pid} -Force"],
                    capture_output=True, timeout=15)
            except Exception:
                pass
        if pids:
            log(f"killed stray bot procs: {pids}")
    except Exception as e:
        log(f"kill_stray: {e}")


def spawn():
    out = open(LOG, "ab")
    err = open(ERR, "ab")
    return subprocess.Popen(
        [sys.executable, os.path.join(D, "fund_bot.py"),
         "--live", "--interval", "300",
         "--gross", "0.20", "--stop", "0.20",
         "--daily-wall", "0.03", "--dd-wall", "0.06",
         "--daily-halt", "0.02", "--dd-halt", "0.05"],
        stdout=out, stderr=err, cwd=D,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def main():
    ok, why = configured()
    if not ok:
        log(f"not starting: {why} (fill .env, run preflight.py, then start)")
        return 0
    log(f"{TAG} starting; account {os.getenv('PROPR_ACCOUNT_ID')} "
        f"start ${os.getenv('CHALLENGE_START')}")
    kill_stray()
    proc = None
    restarts = 0
    last_restart = 0.0
    recent = []                       # spawn times, for crash backoff
    while True:
        now = time.time()
        if proc is None or proc.poll() is not None:
            if proc is not None:
                rc = proc.poll()
                if rc == 3:
                    log("bot refused to start: another instance owns this "
                        "folder. Watchdog exiting so the live one keeps it.")
                    tg("5K2 duplicate prevented",
                       ["another bot already running for this folder",
                        "watchdog exiting"])
                    return 0
                log(f"bot exited rc={rc}; restarting")
                tg("5K2 bot exited", [f"rc={rc}", "restarting"])
                recent = [t for t in recent if now - t < 300]
                if len(recent) >= 4:
                    log("4 exits in 5 min — backing off 5 min")
                    tg("5K2 crash loop", ["4 exits in 5 min", "backing off 5m"])
                    time.sleep(300)
                    recent = []
            proc = spawn()
            restarts += 1
            recent.append(now)
            log(f"spawned 5K2 bot pid={proc.pid} (restart #{restarts})")
            last_restart = now
            # A misconfigured bot dies in seconds; say so instead of looping in
            # silence.
            time.sleep(8)
            if proc.poll() is not None:
                tail = ""
                try:
                    with open(ERR, "r", encoding="utf-8", errors="ignore") as f:
                        tail = "".join(f.readlines()[-3:]).strip()[-400:]
                except OSError:
                    pass
                log(f"bot died within 8s (rc={proc.returncode}): {tail}")
        else:
            try:
                file_mtime = os.path.getmtime(LOG)
            except OSError:
                file_mtime = 0
            silent_for = (now - file_mtime) / 60.0
            if silent_for > STALE_MIN and (now - last_restart) > STALE_MIN * 60:
                if api_up():
                    log(f"bot wedged: no log for {silent_for:.0f} min, API up; "
                        f"killing pid {proc.pid}")
                    tg("5K2 bot wedged", [f"no log {silent_for:.0f} min",
                                          "killing + restarting"])
                    try:
                        proc.terminate()
                        proc.wait(timeout=10)
                    except Exception:
                        proc.kill()
                    proc = None
                    continue
                else:
                    log(f"silent {silent_for:.0f} min but API down — waiting")
        time.sleep(TICK)


if __name__ == "__main__":
    main()
