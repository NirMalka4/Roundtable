# Semantic Roles and Landmarks — Reviewer Rubric

**WCAG**: 1.3.1 Info and Relationships, 4.1.2 Name, Role, Value. **category**: `semantic-roles-and-landmarks`.

Fire a finding when a changed `.tsx`/`.jsx` hunk matches a ❌ pattern below. Emit the ✅ block as `fix`. Quote the **Review note** in `description`.

## ❌ → ✅ patterns

### Selection card using the wrong role (1.3.1, 4.1.2)
❌ `<div role="button" tabIndex={0} onClick={...}>{authType}</div>` used as a single-choice selector.
✅ Wrap options in `role="radiogroup"` + `aria-label`; each option `role="radio"` + `aria-checked={isSelected}` + `aria-label`.
Review note: a card that behaves as a radio must announce "radio button, checked/not checked", not "button".

### Dynamic feedback missing a live role (4.1.2, 1.3.1)
❌ `<div>{strings.SuccessMessage}</div>` rendered conditionally on success/error.
✅ Add `role="status"` (polite) for non-urgent updates, `role="alert"` (assertive) for errors.
Review note: dynamically appearing status text is not announced without `role="status"`/`role="alert"`.

### `role="main"` on non-primary content (1.3.1)
❌ `<NodeMap role="main" aria-label={...} />`
✅ `<NodeMap role="region" aria-label={...} />`
Review note: only one `role="main"` per page; secondary landmarks use `role="region"` paired with `aria-label`.

### Chart/region wrapper (4.1.2)
❌ `<div role="figure" aria-label="...">` around a chart — causes a redundant "graphic" announcement.
✅ `<div role="region" aria-label="...">`. A `role="region"` without an accessible name is useless — always pair with `aria-label`.

### Toolbar grouping (1.3.1)
❌ `<div id={toolbarID}>{formatButtons}</div>`
✅ `<div id={toolbarID} role="toolbar">{formatButtons}</div>`

## Do NOT flag
- Redundant ARIA on Fluent `Dropdown`/`ComboBox`/`Dialog` (they manage their own `role`/`aria-expanded`/`aria-controls`). Flagging the *addition* of such redundant ARIA is correct; flagging its absence is not.
- A landmark/role supplied on an ancestor outside the hunk.
