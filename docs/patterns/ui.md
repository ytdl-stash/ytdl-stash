# UI Patterns (DaisyUI + Tailwind)

Reference for how this project uses DaisyUI components and Tailwind for consistent, accessible UI. The app uses **DaisyUI v5** and **Tailwind v4** via CDN (see `app/templates/base.html`).

---

## Buttons

Always set an explicit `type` on `<button>` elements:

- **`type="submit"`** — only for the button that submits a form (one per form).
- **`type="button"`** — for all other buttons: HTMX triggers, pagination, cancel, modal actions, etc.

Omitting `type` defaults to `submit` in HTML, which can cause accidental form submission if a button is inside (or later moved into) a form.

---

## Tooltips

We use **DaisyUI’s tooltip component** for inline help on buttons, labels, table headers, and indicators. No JavaScript is required: add `class="tooltip"` and `data-tip="Help text"` to the element.

**Positioning:** Use a position class so the tooltip doesn’t go off-screen:

- `tooltip-bottom` — for elements near the top of the page (e.g. stat titles, nav-area buttons, form labels)
- `tooltip-top` — for elements near the bottom of cards or rows (e.g. action buttons in table rows)

**Examples:**

```html
<!-- Button -->
<button class="btn btn-sm btn-primary tooltip tooltip-bottom" data-tip="Scan all enabled channels for new videos">
  Check All Now
</button>

<!-- Table header -->
<th class="tooltip tooltip-bottom" data-tip="Hours between automatic channel scans">Interval</th>

<!-- Form label -->
<span class="label-text tooltip tooltip-bottom" data-tip="Paste the full channel/user page URL">URL</span>
```

Use tooltips consistently for:

- Action buttons (what the action does)
- Column headers (what the column means)
- Form labels (what to enter or what the setting does)
- Status/indicator elements (what the indicator means)

Do **not** use the native HTML `title` attribute for help text; use DaisyUI tooltips so styling and behavior are consistent.

---

## Performer card indicators

Performer cards use distinct classes for Stash link status (see `app/static/style.css`):

- `.stash-performer-linked` / `.stash-performer-unlinked` — Performer sync (show "✓ P" / "P —")
- `.stash-studio-linked` / `.stash-studio-unlinked` — Studio sync (show "✓ S" / "S —")

The generic `.stash-linked` / `.stash-unlinked` remain for single-indicator contexts (e.g. performer detail page).
