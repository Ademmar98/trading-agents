# -*- coding: utf-8 -*-
"""Watchdog: owns the live bot process; restarts it if it dies OR wedges
(silent >15 min while the API is reachable). Posts a Telegram notice on
every restart."""
import io, os, subprocess, sys, time, socket, signal

D = r"C:\Users\DELL\OneDrive\1m\propr-bot"
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
HALT = os.path.join(D, "cache", "HALT")
STALE_MIN = 15
TICK = 30
WATCHLOG = os.path.join(D, "watchdog.log")
HOST, PORT = "api.beta.propr.xyz", 443

def log(msg):
    with open(WATCHLOG, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")

def api_up(timeout=6):
    try:
        s = socket.create_connection((HOST, PORT), timeout=timeout)
        s.close()
        return True
    except OSError:
        return False

def tg(msg, lines):
    try:
        notify.send("WATCHDOG", msg, [notify.esc(x) for x in lines],
                    why="watchdog")
    except Exception as e:
        log(f"tg failed with boot process? {e}")

def kill_stray():
    """Kill only THIS deployment's bot (scoped by its absolute command line),
    so a second running instance is never touched."""
    marker = os.path.join(D, "fund_bot.py").replace("\\", "\\\\")
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | "
             f"Where-Object {{ $_.CommandLine -like '*{marker}*' -and "
             f"$_.ProcessId -ne $PID }} | ForEach-Object {{ $_.ProcessId }}"],
            capture_output=True, text=True, timeout=15).stdout
        pids = [ln.strip() for ln in out.splitlines() if ln.strip().isdigit()]
        for pid in pids:
            if int(pid) == os.getpid():
                continue
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
    p = subprocess.Popen(
        [sys.executable, os.path.join(D, "fund_bot.py"),
         "--live", "--interval", "300", "--gross", "0.25", "--stop", "0.24"],
        stdout=out, stderr=err, cwd=D,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return p

def main():
    log("watchdog starting")
    kill_stray()
    # A persistent halt means the account stopped itself and must stay stopped.
    # The old loop restarted a breached account every 30s: it re-armed the halt,
    # re-sent a failed Telegram message, and re-hit the API, ~2,880 times a day.
    if os.path.exists(HALT):
        try:
            why = open(HALT, encoding="utf-8").read().splitlines()[0]
        except Exception:
            why = "halt marker present"
        log(f"halted ({why}); standing down, not restarting")
        tg("halted", [why, "watchdog standing down"])
        return
    proc = None
    restarts = 0
    last_restart = 0.0
    while True:
        now = time.time()
        if proc is None or proc.poll() is not None:
            if proc is not None:
                rc = proc.poll()
                log(f"bot exited rc={rc}; restarting")
                tg("bot exited", [f"rc={rc}", "restarting now"])
            proc = spawn()
            restarts += 1
            log(f"spawned bot pid={proc.pid} (restart #{restarts})")
            last_restart = now
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
                    tg("bot wedged", [f"no log {silent_for:.0f} min",
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