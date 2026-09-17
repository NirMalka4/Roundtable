## Change Under Review

Synthetic saved Judge context for evaluation.

## Git Context
```diff
diff --git a/calculator.py b/calculator.py
new file mode 100644
--- /dev/null
+++ b/calculator.py
@@ -0,0 +1,2 @@
+def add(left, right):
+    return left + right
```

---
## agent_dossier [REQUIRED]
This is the finding index for the change under review.

### C1 — `calculator.py`
- `taintcheck::TC-01` · high · Addition returns subtraction

---
## Reviewer Claims [REQUIRED]

### Taint Check [AVAILABLE]

```json
{
  "intent": "The change adds an integer addition helper.",
  "attack_surface": [],
  "findings": [
    {
      "id": "TC-01",
      "title": "Addition returns subtraction",
      "class": "CORRECTNESS",
      "severity": "high",
      "defect": "The helper subtracts right from left.",
      "surface": "calculator.add",
      "reachability": "Every caller reaches it.",
      "anchors": [
        {"filePath": "calculator.py", "startLine": 2, "endLine": 2, "role": "sink"}
      ],
      "chain": ["calculator.add", "subtraction"]
    }
  ],
  "abstentions": []
}
```
