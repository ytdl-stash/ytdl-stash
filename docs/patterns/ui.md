# UI Patterns (DaisyUI + Tailwind)

Reference for how this project uses DaisyUI components and Tailwind for consistent, accessible UI. The app uses **DaisyUI v5** and **Tailwind v4** via CDN (see `app/templates/base.html`).

---

## Buttons

Always set an explicit `type` on `<button>` elements:

- **`type="submit"`** — only for the button that submits a form (one per form).
- **`type="button"`** — for all other buttons: HTMX triggers, pagination, cancel, modal actions, etc.

Omitting `type` defaults to `submit` in HTML, which can cause accidental form submission if a button is inside (or later moved into) a form.

---

## Filter/Toggle button groups

For filter, sort, or pagination button groups (e.g. All / Watched / Not Watched on the Performers page), use:

- **DaisyUI `join`** — wrap buttons in `<div class="join">` for a cohesive grouped look
- **`join-item btn btn-sm`** — each button gets `join-item` so it visually connects to its neighbours
- **`btn-active`** — add for the currently selected/active option (DaisyUI semantic for pressed state)

**Reusable partial:** Use `app/templates/components/_filter_button.html` for link-based filter/toggle buttons. Pass: `url`, `label`, `is_active`, `hx_target`. Optional: `tooltip`, `tooltip_classes` (e.g. `tooltip tooltip-bottom`).

**HTMX and active state:** If the buttons trigger HTMX requests, the **swap target must include the filter/sort nav** so the response can re-render the buttons with the correct `is_active`. Otherwise the active state will be wrong after a click. Use a wrapper (e.g. `#performers-content`) that contains both the nav and the content, and return a partial that includes both from the server. See `performers/list.html` and `performers/_list_content.html`.

**Example:**

```html
<div class="join">
  {% with url='/performers?filter=all&sort=' ~ sort, label='All', is_active=(filter == 'all'), hx_target='#performers-content' %}
  {% include "components/_filter_button.html" with context %}
  {% endwith %}
  {% with url='/performers?filter=watched&sort=' ~ sort, label='Watched', is_active=(filter == 'watched'), hx_target='#performers-content', tooltip='Channels that are actively monitored', tooltip_classes='tooltip tooltip-bottom' %}
  {% include "components/_filter_button.html" with context %}
  {% endwith %}
</div>
```

The same pattern applies to pagination (see `videos/_video_list.html`): use `join` + `join-item` + `btn-active` for the current page.

---

## Reusable components

Shared partials and macros live in `app/templates/components/` and `app/templates/components/_macros.html`.

**Passing variables to includes:** Jinja2's `include` does not support `variable=value` syntax. Use `{% with %}` blocks to set variables before including:

```html
{% with url="/videos", label="Videos" %}
{% include "components/_back_link.html" with context %}
{% endwith %}
```

### Back link (`_back_link.html`)

Breadcrumb-style "← Back to X" navigation. Pass: `url`, `label`.

```html
{% with url="/videos", label="Videos" %}
{% include "components/_back_link.html" with context %}
{% endwith %}
```

### Loading button (`_loading_button.html`)

Disabled button with spinner for in-progress HTMX actions. Pass: `label`. Optional: `size` (`sm`|`xs`), default `sm`.

```html
{% with label="Stopping…" %}
{% include "components/_loading_button.html" with context %}
{% endwith %}
```

### Video thumbnail (`_video_thumbnail.html`)

Renders video preview from Stash screenshot, `thumbnail_url`, or placeholder. Pass: `video`, `settings`, `size` (`sm`|`md`|`lg`). Optional: `link_url`, `alt`, `placeholder_text`.

```html
{% with size='md', link_url='/videos/' ~ video.id %}
{% include "components/_video_thumbnail.html" with context %}
{% endwith %}
```

### Video actions (`_video_actions.html`)

Detail, Stop, Retry, Re-sync, Delete buttons for video rows/cards. Pass: `video`, `layout` (`table`|`detail`|`active`). For `table`/`detail`: `hx_status_target`, `hx_row_target`. For `detail`: `detail_page=true`. For `active`: `hx_container_target`.

```html
{% with layout='table', hx_status_target='#video-status-' ~ video.id, hx_row_target='#video-row-' ~ video.id %}
{% include "components/_video_actions.html" with context %}
{% endwith %}
```

### Collapse macro (`_macros.html`)

DaisyUI collapse with arrow. Import and use with `call`:

```html
{% from "components/_macros.html" import collapse %}
{% call collapse("Status legend") %}
  <div>...content...</div>
{% endcall %}
{% call collapse("Active downloads", open=true) %}...{% endcall %}
```

### Table header with tooltip (`th_tooltip` macro)

`<th>` with tooltip. Import and use:

```html
{% from "components/_macros.html" import th_tooltip %}
<tr>{% call th_tooltip("Base URL of your Stash instance") %}Stash URL{% endcall %}<td>...</td></tr>
```

### Status badge class filter

Template filter `status_badge_class` returns DaisyUI badge class for a video status. Registered in `app/main.py`.

```html
<span class="{{ video.status | status_badge_class }}">{{ video.status }}</span>
```

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
