# Keyboard Interaction — Reviewer Rubric

**WCAG**: 2.1.1 Keyboard, 2.1.2 No Keyboard Trap, 1.4.13 Content on Hover/Focus. **category**: `keyboard-interaction`.

Fire a finding when a changed `.tsx`/`.jsx` hunk matches a ❌ pattern below. Emit the ✅ block (adapted to the surrounding code) as `fix`. Quote the **Review note** in `description`.

## ❌ → ✅ patterns

### Custom element with `onClick` but no keyboard handler (2.1.1)
❌ `<div onClick={() => onSelect()}>{label}</div>`
✅ `<div onClick={() => onSelect()} tabIndex={0} role="button" onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(); } }}>{label}</div>`
Review note: a non-native interactive element needs `tabIndex={0}`, a `role`, and an `onKeyDown` for Enter/Space. Use `onKeyDown` (not deprecated `onKeyPress`).

### Hover-only tooltip (1.4.13)
❌ `<TooltipHost content={tip}><div className={styles.pill}>{text}</div></TooltipHost>`
✅ Wrap the trigger in a native `<button>`/`<IconButton>` and open a `Callout` on `onClick` + Enter/Space, dismiss on Escape; keep `onMouseEnter`/`onMouseLeave` for parity.
Review note: content shown only on hover is unreachable by keyboard. Prefer `Callout` (or `IconButton`, which is natively keyboard-accessible) over hover-only `TooltipHost`.

### Child key event bubbles to parent action (2.1.1)
❌ `onKeyPress={(e) => onKeyPress(e)}` on a card whose children are also interactive.
✅ `onKeyDown={(e) => { if (e.target !== e.currentTarget) return; onKeyPress(e); }}` — and add `e.stopPropagation()` in nested arrow-key handlers (tree/grid).
Review note: guard with `e.target !== e.currentTarget` / `stopPropagation()` so a child's key event does not fire the parent.

## Voice Access
A row/element with `onClick` but no interactive role is not targetable by Voice Access. Do not make a whole row `role="button"`; add a native `<Button>`/`<Link>` in a cell, or wrap a non-interactive element in `<Button appearance="transparent">`.

## Do NOT flag
- Native interactive elements (`<button>`, `<a>`, `<input>`, `<select>`, `<textarea>`, Fluent `<Button>`, `<IconButton>`, `<Link>`) — they handle keyboard natively.
- Fluent `Dropdown`/`ComboBox` for Voice Access — natively compatible.
- A missing handler when the element is inside an interactive ancestor not shown in the hunk (see the false-positive guard in the agent prompt).
