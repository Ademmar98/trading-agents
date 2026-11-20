import sys, io, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import paramiko

HOST, USER, PWD = '69.48.202.100', 'root', 'CyVMhAYs8W3or'
PATH = '/opt/propr-bot/bot.py'
CRLF = '\r\n'

# --- exact original strings (CRLF preserved) ---
OLD_IMPORT = "from propr import ProprConfig, ProprRiskClient" + CRLF
NEW_IMPORT = "from propr import ProprConfig, ProprRiskClient, ProprAPIError" + CRLF

OLD_BLOCK = (
    "            if self.client.open_long(asset=asset, quantity=qty, stop_price=sl," + CRLF +
    "                                     take_profit_price=tp, entry_price=px):" + CRLF +
    "                held.add(asset)" + CRLF +
    "                self.opened_at[asset] = time.time()" + CRLF +
    "                self._save_state()" + CRLF +
    "            else:" + CRLF +
    "                log.warning(f\"  {asset} refused by the risk gate\")" + CRLF
)

NEW_BLOCK = (
    "            try:" + CRLF +
    "                ok = self.client.open_long(asset=asset, quantity=qty," + CRLF +
    "                                            stop_price=sl," + CRLF +
    "                                            take_profit_price=tp, entry_price=px)" + CRLF +
    "            except ProprAPIError as e:" + CRLF +
    "                log.warning(f\"  {asset}: order rejected ({e.code}) - "
    "cooling {self.cfg.cooldown}h\")" + CRLF +
    "                self.cooled[asset] = time.time()" + CRLF +
    "                self._save_state()" + CRLF +
    "                continue" + CRLF +
    "            if ok:" + CRLF +
    "                held.add(asset)" + CRLF +
    "                self.opened_at[asset] = time.time()" + CRLF +
    "                self._save_state()" + CRLF +
    "            else:" + CRLF +
    "                log.warning(f\"  {asset} refused by the risk gate\")" + CRLF
)

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(HOST, username=USER, password=PWD, timeout=20)
sftp = ssh.open_sftp()

data = sftp.open(PATH, 'rb').read()
text = data.decode('utf-8')
orig_len = len(data)

if OLD_IMPORT not in text:
    print("ERROR: import line not found (as expected). Aborting.")
else:
    text = text.replace(OLD_IMPORT, NEW_IMPORT, 1)

n_block = text.count(OLD_BLOCK)
print(f"OLD_BLOCK occurrences in (post-import) text: {n_block}")
if n_block != 1:
    print("ERROR: entry block not found exactly once. Aborting WITHOUT write.")
    ssh.close()
    sys.exit(1)
text = text.replace(OLD_BLOCK, NEW_BLOCK, 1)

# backup
ts = time.strftime('%Y%m%d-%H%M%S')
bak = f"{PATH}.bak-{ts}"
sftp.rename(PATH, bak)
print(f"backup -> {bak}")

new_bytes = text.encode('utf-8')
sftp.open(PATH, 'wb').write(new_bytes)
print(f"wrote {PATH} ({len(new_bytes)} bytes vs {orig_len} orig, +{len(new_bytes)-orig_len})")

def run(cmd):
    i, o, e = ssh.exec_command(cmd, timeout=60)
    out = o.read().decode(errors='replace')
    err = e.read().decode(errors='replace')
    return out, err

print("\n== py_compile syntax check ==")
out, err = run("cd /opt/propr-bot && .venv/bin/python -m py_compile bot.py 2>&1; echo EXIT=$?")
print(out.strip() or "(empty)")
if err.strip():
    print("[stderr]", err.strip()[:500])

sftp.close()
ssh.close()
print("\nDone. Service NOT restarted yet - verify syntax result above before restarting.")
