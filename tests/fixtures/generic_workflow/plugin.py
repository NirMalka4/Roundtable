from __future__ import annotations

import json

from roundtable.plugins import register_enricher


def summarize(snapshot, _sources, _entry) -> str:
    accepted = json.loads(snapshot["Structured"].response)
    return json.dumps(
        {
            "accepted": accepted["accepted"],
            "raw": snapshot["Draft"].response,
        },
        sort_keys=True,
    )


register_enricher("generic_fixture_summary", summarize)
