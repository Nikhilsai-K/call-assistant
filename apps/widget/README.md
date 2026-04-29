# @vocalflow/widget

Drop-in voice agent for any website.

```html
<script
  src="https://cdn.vocalflow.app/widget.js"
  data-agent-id="YOUR_AGENT_ID"
  data-api-base="https://api.vocalflow.app"
  defer
></script>
```

To enable an agent for the widget, ensure its status is `published` and add `public_widget` to its `tools_enabled` list (or set `business_hours.widget_public = true`).

Build:

```bash
pnpm install
pnpm build
# emits dist/widget.js (IIFE, ready for any CDN)
```

Rate limits: 5 connects per IP per minute, 60 per agent per hour. Adjust in `apps/api/app/api/widget.py`.
