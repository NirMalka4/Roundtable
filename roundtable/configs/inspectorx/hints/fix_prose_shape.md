One or more findings put a Markdown code fence (```) *inside* a prose `fix` string.

The `fix` field is a UNION with exactly two well-formed shapes — a fenced code block
smuggled into prose is neither, and it renders as a broken block in the published PR
comment (the comment renderer neutralizes fences to prevent layout breaks and watermark
forgery, so any ``` inside a prose fix is defanged).

CORRECTIVE ACTION:
- If the fix is a concrete, ready-to-apply code replacement, emit the STRUCTURED form —
  a `{ "language": "<lang>", "code": "<exact replacement>" }` suggestion object. Put the
  code in `code`; do NOT wrap it in ``` fences and do NOT prefix it with `suggestion`.
- If the fix is a structural / multi-file instruction, keep it as PLAIN prose that names
  the symbol / file / exact change — with no ``` fenced block embedded.

REJECTED (flagged by this advisory gate):
- A prose `fix` string containing ```` ```lang ... ``` ```` or ```` ```suggestion ... ``` ```` —
  move that code into a `{language, code}` suggestion object instead.

This gate is advisory (non-blocking): the comment still renders safely, but the embedded
block will look broken, so prefer the structured suggestion.
