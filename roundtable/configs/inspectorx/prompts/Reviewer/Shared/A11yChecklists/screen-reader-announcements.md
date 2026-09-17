# Screen Reader Announcements — Reviewer Rubric

**WCAG**: 4.1.3 Status Messages, 1.3.1 Info and Relationships. **category**: `screen-reader-announcements`. This is about announcing dynamic changes (live regions, `aria-hidden`, announcement timing) — not static labels (accessible-names) or structural roles (semantic-roles).

Fire a finding when a changed `.tsx`/`.jsx` hunk matches a ❌ pattern below. Emit the ✅ block as `fix`. Quote the **Review note** in `description`.

## ❌ → ✅ patterns

### Dynamic update with no live region (4.1.3)
❌ A loading count / search-results count / "Generation cancelled" status written to the DOM with no `aria-live` container or `role="status"`/`role="alert"`.
✅ Render the message inside `role="status"` (polite) or `role="alert"` (urgent), or a visually-hidden `aria-live="polite"` + `aria-atomic="true"` region; set both `.textContent` and `aria-label` for reliable re-announcement.
Review note: status changes that are not in a live region are silent to screen readers.

### Duplicate / competing announcements (4.1.3)
❌ `<MessageBar role="alert"><Spinner aria-label={loadingText} /></MessageBar>` — both the child and the bar announce.
✅ Move `aria-label` to the `MessageBar` and set `aria-hidden` on the child `<Spinner>`.
Review note: keep one announcement source; suppress redundant child labels with `aria-hidden`.

### Decorative element announced (1.3.1)
❌ `<Divider />` / `<hr>` rendered purely for visual separation.
✅ `<Divider aria-hidden={true} role="presentation" />` (and `<hr aria-hidden="true" role="presentation">`).

### Visual-only value read aloud unclearly (4.1.3)
❌ Chart point / time shown as `"46 H"` or a y-axis value with no spoken context.
✅ Provide a readable `ariaLabel` (e.g. "46 hours", legend + value + time range) on the chart callout data.

## Do NOT flag
- A live region / `aria-hidden` already provided on an ancestor or sibling outside the hunk.
- Static labels with no dynamic update — those are `accessible-names`.
