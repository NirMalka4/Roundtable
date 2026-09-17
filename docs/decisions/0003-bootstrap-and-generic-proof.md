# ADR 0003: Bootstrap scope and workflow-neutral proof

**Status:** Superseded by
[ADR 0004](0004-user-tool-bootstrap.md) (2026-09-14)

The bootstrap installer uses only the Python standard library, targets the
checkout-local `.venv`, and installs the current checkout. It does not alter
global Python, PATH, shell profiles, or package-feed settings.

Engine genericity is proven by a test-only synthetic graph with caller-supplied
sources, dependent schema-backed and schema-less LLM nodes, deterministic code,
and persisted generic artifacts. It is not a shipped application bundle.

**Rejected:** global installers, feed mutation, and a documentation-only claim
that the review engine is generic.
