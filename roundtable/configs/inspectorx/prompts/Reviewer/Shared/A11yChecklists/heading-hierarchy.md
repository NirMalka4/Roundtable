# Heading Hierarchy — Reviewer Rubric

**WCAG**: 1.3.1 Info and Relationships, 2.4.6 Headings and Labels. **category**: `heading-hierarchy`.

Fire a finding when a changed `.tsx`/`.jsx` hunk matches a ❌ pattern below. Emit the ✅ block as `fix`. Quote the **Review note** in `description`.

## ❌ → ✅ patterns

### Styled `<div>` used as a heading (1.3.1)
❌ `export const HeroTitle = styled.div\`font-size: 32px; font-weight: 600;\`;` (visually a heading, not semantic).
✅ `export const HeroTitle = styled.h2\`font-size: 32px; font-weight: 600; margin: 0;\`;`
Review note: visually prominent text is not a heading to a screen reader unless it is an `<h1>`–`<h6>` (or `as="hN"`). Add `margin: 0` when converting from `styled.div`.

### Skipped or wrong heading level (1.3.1, 2.4.6)
❌ `<h1>...</h1>` then `<h3>...</h3>` (skips h2); or a side panel/flyout header using `<h1>`.
✅ Sequential levels with no gaps. Page title = h1; side-panel/flyout/dialog header = h2; sub-section within a panel = h3.
Review note: panels open inside a page that already owns h1, so their header is h2. Match the level used by sibling panels.

### Fluent title rendering a non-heading (1.3.1)
❌ `<Title2 role="heading">{header}</Title2>` (renders a `<span>`); or `role="heading"` with no `aria-level`.
✅ `<Title2 as="h2">{header}</Title2>` — prefer a semantic element/`as` prop over `role="heading"`.

## Do NOT flag
- A heading that is really a form-field label — that belongs to `accessible-names` (`label`/`aria-labelledby`), not a heading element.
- Heading levels that are correct relative to an ancestor heading defined outside the hunk.
