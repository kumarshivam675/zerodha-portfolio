# web

Next.js frontend for the portfolio app: dashboard, snapshots, analyst chat and the per-holding
analysis panel. Requests to `/api/*` are proxied to the FastAPI service (`API_URL`, default
`http://localhost:8000`) by `app/api/[...path]/route.ts`, which also streams the analyst's
Server-Sent Events.

See the [root README](../../README.md) for setup. For local development without Docker:

```bash
npm install
API_URL=http://localhost:8000 npm run dev
```
