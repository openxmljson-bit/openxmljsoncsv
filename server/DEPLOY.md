# Deploy checklist — OPENXMLJSON Licensing API

Run these on **your machine**, in order. All commands assume you're in the
`server/` folder unless noted.

## 0. Prerequisites (one time)
- [ ] Node 18+ installed (`node -v`)
- [ ] A Netlify account (free) — https://app.netlify.com
- [ ] A Resend account + API key + a verified sender domain — https://resend.com
- [ ] Shopify custom app created with Admin API scope `read_orders`
      (and `read_own_subscription_contracts` if you use subscriptions);
      copy its Admin API access token (`shpat_...`)

## 1. Install dependencies
```
cd server
npm install
```

## 2. Generate a signing secret
```
openssl rand -base64 48
```
Save the output — it's `LICENSE_SIGNING_SECRET` (used to issue and verify keys).

## 3. Local smoke test (optional but recommended)
```
cp .env.example .env          # fill in real values
npx netlify dev               # serves functions locally at http://localhost:8888
```
In another terminal:
```
curl -X POST http://localhost:8888/request-otp \
  -H 'content-type: application/json' -d '{"email":"you@example.com"}'
# check your inbox, then:
curl -X POST http://localhost:8888/verify \
  -H 'content-type: application/json' \
  -d '{"email":"you@example.com","code":"123456"}'
```
Run the key unit tests too:
```
npm test
```

## 4. Create + link the Netlify site
```
npx netlify login
npx netlify init          # create a new site (base directory: server)
```

## 5. Set environment variables on Netlify
Dashboard ▸ Site settings ▸ Environment variables — add:
- [ ] `SHOPIFY_STORE`
- [ ] `SHOPIFY_ADMIN_TOKEN`
- [ ] `SHOPIFY_API_VERSION` (optional)
- [ ] `RESEND_API_KEY`
- [ ] `OTP_FROM_EMAIL`
- [ ] `LICENSE_SIGNING_SECRET`

Also enable **Netlify Blobs** for the site (Site settings ▸ Blobs) — the OTP
store uses it.

## 6. Deploy
```
npx netlify deploy --build          # preview URL — test it
npx netlify deploy --build --prod   # go live
```

## 7. Custom domain (keeps DNS on Shopify)
- [ ] Netlify ▸ Domain settings ▸ add `api.openxmljson.com`; copy the CNAME
      target Netlify shows.
- [ ] Shopify ▸ Settings ▸ Domains ▸ DNS settings ▸ Add custom record:
      type CNAME, host `api`, value = the Netlify target.
- [ ] Wait for TLS to provision, then verify:
```
curl -X POST https://api.openxmljson.com/request-otp \
  -H 'content-type: application/json' -d '{"email":"you@example.com"}'
```

## 8. Issue a license key (when needed)
```
LICENSE_SIGNING_SECRET=... npm run sign-key -- customer@example.com Pro 365
```

## 9. Point the desktop app at it
No app change needed if you use the default `https://api.openxmljson.com`.
Otherwise set `OXJ_API_BASE` in the app's environment.

---
Reminder: never commit `.env`. Only `.env.example` belongs in git.
