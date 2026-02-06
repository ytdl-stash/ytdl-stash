# ADR-005: Use Jinja2 + HTMX Instead of an SPA

**Status**: Accepted

## Context

The app needs a web UI for:
- Viewing dashboard stats (channel count, video count, queue status).
- Managing channels (add, edit, enable/disable, trigger scan).
- Browsing videos (filter by status/channel, view detail, retry failures).
- Checking settings and Stash connectivity.

We need to decide between a traditional server-rendered approach and a JavaScript SPA.

## Decision

Use **Jinja2 templates** (server-side rendering) enhanced with **HTMX** for interactive behavior. Use **Pico CSS** (or Simple.css) for styling defaults with no build step.

## Alternatives Considered

### React / Vue / Svelte SPA
- Rich, app-like user experience.
- Requires a separate build step (Node.js, Vite/Webpack, npm).
- Adds significant complexity: API serialization layer, CORS, state management, client-side routing.
- Doubles the cognitive overhead: Python backend + JavaScript frontend.
- Rejected as disproportionate for this admin-panel-style UI.

### Streamlit / Gradio
- Rapid prototyping, minimal code.
- Very limited customization.
- Cannot coexist easily with FastAPI routes.
- Rejected because we need a custom UI with specific CRUD flows.

### Plain Jinja2 (no HTMX)
- Simplest approach: full page reloads for every action.
- Workable but clunky UX (page flash on every toggle/action).
- HTMX adds negligible complexity while dramatically improving UX.
- Rejected in favor of Jinja2 + HTMX.

## Consequences

**Positive:**
- Zero build step. Templates are plain HTML files.
- Single language stack: everything is Python.
- HTMX attributes (`hx-get`, `hx-post`, `hx-swap`, `hx-trigger`) provide SPA-like interactions with server-rendered HTML fragments.
- Pico CSS provides clean, accessible defaults without writing custom CSS.
- Easy to understand and modify for contributors who know HTML.

**Negative:**
- Less interactive than a full SPA (e.g., no client-side filtering, no optimistic updates).
- HTMX requires returning HTML fragments from endpoints, which means some routes serve both full-page renders and partial HTML swaps.
- No offline support (not needed for this use case).

## HTMX Patterns Used

| Interaction | HTMX Attribute | Behavior |
|-------------|---------------|----------|
| Toggle channel enabled | `hx-put="/channels/{id}"` | Inline swap of the toggle element |
| Check now button | `hx-post="/channels/{id}/check-now"` | Button swap with loading indicator |
| Retry failed video | `hx-post="/videos/{id}/retry"` | Swap status badge |
| Live video list update | `hx-trigger="every 10s"` `hx-get="/videos"` | Auto-refresh table body |
| Test Stash connection | `hx-post="/settings/test-stash"` | Swap result indicator |

## References

- [HTMX docs](https://htmx.org/docs/)
- [Pico CSS](https://picocss.com/)
- [Jinja2 docs](https://jinja.palletsprojects.com/)
