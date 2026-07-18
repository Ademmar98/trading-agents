"""Apply the unleashed test profile to the VPS .env (with backup) and restart."""
import paramiko

KEYS = [
    "SCOUT_MODE_ENABLED", "SCOUT_MAX_DEPLOY_PCT", "SCOUT_RISK_PER_TRADE_PCT",
    "SMA200_DEPLOY_TARGET", "SMA200_UNKNOWN_TARGET", "RISK_PER_TRADE_PCT",
    "SWING_RISK_PER_TRADE_PCT", "MAX_POSITION_SIZE_PCT", "MAX_PORTFOLIO_RISK_PCT",
    "MAX_PEAK_DRAWDOWN_PCT", "STREAK_LOSS_HALT_PCT", "DAILY_LOSS_LIMIT_PCT",
    "MAX_DAILY_LOSS_USD", "MAX_WEEKLY_LOSS_PCT", "MAX_TRADES_PER_DAY",
    "MAX_TRADES_PER_HOUR", "MAX_OPEN_RISK_PCT", "MAX_POSITIONS_PER_CLUSTER",
    "MAX_GROUP_POSITIONS", "MAX_PAIR_CORRELATION", "PER_STRATEGY_MAX_OPEN",
    "MACRO_DIP_PCT", "SESSION_RISK_MULTS", "SCALP_MIN_WIN_PROB",
]

BLOCK = """
# ---- UNLEASHED TEST PROFILE (2026-07-18) — all risk brakes off for the judging week ----
SCOUT_MODE_ENABLED=true
SCOUT_MAX_DEPLOY_PCT=100
SCOUT_RISK_PER_TRADE_PCT=2.0
SMA200_DEPLOY_TARGET=1.0
SMA200_UNKNOWN_TARGET=1.0
RISK_PER_TRADE_PCT=2.0
SWING_RISK_PER_TRADE_PCT=2.0
MAX_POSITION_SIZE_PCT=100
MAX_PORTFOLIO_RISK_PCT=100
MAX_PEAK_DRAWDOWN_PCT=100
STREAK_LOSS_HALT_PCT=100
DAILY_LOSS_LIMIT_PCT=100
MAX_DAILY_LOSS_USD=10000000
MAX_WEEKLY_LOSS_PCT=100
MAX_TRADES_PER_DAY=0
MAX_TRADES_PER_HOUR=0
MAX_OPEN_RISK_PCT=0
MAX_POSITIONS_PER_CLUSTER=0
MAX_GROUP_POSITIONS=0
MAX_PAIR_CORRELATION=0
PER_STRATEGY_MAX_OPEN=999
MACRO_DIP_PCT=100
SESSION_RISK_MULTS=1.0,1.0,1.0,1.0
SCALP_MIN_WIN_PROB=0
"""

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('69.48.202.100', username='root', password='CyVMhAYs8W3or', timeout=15)

def run(cmd, t=60):
    _, o, e = ssh.exec_command(cmd, timeout=t)
    return (o.read().decode(errors='replace') + e.read().decode(errors='replace')).strip()

print(run('cp /root/firm/.env /root/firm/.env.pre_unleashed && echo backup-ok'))

# strip old occurrences of these keys, then append the block
py = (
    "python3 - <<'EOF'\n"
    "keys = %r\n" % (tuple(KEYS),) +
    "p = '/root/firm/.env'\n"
    "lines = open(p).read().splitlines()\n"
    "keep = [l for l in lines if not any(l.strip().startswith(k + '=') for k in keys)]\n"
    "block = open('/root/firm/.env.unleashed_block').read()\n"
    "open(p, 'w').write('\\n'.join(keep).rstrip() + '\\n' + block)\n"
    "print('env rewritten, lines:', len(keep))\n"
    "EOF"
)
sftp = ssh.open_sftp()
with sftp.file('/root/firm/.env.unleashed_block', 'w') as f:
    f.write(BLOCK)
print(run(py))
print(run("grep -c '=' /root/firm/.env; grep -E 'SCOUT_MAX|RISK_PER_TRADE|MAX_TRADES_PER_DAY|SCALP_MIN_WIN' /root/firm/.env"))

print(run('systemctl restart trading-firm && sleep 4 && systemctl is-active trading-firm'))
ssh.close()
