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

## Pattern: Auto-Refreshing Table

Video list that updates every 10 seconds:

```html
<div hx-get="/videos?partial=true"
     hx-trigger="every 10s"
     hx-target="#video-table-body"
     hx-swap="innerHTML">

    <table>
        <thead>...</thead>
        <tbody id="video-table-body">
            {% include "videos/_table_body.html" %}
        </tbody>
    </table>
</div>
```

**Rule**: The `partial=true` query param tells the server to return only the table body fragment, not the full page.

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

## Server-Side: Detecting HTMX Requests

Check for the `HX-Request` header to decide whether to return a full page or a partial:

```python
@router.get("/videos")
async def list_videos(request: Request, partial: bool = False, ...):
    videos = ...

    if request.headers.get("HX-Request") or partial:
        return templates.TemplateResponse("videos/_table_body.html", {
            "request": request,
            "videos": videos,
        })
    return templates.TemplateResponse("videos/list.html", {
        "request": request,
        "videos": videos,
    })
```

---

## Template Organization

```
templates/
  base.html                    # Full page layout (nav, head, scripts)
  dashboard.html               # Extends base.html
  channels/
    list.html                  # Extends base.html
    add.html                   # Extends base.html
    _row.html                  # Partial: single channel table row
  videos/
    list.html                  # Extends base.html
    detail.html                # Extends base.html
    _table_body.html           # Partial: video table body rows
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
