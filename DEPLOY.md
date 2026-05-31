# RootBridge — Railway Deployment

## First Deploy

1. Install Railway CLI: `npm install -g @railway/cli`
2. `railway login`
3. `railway init` — name the project `rootbridge`
4. Add PostgreSQL plugin in Railway dashboard
5. Add Redis plugin in Railway dashboard
6. `railway up`

## Environment Variables (set in Railway dashboard)

Generate SECRET_KEY: `python3 -c "import secrets; print(secrets.token_hex(32))"`

| Variable | Description |
|----------|-------------|
| SECRET_KEY | Long random string — generate as above |
| OPENROUTER_API_KEY | From openrouter.ai |
| FAMILYSEARCH_CLIENT_ID | From FamilySearch developer portal (free) |
| FAMILYSEARCH_CLIENT_SECRET | From FamilySearch developer portal |
| STRIPE_SECRET_KEY | From Stripe dashboard (use sk_test_ for staging) |
| STRIPE_WEBHOOK_SECRET | From Stripe webhook endpoint setup |

DATABASE_URL and REDIS_URL are injected automatically by Railway plugins.

## Verify

After deploy: `curl https://your-app.railway.app/health`
Expected: `{"status":"ok"}`
