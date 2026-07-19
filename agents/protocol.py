"""The desk protocol: how agents argue, negotiate, and reach a verdict.

A trade travels through a *deliberation*:

  1. analyst publishes TOPIC_PROPOSAL (its own tick, its own conviction)
  2. the chair (orchestrator) fans out TOPIC_REVIEW_REQ to every reviewer
     concurrently; each answers with a stance:
        approve  — take the trade as proposed
        counter  — take it, but only with my adjustments (size/SL/TP/confidence)
        reject   — don't take it (votes against, can be outvoted)
        veto     — trade is dead, no vote can revive it (risk/compliance/health only)
  3. objections go back to the analyst, which may CONCEDE (accept the
     counters), DEFEND (push back with evidence, staking its own
     credibility), or WITHDRAW
  4. bounded rounds, then a weighted vote decides; reviewer weights are
     earned — the auditor moves them after seeing real trade outcomes.

Everything here is pure data + pure functions so the argument logic is
unit-testable without an event loop.
"""

# ── topics ──
TOPIC_CONTEXT = "market.context"            # regime/sentiment broadcast scans
TOPIC_PROPOSAL = "trade.proposal"           # analyst -> desk
TOPIC_REVIEW_REQ = "trade.review.request"   # chair -> each reviewer (request/reply)
TOPIC_REVISION_REQ = "trade.revision"       # chair -> analyst (request/reply)
TOPIC_VERDICT = "trade.verdict"             # chair broadcast: approved/rejected + why
TOPIC_EXECUTE = "trade.execute"             # chair -> trader (request/reply)
TOPIC_EXECUTED = "trade.executed"           # trader broadcast: fill result
TOPIC_HALT = "system.halt"                  # health/compliance broadcast halt state
TOPIC_TUNING = "desk.tuning"                # optimizer broadcast parameter changes

# Only these agents can kill a trade unilaterally. Everyone else argues.
VETO_POWERS = ("compliance", "risk_manager", "health")

STANCE_APPROVE = "approve"
STANCE_COUNTER = "counter"
STANCE_REJECT = "reject"
STANCE_VETO = "veto"
STANCE_ABSTAIN = "abstain"   # no opinion (reviewer crashed or has no read)

# Vote contribution per stance (multiplied by earned reviewer weight).
_STANCE_SCORE = {
    STANCE_APPROVE: 1.0,
    STANCE_COUNTER: 0.5,   # supports the trade, but conditionally
    STANCE_REJECT: -1.0,
    STANCE_VETO: -10.0,    # unreachable by design; belt and braces
    STANCE_ABSTAIN: 0.0,
}


def make_review(reviewer, stance, reasons, qty_mult=1.0, confidence_delta=0.0,
                sl=None, tp=None):
    """A reviewer's argument: stance + the adjustments that would win it over."""
    return {
        "reviewer": reviewer,
        "stance": stance,
        "reasons": list(reasons)[:6],
        "qty_mult": round(float(qty_mult), 6),
        "confidence_delta": round(float(confidence_delta), 4),
        "sl": sl,
        "tp": tp,
    }


def apply_counters(proposal, reviews):
    """Fold every counter's adjustments into a revised proposal.

    Size cuts multiply (two independent 0.5 cuts = 0.25 — both concerns are
    real), confidence deltas add (approvals may also nudge confidence, e.g.
    regime alignment), and SL/TP overrides go to the most risk-averse
    bidder: the tightest stop wins, the nearest target wins.

    A reviewer that counters in a *later* round is judging the already
    revised proposal, so a repeated multiplier means "still too big", not a
    double count.
    """
    revised = dict(proposal)
    qty = float(proposal.get("qty", 0))
    confidence = float(proposal.get("confidence", 0))
    side = proposal.get("action", "BUY")
    sl, tp = proposal.get("sl"), proposal.get("tp")

    for r in reviews:
        if r["stance"] in (STANCE_COUNTER, STANCE_APPROVE):
            confidence += r.get("confidence_delta", 0.0)
        if r["stance"] != STANCE_COUNTER:
            continue
        qty *= max(0.0, r.get("qty_mult", 1.0))
        if r.get("sl") is not None:
            if sl is None:
                sl = r["sl"]
            elif side == "BUY":
                sl = max(sl, r["sl"])   # tighter stop for a long = higher SL
            else:
                sl = min(sl, r["sl"])
        if r.get("tp") is not None:
            if tp is None:
                tp = r["tp"]
            elif side == "BUY":
                tp = min(tp, r["tp"])   # nearer target = lower TP for a long
            else:
                tp = max(tp, r["tp"])

    revised["qty"] = round(qty, 8)
    revised["confidence"] = round(max(0.0, min(confidence, 0.95)), 4)
    if sl is not None:
        revised["sl"] = sl
    if tp is not None:
        revised["tp"] = tp
    return revised


def tally_votes(reviews, weights=None):
    """Weighted vote across stances. Weights are the auditor's earned trust
    scores; an unknown reviewer votes at weight 1.0."""
    weights = weights or {}
    score = 0.0
    tally = {}
    for r in reviews:
        w = float(weights.get(r["reviewer"], 1.0))
        s = _STANCE_SCORE.get(r["stance"], 0.0) * w
        tally[r["reviewer"]] = {"stance": r["stance"], "weight": round(w, 3),
                                "score": round(s, 3)}
        score += s
    return round(score, 3), tally


def merge_reviews(proposal, reviews, weights=None, min_confidence=0.55,
                  rounds_left=0):
    """Chair's decision function for one round of argument.

    Returns a verdict dict:
      decision   'approved' | 'rejected' | 'revise'
      proposal   the (possibly counter-adjusted) proposal
      vetoes / objections / tally / score   the reasoning trail
    'revise' means: no veto, but there are objections the analyst deserves
    a chance to answer — only offered while rounds_left > 0.
    """
    # A veto claimed by an agent without veto power is demoted to a reject:
    # strong opinion, no kill switch.
    reviews = [
        {**r, "stance": STANCE_REJECT}
        if r["stance"] == STANCE_VETO and r["reviewer"] not in VETO_POWERS
        else r
        for r in reviews if r
    ]
    vetoes = [r for r in reviews if r["stance"] == STANCE_VETO]

    objections = [r for r in reviews
                  if r["stance"] in (STANCE_REJECT, STANCE_COUNTER)]
    revised = apply_counters(proposal, reviews)
    score, tally = tally_votes(reviews, weights)

    if vetoes:
        return {"decision": "rejected", "proposal": revised, "vetoes": vetoes,
                "objections": objections, "score": score, "tally": tally,
                "reasons": [x for v in vetoes for x in v["reasons"]][:6]}

    if objections and rounds_left > 0:
        return {"decision": "revise", "proposal": revised, "vetoes": [],
                "objections": objections, "score": score, "tally": tally,
                "reasons": [x for o in objections for x in o["reasons"]][:6]}

    dead_size = revised.get("qty", 0) <= 0
    weak = revised.get("confidence", 0) < min_confidence
    if score > 0 and not dead_size and not weak:
        return {"decision": "approved", "proposal": revised, "vetoes": [],
                "objections": objections, "score": score, "tally": tally,
                "reasons": [f"vote passed at {score:+.2f}"]}

    why = []
    if score <= 0:
        why.append(f"vote failed at {score:+.2f}")
    if dead_size:
        why.append("counters reduced size to zero")
    if weak:
        why.append(f"confidence {revised.get('confidence', 0):.2f} below "
                   f"{min_confidence:.2f} after argument")
    return {"decision": "rejected", "proposal": revised, "vetoes": [],
            "objections": objections, "score": score, "tally": tally,
            "reasons": why}


# ── analyst's side of the argument ──
DEFENSE_CONCEDE = "concede"    # accept the counters, proceed with revision
DEFENSE_DEFEND = "defend"      # push back: evidence raises confidence
DEFENSE_WITHDRAW = "withdraw"  # objections convinced the analyst


def make_defense(action, reasons, confidence_boost=0.0, proposal=None):
    return {
        "action": action,
        "reasons": list(reasons)[:6],
        "confidence_boost": round(float(confidence_boost), 4),
        "proposal": proposal,
    }
