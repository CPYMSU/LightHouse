# LightHouse Desktop Kernel 0.5

## Purpose

Desktop Kernel is the third governed execution surface beside Data and System.
It controls user-facing applications without giving the model unrestricted GUI
or input-device authority.

```text
LightHouse Brain
  -> Capability Atlas
  -> immutable Operation
  -> Desktop Target policy
  -> Desktop Executor
  -> durable Receipt
```

## Mature design principle: semantic actions before pixels

The first implementation decomposes the strongest idea from mature computer-use
systems: prefer stable semantic actions over screen-coordinate guessing.

For macOS, application and document launching is delegated to Launch Services
through `/usr/bin/open`. This gives deterministic operating-system behavior for:

- opening HTTP/HTTPS URLs;
- previewing a generated HTML file;
- opening a confined project document;
- launching one allow-listed application.

No mouse coordinates, keystroke injection, Accessibility permission or screenshot
vision loop is required for this slice.

## Capabilities

### `desktop.browser.open_url.v1`

Arguments:

```json
{"url":"https://example.com","browser":"Safari"}
```

The URL scheme must be enabled on the Desktop Target. HTTP and HTTPS require a
host. A `file://` URL is converted to a real path and checked against
`allowed_roots`.

### `desktop.file.open.v1`

Arguments:

```json
{"path":"dashboard.html","browser":"Safari"}
```

Relative paths are resolved from the Desktop Target `default_cwd`; the resolved
file must exist and stay inside an allowed root.

### `desktop.app.open.v1`

Arguments:

```json
{"app":"Safari"}
```

The application must be listed in `allowed_apps`. This capability requires an
explicit confirmation Operation.

## Target contract

```json
{
  "platform": "macos",
  "default_cwd": "/Users/user/project",
  "allowed_roots": ["/Users/user/project"],
  "allowed_apps": ["Safari", "Google Chrome", "Firefox", "Arc", "Finder"],
  "allowed_schemes": ["http", "https", "file"],
  "browser": "default"
}
```

The target record contains no credentials. A local project workspace binds the
same project to a System Target and Desktop Target and runs in `AUTO` mode.

## HTML creation and preview

A natural-language request such as:

> Create `dashboard.html` and open it in Safari.

is not one opaque shell command. LightHouse should plan two separate operations:

1. System capability creates or patches the project file and yields a Receipt.
2. Desktop capability resolves and opens the exact file and yields a Receipt.
3. The Brain verifies both observations before claiming completion.

## Future browser adapter

Opening a page and operating inside a page are different authority surfaces.
Interactive browser work should be implemented later as a dedicated
Playwright/Chrome DevTools Protocol adapter with typed capabilities such as:

- `browser.page.inspect`
- `browser.element.click`
- `browser.form.fill`
- `browser.download`
- `browser.screenshot`

Those capabilities must remain behind Desktop Target policy, frozen Operations
and Receipts. Pixel-based mouse control should remain a fallback, not the primary
architecture.
