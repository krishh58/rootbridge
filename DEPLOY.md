# RootBridge — Railway Deployment

## Prerequisites

- Node.js installed (for Railway CLI): `node --version`
- Railway account: https://railway.app

## First Deploy

```bash
# 1. Install Railway CLI
npm install -g @railway/cli

# 2. Log in
railway login

# 3. Create project (from inside familytree_app/)
railway init
# → name it: rootbridge

# 4. Add PostgreSQL
railway add --plugin postgresql

# 5. Add Redis
railway add --plugin redis

# 6. Deploy
railway up
```

Railway injects `DATABASE_URL` and `REDIS_URL` automatically from the plugins.

## Environment Variables

Set these in the Railway dashboard under **Variables**:

| Variable | How to get it |
|---|---|
| `SECRET_KEY` | Run: `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `OPENROUTER_API_KEY` | openrouter.ai → Keys |
| `FAMILYSEARCH_CLIENT_ID` | developers.familysearch.org → Register app (free) |
| `FAMILYSEARCH_CLIENT_SECRET` | Same as above |
| `STRIPE_SECRET_KEY` | dashboard.stripe.com → Developers → API keys (`sk_test_` for staging) |
| `STRIPE_WEBHOOK_SECRET` | Stripe → Webhooks → Add endpoint → signing secret |
| `GMAIL_USER` | Gmail address used for invite emails (e.g. rootbridge.app@gmail.com) |
| `GMAIL_APP_PASSWORD` | Google Account → Security → App Passwords (requires 2FA on) |

### Stripe price IDs (needed for subscription checkout)

| Variable | Description |
|---|---|
| `STRIPE_PRICE_US` | Price ID for Explorer tier ($9.99/mo recurring) — the only subscription tier |
| `STRIPE_PRICE_TOPUP_300` | Price ID for 300-token top-up (one-time) |
| `STRIPE_PRICE_TOPUP_1000` | Price ID for 1000-token top-up (one-time) |
| `STRIPE_PRICE_TOPUP_2500` | Price ID for 2500-token top-up (one-time) |
| `STRIPE_PRICE_TOPUP_5000` | Price ID for 5000-token top-up (one-time) |

Create `STRIPE_PRICE_US` as a recurring monthly subscription at $9.99. Create top-up prices as one-time payments in the Stripe dashboard.

## Stripe Webhook Setup

After first deploy, get your Railway URL (`https://rootbridge-xxx.railway.app`) and:

1. Stripe dashboard → Developers → Webhooks → Add endpoint
2. URL: `https://rootbridge-xxx.railway.app/webhook/stripe`
3. Events to listen for:
   - `checkout.session.completed`
   - `customer.subscription.created`
   - `customer.subscription.deleted`
4. Copy the signing secret → set as `STRIPE_WEBHOOK_SECRET`

## Verify

```bash
curl https://rootbridge-xxx.railway.app/health
# Expected: {"status":"ok"}
```

## Redeploy after changes

```bash
railway up
```
