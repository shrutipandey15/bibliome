"""Preview the DNF-reason insight sentences without a DB, a login, or the UI.

    python -m scripts.dnf_preview                # the three variants
    python -m scripts.dnf_preview bored bored lost_me   # your own reasons

Pure signal math (no I/O), so it renders exactly what the real payload would.
"""
import sys
from datetime import datetime, timedelta, timezone

from app.services import dna_signals as S
from app.services.dna_insights import build_dna

NOW = datetime.now(timezone.utc)


def _read(n=8):
    """Filler so the shelf clears the 5-tagged-book floor for DNA at all."""
    return [S.EntrySig(emotions=["comfort"], intensity=8,
                       ts=NOW - timedelta(days=i), status="finished")
            for i in range(n)]


def _dnf(reason):
    return S.EntrySig(emotions=["grief"], intensity=6, ts=NOW,
                      status="abandoned", dnf_reason=reason)


def show(label, reasons):
    res = build_dna(_read() + [_dnf(r) for r in reasons], insight_limit=99)
    line = next((i["text"] for i in res["insights"]
                 if i["category"] == "dnf_reason"), None)
    print(f"\n{label}\n  reasons: {', '.join(reasons)}")
    if line:
        print(f"  → {line}")
    else:
        locked = {l["category"]: l["reason"] for l in res.get("locked", [])}
        print(f"  → nothing yet — {locked.get('dnf_reason', 'gate not met')}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        show("yours", sys.argv[1:])
    else:
        show("unanimous", ["bored"] * 4)
        show("dominant", ["bored"] * 5 + ["too_much", "wrong_time"])
        show("spread", ["bored", "lost_me", "wrong_time", "drifted", "too_much"])
        show("below the gate", ["bored", "bored"])
