# HTMX Patterns

Reference patterns for how this project uses HTMX for interactive UI elements. Read this before modifying templates or adding new interactive features.

---

## HTMX Setup

HTMX is loaded via CDN in the base template:

```html
<!-- app/templates/base.html -->
<head>
    <script src="https://unpkg.com/htmx.org@2.0.4"></script>
</head>
```

No build step required. HTMX is a single JS file.

---

## Core Concepts

HTMX works by adding attributes to HTML elements that trigger HTTP requests and swap content:

| Attribute | Purpose |
|-----------|---------|
| `hx-get` | Make a GET request to the URL |
| `hx-post` | Make a POST request to the URL |
| `hx-put` | Make a PUT request to the URL |
| `hx-delete` | Make a DELETE request to the URL |
| `hx-target` | CSS selector for element to update with response |
| `hx-swap` | How to swap content: `innerHTML`, `outerHTML`, `beforeend`, etc. |
| `hx-trigger` | Event that triggers the request (default: natural event for element type) |
| `hx-indicator` | Element to show during request (loading spinner) |
| `hx-confirm` | Show a confirm dialog before the request |

---

## Pattern: Toggle Switch

Channel enable/disable toggle:

```html
<!-- In channels/list.html -->
<button hx-put="/channels/{{ channel.id }}"
        hx-vals='{"enabled": {{ "false" if channel.enabled else "true" }}}'
        hx-target="#channel-row-{{ channel.id }}"
        hx-swap="outerHTML"
        class="{{ 'enabled' if channel.enabled else 'disabled' }}">
    {{ "Enabled" if channel.enabled else "Disabled" }}
</button>
```

The server returns the updated row HTML, which replaces the old one.

---

## Pattern: Action Button with Loading State

"Check Now" button that shows a spinner while scanning:

```html
<button hx-post="/channels/{{ channel.id }}/check-now"
        hx-target="#check-result-{{ channel.id }}"
        hx-swap="innerHTML"
        hx-indicator="#spinner-{{ channel.id }}">
    Check Now
</button>
<span id="spinner-{{ channel.id }}" class="htmx-indicator">Scanning...</span>
<span id="check-result-{{ channel.id }}"></span>
```

The `htmx-indicator` class is automatically shown/hidden by HTMX during the request. CSS:

```css
.htmx-indicator {
    display: none;
}
.htmx-request .htmx-indicator,
.htmx-request.htmx-indicator {
    display: inline;
}
```

---

## Pattern: Auto-Refreshing Table with Pagination

Video list that updates every 10 seconds and supports pagination:

```html
<form id="filter-form" ...>
  <input type="hidden" name="page" id="page-input" value="{{ page }}">
  <!-- channel + status selects -->
</form>
<div hx-get="/videos"
     hx-trigger="every 10s"
     hx-target="#video-list-content"
     hx-swap="innerHTML"
     hx-include="#filter-form">
  <div id="video-list-content">
    {% include "videos/_video_list.html" %}
  </div>
</div>
```

The server detects `HX-Request` and returns the `_video_list.html` partial (table + pagination controls). Query params `page` and `per_page` come from the form when using `hx-include="#filter-form"`. Changing filters resets to page 1 (e.g. via `hx-vals='{"page": 1}'` on the form).

Above the video list, an **active downloads** panel (`_active_downloads.html`) is included. It self-polls `GET /videos/active_downloads` every 3 seconds and swaps its own container (`#active-downloads`) with the response, showing in-flight downloads with progress bars, speed, and ETA. When no downloads are active, the panel renders an empty div so it takes no visual space.

If the panel includes action buttons (e.g. Stop), have them target `#active-downloads` and swap `outerHTML` so the whole panel refreshes. This avoids duplicate `video-status-{{id}}` element IDs (the main table also uses those IDs), and lets the server return the panel HTML when `HX-Target` is `active-downloads`.

---

## Pattern: Retry Button

Retry a failed video download:

```html
<button hx-post="/videos/{{ video.id }}/retry"
        hx-target="#video-status-{{ video.id }}"
        hx-swap="outerHTML"
        hx-confirm="Retry download for '{{ video.title }}'?">
    Retry
</button>
```

---

## Pattern: Delete with Confirmation

Delete a channel with a confirm dialog:

```html
<button hx-delete="/channels/{{ channel.id }}"
        hx-target="#channel-row-{{ channel.id }}"
        hx-swap="outerHTML swap:1s"
        hx-confirm="Delete channel '{{ channel.name }}' and all its videos?">
    Delete
</button>
```

The server returns an empty response or a fade-out element. `swap:1s` adds a delay for the swap animation.

---

## Pattern: Form Submission

Add a new channel via a form:

```html
<form hx-post="/channels"
      hx-target="#channel-list"
      hx-swap="beforeend"
      hx-on::after-request="this.reset()">

    <input type="url" name="url" placeholder="Channel URL" required>
    <input type="text" name="name" placeholder="Display name">
    <input type="number" name="check_interval_hours" value="6" min="1">
    <button type="submit">Add Channel</button>
</form>
```

`hx-on::after-request="this.reset()"` clears the form after successful submission.

---

## Pattern: Bulk Edit with Global Save

Channels page bulk edit: swap the list content area with an editable form, then save all at once:

```html
<!-- Header: enter bulk edit mode -->
<button hx-get="/channels/bulk-edit"
        hx-target="#channels-content"
        hx-swap="innerHTML">
  Bulk Edit
</button>

<!-- In _bulk_edit.html: single form wrapping all rows -->
<form hx-put="/channels/bulk"
      hx-target="#channels-content"
      hx-swap="innerHTML">
  <button type="submit">Save All</button>
  <button type="button"
          hx-get="/channels"
          hx-target="#channels-content"
          hx-swap="innerHTML">
    Cancel
  </button>
  <table>
    {% for channel in channels %}
    <tr>
      <td>
        <input name="name__{{ channel.id }}" value="{{ channel.name }}">
        <input name="enabled__{{ channel.id }}" type="hidden" value="false">
        <input name="enabled__{{ channel.id }}" type="checkbox" value="true" ...>
      </td>
    </tr>
    {% endfor %}
  </table>
</form>
```

Fields are namespaced by ID (`name__5`, `enabled__5`). The backend parses keys with `rsplit("__", 1)` to group by channel and batch-update. Checkbox uses hidden+checkbox trick: hidden submits `false`, checkbox submits `true` when checked; last value wins.

---

## Server-Side: Detecting HTMX Requests

Check for the `HX-Request` header to decide whether to return a full page or a partial:

```python
@router.get("/videos")
async def list_videos(request: Request, page: int = Query(1, ge=1), per_page: int = Query(25, ge=1, le=100), ...):
    # ... apply filters, count total, apply offset/limit ...
    ctx = {"request": request, "videos": videos, "page": page, "per_page": per_page, "total": total, "total_pages": total_pages, ...}

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse("videos/_video_list.html", ctx)
    return templates.TemplateResponse("videos/list.html", {**ctx, "channels": channels, ...})
```

---

## Pattern: Modal Wizard (Add Channel)

The Add Channel flow uses a multi-step modal: Step 1 (URL + scrape), Step 2 (review metadata + settings), Step 3 (Stash linking results + save). The modal is a DaisyUI `<dialog>` included in the page; HTMX swaps only the modal body (`#add-channel-modal-body`) at each step.

**Open modal**: Button with `hx-get="/channels/add-modal"` targeting `#add-channel-modal-body`, then `hx-on::after-request="document.getElementById('add-channel-modal').showModal()"` so the dialog opens after step 1 content loads.

**Step transitions**: Each step’s form POSTs to a route that returns the next step’s partial; same target and `innerHTML` swap. Data is carried forward via hidden inputs. Back navigation uses `hx-get` to a route that re-renders the previous step with query (or form) params.

**Close on success**: The final step POSTs to `/channels` with `hx-target="body"` and `hx-swap="none"`. The server returns `_card_oob.html` — the new channel card wrapped in `hx-swap-oob="beforeend:#channel-grid"` — plus `HX-Trigger: closeAddChannelModal`. A script on the page listens for that event and calls `document.getElementById('add-channel-modal').close()`.

**Why OOB and not a direct target**: the modal lives in `base.html`, so Add Channel can be clicked from any page, but `#channel-grid` only exists on the Channels page (and is replaced while Bulk Edit is open). htmx aborts a request whose `hx-target` selector matches nothing — it fires `htmx:targetError` and never opens the connection, so the button silently does nothing. An out-of-band miss, by contrast, is a harmless no-op: the POST still runs, and the `closeAddChannelModal` handler in `base.html` redirects to `/channels` when there's no grid to append to. **Never point `hx-target` at an element that only exists on some pages.**

**Loading states**: Each step’s submit button has an `hx-indicator` pointing to a spinner element inside the modal.

---

## Template Organization

```
templates/
  base.html                    # Full page layout (nav, head, scripts)
  dashboard.html               # Extends base.html
  channels/
    list.html                  # Extends base.html (Add Channel, Bulk Edit, Check All Now, card grid)
    _list_content.html         # Partial: filter/sort nav + channel card grid
    _card.html                 # Partial: single channel card
    _detail_card.html          # Partial: channel detail (Stash sync + Channel Settings + videos)
    detail.html                # Channel detail page
    _add_modal.html            # Partial: dialog shell + initial step 1 body
    _add_step1.html            # Partial: URL input (step 1)
    _add_step2.html            # Partial: metadata review + settings (step 2)
    _add_step3.html            # Partial: Stash linking + save (step 3)
    _bulk_edit.html            # Partial: bulk edit form (all channels editable)
  videos/
    list.html                  # Extends base.html
    detail.html                # Extends base.html
    _video_list.html           # Partial: table + pagination (HTMX list target)
    _table_body.html           # Partial: video table body rows (included by _video_list.html)
    _status_badge.html         # Partial: status badge element
  settings.html                # Extends base.html
```

**Rule**: Partial templates (returned for HTMX swaps) are prefixed with `_`. They do NOT extend `base.html`.

---

## CSS Framework: Pico CSS

Pico CSS provides classless semantic styling. Just use proper HTML elements:

```html
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css">
```

- `<table>` gets clean styling automatically.
- `<button>` looks like a button.
- `<input>` is properly styled.
- `<nav>` creates a navigation bar.
- `<article>` creates card-like containers.
- `role="group"` on a `<div>` around buttons creates a button group.

Minimal custom CSS should be needed. Put overrides in `app/static/style.css`.
