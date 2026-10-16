import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import date

from contracts.data import Bar, CorporateAction, Snapshot


def build_snapshot(
    bars: Sequence[Bar],
    *,
    actions: Sequence[CorporateAction] = (),
    memberships: Mapping[tuple[str, date], frozenset[str]] | None = None,
) -> Snapshot:
    """Content-hash the raw inputs. `survivorship_biased` is always True: free data has no
    prices for delisted names, so this layer can never certify an unbiased universe."""
    if not bars:
        raise ValueError("cannot snapshot an empty dataset")
    ordered = sorted(bars, key=lambda b: (b.ts, b.symbol))
    payload = {
        "bars": [b.model_dump(mode="json") for b in ordered],
        "actions": sorted(
            (a.model_dump(mode="json") for a in actions),
            key=lambda d: json.dumps(d, sort_keys=True),
        ),
        "memberships": sorted(
            (index, day.isoformat(), sorted(members))
            for (index, day), members in (memberships or {}).items()
        ),
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return Snapshot(
        id=f"sha256:{digest[:32]}",
        survivorship_biased=True,
        start=ordered[0].ts,
        end=ordered[-1].ts,
    )
