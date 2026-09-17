# Focus Management — Reviewer Rubric

**WCAG**: 2.4.3 Focus Order, 3.2.1 On Focus. **category**: `focus-management`. This is about WHERE focus goes (not whether the focus ring is visible — that is focus-indicators, out of scope).

Fire a finding when a changed `.tsx`/`.jsx` hunk matches a ❌ pattern below. Emit the ✅ block as `fix`. Quote the **Review note** in `description`.

## ❌ → ✅ patterns

### Focus lost after panel/dialog close (2.4.3)
❌ `onDismiss={() => setOpen(false)}` with no focus restoration.
✅ In the dismiss/`onDismissed` handler, return focus to the trigger inside `requestAnimationFrame(() => triggerRef.current?.focus())`, with a fallback chain (`getElementById` → inner `<button>` → overflow button).
Review note: without restoration, focus falls to `<body>`. Use `onDismissed` (fires after animation) over `onDismiss`, and `requestAnimationFrame` over `setTimeout(0)`.

### Navigation that changes view without moving focus (2.4.3, 3.2.1)
❌ `onClick={() => navigate({ viewid: 'activity' })}` only.
✅ After navigating, move focus to the destination (e.g. the selected tab), with a `MutationObserver` fallback if the target has not mounted yet.

### Phantom tab stop on a wrapper (2.4.3)
❌ `<StyledFocusContainer tabIndex={0}><ActionCard onClick={...}/></StyledFocusContainer>`
✅ Remove the wrapper `tabIndex`; let the interactive child be the only tab stop.
Review note: `tabIndex={0}` on a non-interactive `<div>`/`<span>` that wraps interactive content creates a meaningless tab stop.

### Focus after element removal / destructive action (2.4.3)
❌ Keeping an empty placeholder row in the data model just to hold focus, or reloading before closing a dialog.
✅ Close the dialog + reset state first, then after the state update move focus to the next logical target (sibling, "Add"/"Create" button) via a saved `ref` + `requestAnimationFrame`.

## Do NOT flag
- A `tabIndex={0}` added to a genuinely interactive custom element (editor, gridcell) — that is correct.
- Focus restoration that already exists in unchanged orchestration code outside the hunk.
