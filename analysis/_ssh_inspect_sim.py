import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('69.48.202.100', username='root', password='CyVMhAYs8W3or', timeout=20)

def run(cmd, t=60):
    i, o, e = ssh.exec_command(cmd, timeout=t)
    return o.read().decode(errors='replace'), e.read().decode(errors='replace')

print("== sim.py: CLI entry points / main / argparse ==")
out, _ = run("grep -n 'def main\|argparse\|add_parser\|cmd_\|def cmd\|__main__\|def simulate\|def snapshot' /opt/propr-bot/sim.py 2>&1")
print(out.strip())

print("\n== sim.py: how data is fetched (hyperliquid / snapshot / BARS / lookback) ==")
out, _ = run("grep -n 'def snapshot\|def fetch\|hyperliquid\|api.hyperliquid\|import.*hl\|hours\|BARS\\|days\\|interval\\|resolution' /opt/propr-bot/sim.py 2>&1 | head -40")
print(out.strip())

print("\n== bot.py data fetch (universe + snapshot import) ==")
out, _ = run("grep -n 'def snapshot\|import sim\|sim.snapshot\|universe\\|BARS\\|COOLOFF\\|__main__' /opt/propr-bot/bot.py 2>&1 | head -30")
print(out.strip())

ssh.close()
