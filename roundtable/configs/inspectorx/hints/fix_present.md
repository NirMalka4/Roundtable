One or more real defects (severity >= low) do not carry a usable `fix`.

Every finding that is an actual defect owes the developer ONE ready-to-use resolution —
not a restatement of the problem, not generic advice. `info`-severity observations are
exempt.

CORRECTIVE ACTION (for every finding at severity low/medium/high/critical):
- Add a `fix`. It is a UNION — pick the strongest form the change allows:
    - **One-click suggestion** (preferred when the change is localized to the anchored
      span): a `{ "language": "<lang>", "code": "<exact replacement>" }` object the
      developer can apply verbatim.
    - **Precise prose** (when the change is structural or spans multiple files): a
      specific instruction that names the symbol / file / exact change to make.
- The fix MUST resolve the grounded failure. Derive it from the same evidence that
  proves the finding (the `trace` / `impact` / anchored `locations`), so applying the
  fix would make the reproduction stop failing.

REJECTED (these fail the gate):
- Missing `fix`, an empty string, or a suggestion object missing `language`/`code`.

WRITE A CONCRETE FIX (quality — not gate-enforced, but expected):
- State the actual change; don't restate the goal. Boilerplate like "fix the bug",
  "handle this properly", "address the issue", or "refactor as needed" is a non-answer —
  name the exact edit. (Vague descriptions are separately flagged advisory by
  `generic_phrase`, and a weak fix invites reviewer/Judge pushback.)
