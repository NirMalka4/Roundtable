One or more findings reference a file that is not part of this change.

CORRECTIVE ACTION:
- Every finding must anchor to a file in the CHANGED FILES for this review.
- Remove findings that are not grounded in the diff, or correct the `filePath` to the
  actual changed file (and line) the finding is about.
- Do not invent paths; cite the concrete in-diff code anchor you observed.
