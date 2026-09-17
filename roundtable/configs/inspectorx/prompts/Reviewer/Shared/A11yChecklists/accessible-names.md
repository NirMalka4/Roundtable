# Accessible Names — Reviewer Rubric

**WCAG**: 4.1.2 Name, Role, Value, 1.1.1 Non-text Content. **category**: `accessible-names`.

Fire a finding when a changed `.tsx`/`.jsx` hunk matches a ❌ pattern below. Emit the ✅ block as `fix`. Quote the **Review note** in `description`. This is the most common a11y defect class.

## ❌ → ✅ patterns

### Icon-only button missing an accessible name (4.1.2, 1.1.1)
❌ `<IconButton iconProps={{ iconName: "edit" }} />`
✅ `<IconButton aria-label={strings.Edit_AriaLabel} iconProps={{ iconName: "edit" }} />`
Review note: an icon-only control announces only "button". Provide `aria-label` (prefer the typed `ariaLabel` prop on Fluent components) using a localized string — never a hardcoded English literal.

### Form control with no programmatic label (4.1.2)
❌ `<StyledLabel>{'Comment:'}</StyledLabel><TextField .../>` (visible label not associated); or `<ChoiceGroup .../>` / `<Dropdown .../>` with no label.
✅ Associate via `aria-labelledby={id}` (label gets matching `id`) or the component's `label` prop.
Review note: a nearby visible label is not programmatically associated unless wired via `label`/`aria-labelledby`.

### Chart/visualization container (1.1.1)
❌ `<div role="figure" aria-label={...}><LineChart/></div>`
✅ `<div role="region" aria-label={...}><LineChart/></div>`

### Non-descriptive link text (4.1.2)
❌ `<Link href={...}>{vtValue}</Link>` rendering bare text like "0/71".
✅ Add `aria-label={`${vtKey} ${vtValue}`}` so the link carries context.

## CRITICAL: ARIA First Rule
NEVER replace a semantic interactive element with a non-interactive element + ARIA role.
❌ `<Icon role="button" tabIndex={0} aria-label="More info" onKeyDown={...} onClick={...} />`
✅ `<IconButton iconProps={{ iconName: "Info" }} aria-label={strings.MoreInfo_AriaLabel} onClick={...} />`
Review note: if it already IS a button/link/input, keep it and fix its name — don't downgrade it. Decorative icons next to interactive elements get `aria-hidden="true"`.

## Do NOT flag
- An accessible name supplied outside the hunk (parent `aria-labelledby`, wrapping `<label>`, Tooltip on the interactive ancestor).
- Adding redundant `aria-expanded`/`aria-controls`/`role` to a Fluent component that manages them (that is itself a defect to flag, not the absence).
