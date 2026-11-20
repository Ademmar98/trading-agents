# -*- coding: utf-8 -*-
"""Read-only: print just the challenge phase parameters + any time limit fields."""
import os, sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from dotenv import load_dotenv
load_dotenv("/opt/propr-bot/.env", override=True)
sys.path.insert(0, "/opt/propr-bot")
from propr import ProprClient

c = ProprClient(api_key=os.getenv("PROPR_API_KEY"))
a = c.get_challenge_attempt("urn:prp-challenge-attempt:vXXQSywexgtc")

def walk(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            walk(v, f"{path}.{k}" if path else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            walk(v, f"{path}[{i}]")
    else:
        kl = path.lower()
        if any(s in kl for s in ("phase", "target", "loss", "drawdown",
                                 "deadline", "duration", "limit", "balance",
                                 "time", "days")):
            print(f"{path} = {obj}")

walk(a, "attempt")