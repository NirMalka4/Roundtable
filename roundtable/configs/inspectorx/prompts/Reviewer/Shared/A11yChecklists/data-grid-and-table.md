# Data Grid and Table — Reviewer Rubric

**WCAG**: 1.3.1 Info and Relationships, 2.1.1 Keyboard. **category**: `data-grid-and-table`.

Fire a finding when a changed `.tsx`/`.jsx` hunk matches a ❌ pattern below. Emit the ✅ block as `fix`. Quote the **Review note** in `description`.

## ❌ → ✅ patterns

### Custom grid with no keyboard navigation (2.1.1)
❌ A virtualized grid that renders cells with no `tabIndex`, no arrow-key handling, no focus tracking.
✅ Track a `focusedCell {row,col}`; give each cell `role="gridcell"`, roving `tabIndex` (`0` for the focused cell, `-1` otherwise), and an `onKeyDown` handling Arrow/Home/End to move focus.
Review note: a custom (non-native) grid must implement roving-tabindex arrow-key navigation; native `<table>` does not.

### Missing column-header semantics (1.3.1)
❌ Header cells rendered as plain `<div>`/`<span>`.
✅ Use `<th>` (or `role="columnheader"`) so the header relationship is announced.

### Column resize handle not keyboard-operable (2.1.1)
❌ Resize works only via mouse drag.
✅ Enable the keyboard-accessible resize path (e.g. `enableKeyboardAccessibleColumnResize: true`) and constrain width with a `maxWidth`.

### Action cell without keyboard support / label (2.1.1, 4.1.2)
❌ Inline action icons with no `aria-label` and no keyboard menu.
✅ Render an `<IconButton ariaLabel={...}>` that opens a `ContextualMenu` (keyboard-navigable) anchored to a `ref`.

## Do NOT flag
- A native `<table>`/`<th>` (or Fluent `DetailsList`/`ListPage`) — it provides header semantics and keyboard navigation natively.
- Focus-return-to-table after a row dialog closes — that is `focus-management`.
