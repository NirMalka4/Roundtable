One or more findings claim `measured` evidence but this run recorded no successful
test/coverage execution for you.

`measured` means you actually RAN the relevant existing tests and/or a coverage
report and are citing the tool's real output. Reasoning from reading the code — or a
drafted, unrun test — is `inferred`, not `measured`.

This is an ADVISORY downgrade (not a rejection): treat every affected finding as
`inferred`.

CORRECTIVE ACTION:
- If you can run the check, run the smallest existing command that covers the
  behavior (e.g. the targeted tests or a scoped coverage report) and cite its actual
  output — then keep `evidence: measured`.
- If you cannot run it (no runner, missing deps, unclear command), set
  `evidence: inferred` and lower the affected findings' severity toward Low, saying why.
- Never set `evidence: measured` for a drafted/unrun test.
