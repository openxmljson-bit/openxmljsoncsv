# OPENXMLJSON Licensing API (Netlify)

A tiny serverless backend that lets the desktop app verify a customer's
subscription **without shipping any Shopify secret**. The app calls this
endpoint; the endpoint holds the Shopify Admin token server-side.

Two verification modes (both server-verified):

- **OTP** — `POST /request-otp {email}` emails a 6-digit code, then
  `POST /verify {email, code}` proves the inbox and does a live Shopify
  entitlement lookup.
- **License key** — `POST /verify {email, licenseKey}` verifies an
  HMAC-signed key you issued at purchase (no Shopify call needed).

Response shape:

```json
{ "valid": true, "tier": "Pro", "email": "a@b.com",
  "status": "active", "expires_at": "2027-01-01T00:00:00.000Z", "reason": "" }
```

## Layout

```
server/
  netlify.toml                 build + /request-otp,/verify redirects
  package.json                 @netlify/blobs; `npm test`, `npm run sign-key`
  lib/keys.mjs                 HMAC license-key sign/verify
  lib/shopify.mjs              Admin API entitlement lookup by email
  lib/otp.mjs                  code gen/store/rate-limit (Netlify Blobs)
  lib/email.mjs               Resend transactional email (swap as needed)
  netlify/functions/request-otp.mjs
  netlify/functions/verify.mjs
  scripts/sign-key.mjs         issue a license key
  test/keys.test.mjs           `node --test`
```

## 1. Shopify custom app (Admin token)

Shopify admin → Settings → Apps and sales channels → Develop apps →
Create an app → Configure Admin API scopes: `read_orders`
(and `read_own_subscription_contracts` if you use Shopify subscriptions) →
Install → copy the **Admin API access token** (`shpat_...`). This is a
secret — it only goes in Netlify env vars below.

## 2. Environment variables (Netlify → Site settings → Environment)

| Var | Purpose |
|-----|---------|
| `SHOPIFY_STORE` | `your-store.myshopify.com` |
| `SHOPIFY_ADMIN_TOKEN` | the `shpat_...` token from step 1 |
| `SHOPIFY_API_VERSION` | optional, defaults to `2025-07` |
| `RESEND_API_KEY` | Resend API key (OTP email) |
| `OTP_FROM_EMAIL` | verified sender, e.g. `login@openxmljson.com` |
| `LICENSE_SIGNING_SECRET` | random 32+ byte secret for license keys |

Generate a signing secret: `openssl rand -base64 48`.

## 3. Deploy

```
cd server
npm install
npx netlify deploy --build        # preview
npx netlify deploy --build --prod # production
```

Enable **Netlify Blobs** for the site (used by the OTP store).

## 4. Custom domain (keep DNS on Shopify)

Netlify → Domain settings → add `api.openxmljson.com` → Netlify shows a
CNAME target. In Shopify → Settings → Domains → DNS settings → add a CNAME
record: host `api`, value = the Netlify target. Netlify issues TLS
automatically. (No nameserver change; `www`/`account` stay on Shopify.)

## 5. Issue a license key

```
LICENSE_SIGNING_SECRET=... npm run sign-key -- customer@example.com Pro 365
```

Prints a key to hand the customer. Wire this into a Shopify order-paid
webhook later to automate issuance.

## Security notes

- No secret ships in the desktop app; only this server holds the Admin token
  and signing secret.
- `/request-otp` always returns `ok` for well-formed emails and is
  rate-limited, so it can't enumerate customers.
- OTP codes are stored **hashed** with a 10-minute TTL and capped attempts.
- A client-side gate is a deterrent, not DRM — pair short cache TTLs with the
  live OTP path for anything you want revocable.
```
