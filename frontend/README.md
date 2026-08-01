# Stockara Frontend

React/Vite static frontend for Stockara 1.0.

The public app is a read-only decision-support dashboard. It renders the
latest generated artifacts and does not trigger collection or analysis when a
user refreshes the page. See `docs/steering/stockara-1.0.md` for the complete
runtime baseline.

The app reads generated static artifacts from:

- `/top-picks/latest.json`
- `/sell-alerts/latest.json`
- `/data-readiness/latest.json`
- `/workflow/latest.json`

Useful commands:

```bash
npm ci
npm run lint
npm run build
npm run dev
```
