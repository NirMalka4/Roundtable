One or more high-severity findings do not meet the evidence-depth floor.

A finding at CRITICAL/HIGH severity must prove itself — a reviewer has to be able to
verify the defect from what you provide.

CORRECTIVE ACTION (for every CRITICAL/HIGH finding):
- `exploitability` must be an object with:
    - `rating`     — one of Trivial, Easy, Moderate, Hard, Unknown (case-insensitive)
    - `reasoning`  — a non-empty string explaining the rating
- `trace` — a non-empty array of the ordered steps to trigger the defect
  (meeting the minimum step count required for this agent).
- `impact` — a non-empty string (required for ALL findings, any severity).

The ready-to-use `fix` is enforced separately by the `fix_present` gate.

Do not pad with filler; give the concrete, in-diff evidence you actually observed.
