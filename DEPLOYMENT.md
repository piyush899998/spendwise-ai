# Public synthetic demo deployment

This configuration is for a portfolio demo only. Do not connect it to the local
`spendwise.db`, upload real statements, or add real financial records.

## Backend: Render Free Web Service

The repository includes `render.yaml` for the FastAPI service.

- Build: `pip install -r requirements.txt`
- Start: `uvicorn backend.main:app --host 0.0.0.0 --port $PORT`
- Health check: `/api/health`
- Database: `/tmp/spendwise-demo.db`, which is intentionally ephemeral
- Demo mode: seeds synthetic data only when the database is empty
- External OpenRouter calls: disabled

After the Render service exists, set `SPENDWISE_CORS_ORIGINS` to the exact
frontend origin, for example `https://your-demo.example`.

## Frontend: Vercel or Render

Use `frontend` as the project root and set:

```text
NEXT_PUBLIC_API_URL=https://your-api.onrender.com/api
```

The frontend must point to the deployed API before the production build runs.

## Privacy boundary

The public deployment is a synthetic-data showcase. The current application does
not provide production authentication or tenant isolation, so it must not be
used for real statements or personal financial data. Keep private data local.
