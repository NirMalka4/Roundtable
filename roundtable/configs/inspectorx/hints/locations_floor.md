One or more findings are missing a structurally-valid `locations[]` array.

CORRECTIVE ACTION:
- Every finding must carry a `locations[]` array with at least one entry.
- Each entry must be an object with:
    - `filePath`   — a non-empty string (the changed file the finding is about)
    - `startLine`  — an integer ≥ 1
    - `endLine`    — an integer ≥ 1, and ≥ `startLine`
- Do NOT emit the legacy `location` string field — use structured `locations[]`.
- Point at the concrete in-diff code anchor you observed; do not invent lines.
