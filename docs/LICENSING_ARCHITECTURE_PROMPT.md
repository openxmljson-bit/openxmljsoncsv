# Licensing Architecture — Reference & Build Prompt

How OPENXMLJSON manages editions, how it relates to Shopify, and what Netlify +
webhooks do to issue license keys. Written so it doubles as a **build prompt**:
hand the whole file to an agent and it can rebuild the system.

---

## 0. One-paragraph summary

There is **one build** of the app. What a user may do is decided **at runtime**
by a signed license key, not by a compile-time edition. Shopify is only the
**shop** (checkout + the order record). When an order is paid, Shopify fires a
**webhook** to a **Netlify function**, which mints a short **HMAC-signed license
key** and emails it to the buyer. The desktop app verifies that key against a
second Netlify function and caches the result. **The app never talks to Shopify**
and **holds no secrets**; the server stores **no per-user data**.

```
                    ┌───────────── Shopify (store) ─────────────┐
  buyer checkout →  │ product: Essential (monthly)              │
                    │ product: Premium  (annual)                │
                    └───────┬───────────────────────────────────┘
                            │ orders/paid webhook (HMAC-signed)
                            ▼
        ┌──────────── Netlify (checkapi.openxmljson.com) ────────────┐
        │ /orders-paid : verify HMAC → derive tier → issueKey()      │
        │                → email key (Gmail SMTP)                    │
        │                → write order metafield (best-effort)       │
        │ /verify      : verifyKey() — signature + expiry + email    │
        └───────┬─────────────────────────────────────┬──────────────┘
                │ email with key                      │ POST {email, licenseKey}
                ▼                                     ▲
             buyer ─────── pastes key ──────► OPENXMLJSON desktop app
                                              (caches entitlement locally)
```

---

## 1. How editions / tiers are managed

### 1.1 One build, runtime gate

* `python/openxmljson/edition.py` holds only build-wide constants:
  * `TRIAL_MAX_BYTES = 50 MB` — the Trial size cap.
  * `UPDATES_ENABLED = True`.
  * There are **no** compile-time editions (the old `essential`/`premium`/
    `narik` build flavors were removed).
* `python/openxmljson/licensing/status.py` is the **single source of truth**:
  * `is_licensed()` → bool (a valid, unexpired cached entitlement exists)
  * `tier()` → `"Essential" | "Premium" | "Unbxd" | None`
  * `membership_badge()` → `(label, color)`

### 1.2 The tiers

| Tier | Sold as | Validity | Badge | Notes |
|---|---|---|---|---|
| **Trial** | — (no license) | n/a | **red** `#D9433B` | Data files capped at 50 MB |
| **Essential** | $4.99 / month | 30 days | **blue** `#2F6BE3` | New key each renewal |
| **Premium** | $49.99 / year | 365 days | **green** `#2FA55A` | Cheaper per month |
| **Netcore Unbxd** | not sold — internal | **lifetime** (no expiry) | **orange** `#D97757` | Issued by CLI to staff |

**Both paid tiers unlock exactly the same features.** They differ only in
billing period. That is deliberate: one product, one binary, no feature matrix.

### 1.3 What the gate actually does

* **Trial:** every *data* format — JSON, NDJSON, XML, CSV, TSV, YAML — is capped
  at `TRIAL_MAX_BYTES` (50 MB). Plain text (`.txt`, `.js`, `.log`, `.py`) is not
  gated. Exceeding the cap shows an **upsell dialog** (file size vs limit, with
  *Activate…* / *View plans* / *Not now*) instead of opening.
* **Licensed (any tier):** the cap is removed. Nothing else changes.
* **Expired license:** the app quietly falls back to Trial behavior — it never
  hard-locks or destroys work.

### 1.4 Where the user sees it

* **Center-box pill** on the welcome screen: `Trial` / `Essential` / `Premium` /
  `Netcore Unbxd` in the colors above.
* **Status-bar badge** (clickable → Activate dialog).
* **Membership card** (welcome screen, below Memory): Status, Plan, Account
  (email, ellipsized + tooltip; card widens 50 % when an email is present),
  Valid (`Lifetime` or `until YYYY-MM-DD`). Lifetime licenses hide the
  "Manage subscription" button.
* **Help ▸ Activate…** opens the license dialog.

### 1.5 The license key format (`server/lib/keys.mjs`)

Compact, stateless, self-verifying — **15 bytes → 24 Crockford base32 chars**,
grouped as `XXXX-XXXX-XXXX-XXXX-XXXX-XXXX` (29 chars incl. dashes):

```
header (7 bytes)
  [0]    version (1)
  [1]    tier index into TIERS = ["Essential", "Premium", "Unbxd"]   // index-stable: append only
  [2..3] expiry as days-since-epoch, uint16 BE (0 = never expires)
  [4..6] first 3 bytes of sha256(lowercased email)   // binds the key to the buyer
signature (8 bytes)
  first 8 bytes of HMAC-SHA256(header, LICENSE_SIGNING_SECRET)
```

* Verification = recompute the HMAC (constant-time compare), check expiry, check
  the email fingerprint. **No database, no lookup.**
* Input is forgiving: case-insensitive, dashes/spaces ignored, `O→0`, `I/L→1`,
  `U→V` (Crockford), so buyers can't really mistype it.
* Truncated 64-bit HMAC is ample for a purchase gate.
* **`TIERS` must never be reordered** — the index is encoded in issued keys.

### 1.6 Issuing internal lifetime keys

```bash
cd server
LICENSE_SIGNING_SECRET=… node scripts/sign-key.mjs teammate@netcoreunbxd.com Unbxd 0
# 0 days = never expires  →  e.g. 0410-002J-TCJB-CAR8-9XMA-SNRY
```

The app caches lifetime keys **permanently** (`cache.py`: no `expires_at` ⇒
`cached_until` ≈ +100 years), so after one activation those users never contact
the server again.

---

## 2. How the connection to Shopify is established

### 2.1 The key decision: the app never contacts Shopify

Shopify's Customer Account API can only authenticate a shopper through its
**hosted login page** (OAuth redirect) — there is no password grant, so a
desktop app cannot verify a customer by email+password. And the only credential
that *can* look up orders — the **Admin API** credential — is a secret that must
never ship inside a distributed binary.

**Therefore:** Shopify is contacted **only server-side**, and only in the
purchase direction. The app talks exclusively to our own Netlify endpoint.

### 2.2 Shopify setup

1. **Products** — two: one containing "Essential" (monthly), one containing
   "Premium" (annual). The webhook derives the tier by matching those words in
   the line items, so **the product titles matter**.
2. **Custom app** (Settings ▸ Apps and sales channels ▸ Develop apps) with Admin
   API scopes `read_orders` + `write_orders`, and **protected customer data
   access** requested/approved (orders contain PII — the scope alone is not
   enough; without this, Admin calls fail with *"requires merchant approval for
   read_orders"*).
3. **Webhook** — Settings ▸ Notifications ▸ Webhooks → event **Order payment**
   (`orders/paid`), JSON, URL `https://checkapi.openxmljson.com/orders-paid`.
   That page also shows the **signing secret** → `SHOPIFY_WEBHOOK_SECRET`.

### 2.3 Admin API authentication (2026 model)

Shopify **deprecated static `shpat_` tokens** for new custom apps (Jan 1 2026).
A modern custom app exposes a **Client ID + Client Secret**, which the server
exchanges for a short-lived access token via the **client-credentials grant**:

```
POST https://{SHOPIFY_STORE}/admin/oauth/access_token
     { client_id, client_secret, grant_type: "client_credentials" }
  → { access_token, expires_in }
```

`server/lib/shopify.mjs` does this exchange, **caches the token in memory**
until shortly before expiry, and still accepts a legacy `shpat_` token if
`SHOPIFY_ADMIN_TOKEN` is provided. Two gotchas worth stating explicitly:

* `SHOPIFY_STORE` must be the **`*.myshopify.com`** domain — the Admin API does
  not answer on a custom storefront domain.
* Shopify returns **HTTP 200 with a top-level `errors` array** for permission
  problems, so the helper must inspect the body, not just the status code.

### 2.4 Inbound webhook verification

`orders/paid` is authenticated by **HMAC-SHA256 over the raw request body** with
`SHOPIFY_WEBHOOK_SECRET`, compared in constant time against the
`X-Shopify-Hmac-Sha256` header. The body must be read as **raw text before JSON
parsing** — parsing first breaks the signature. Invalid signature → `401`.

---

## 3. The role of Netlify and the webhook

Netlify hosts the licensing API at **`checkapi.openxmljson.com`** (a CNAME from
Shopify-managed DNS to the Netlify site, so DNS stays on Shopify; Netlify issues
the TLS certificate). Deploy from `server/`; `netlify.toml` maps clean paths onto
the functions.

### 3.1 `/orders-paid` — mint and deliver the key (write path)

Triggered by Shopify on payment:

1. **Verify** the HMAC over the raw body (else `401`).
2. **Extract** the buyer email (`order.email` / `contact_email` /
   `customer.email`); no email ⇒ ack and stop.
3. **Derive the tier** from the line items: title/name/SKU/variant containing
   "premium" ⇒ **Premium**, "essential" ⇒ **Essential** (Premium wins if both);
   fallback `LICENSE_DEFAULT_TIER`.
4. **Derive validity**: Premium `PREMIUM_DAYS` (365), Essential
   `ESSENTIAL_DAYS` (30).
5. **`issueKey({email, tier, days})`** with `LICENSE_SIGNING_SECRET`.
6. **Email the key** to the buyer via **Gmail SMTP** (nodemailer + a Google
   **App Password**) — subject, plan, "Valid until", the key, and activation
   steps. *This is the primary delivery path.*
7. **Best-effort**: also write the key onto the order as metafield
   `openxmljson.license_key` (visible in admin once a metafield **definition**
   exists). A failure here must **not** block the email.
8. Always return **200** so Shopify does not retry; log failures.

> Why the webhook emails directly instead of using Shopify: Shopify Flow's
> "Send internal email" cannot use variables for the recipient, "Send order
> invoice" only works on *unpaid* orders, and the order-confirmation template
> races the metafield write (the key isn't there yet). Emailing from the webhook
> is the only reliable option.

### 3.2 `/verify` — validate a key (read path, what the app calls)

```
POST /verify   { email, licenseKey }
  → { valid, tier, email, status, expires_at, reason }
```

* Pure **signature + expiry + email-fingerprint** check. **No Shopify call, no
  database.** Requires only `LICENSE_SIGNING_SECRET`.
* Therefore **zero per-user server state** — 200k users cost nothing to store,
  and verification is O(1) computation.
* Consequence to accept: a refunded/cancelled order isn't detected until the key
  expires (30 days for monthly). Revocation would need a live Shopify lookup or
  a blocklist.

*(`/request-otp` exists for an email-code flow but is unused: it needs a
transactional email provider and the license-key flow replaced it. The `/verify`
key path deliberately imports nothing OTP-related — a static import of the OTP
module once broke the function with `Cannot find package '@netlify/blobs'`.)*

### 3.3 Environment variables (Netlify site settings)

| Variable | Needed for | Notes |
|---|---|---|
| `LICENSE_SIGNING_SECRET` | **`/verify` + minting** | The only one `/verify` needs. Rotating it invalidates every issued key. |
| `SHOPIFY_WEBHOOK_SECRET` | webhook auth | From the Shopify Webhooks page |
| `SHOPIFY_STORE` | Admin API | Must be `*.myshopify.com` |
| `SHOPIFY_CLIENT_ID` / `SHOPIFY_CLIENT_SECRET` | Admin API | Client-credentials grant |
| `SHOPIFY_ADMIN_TOKEN` | Admin API (legacy) | Optional `shpat_…` alternative |
| `GMAIL_USER` / `GMAIL_APP_PASSWORD` | key email | Google App Password (2-Step Verification required) |
| `PREMIUM_DAYS` / `ESSENTIAL_DAYS` / `LICENSE_DEFAULT_TIER` | optional | Defaults 365 / 30 / Essential |

Secrets live **only** here (and in a gitignored local `.env`). Never in the repo,
never in the shipped app.

---

## 4. Desktop client (what ships)

| File | Role |
|---|---|
| `licensing/config.py` | `ApiConfig.from_env()` — `OXJ_API_BASE` (default `https://checkapi.openxmljson.com`), store URL, timeout, cache TTL. **Public values only.** |
| `licensing/client.py` | `verify_key(email, key)` → `Entitlement`. Uses **stdlib `urllib`** (no `requests`) and loads **certifi's CA bundle explicitly** — Windows' Python ignores the OS trust store, which otherwise causes `CERTIFICATE_VERIFY_FAILED`. |
| `licensing/cache.py` | Entitlement cache: OS **keyring** if available, else `~/.config/OPENXMLJSON/license.json`. TTL = `cache_hours` (24); **lifetime keys cached ~forever**. |
| `licensing/status.py` | `is_licensed()`, `tier()`, `membership_badge()`. |
| `licensing/ui.py` | `LicenseDialog` (email + license key + Verify, Buy/Manage, Close) and `ensure_licensed()`. |
| `app.py` | Help ▸ Activate…, status-bar badge, size-gate upsell. |
| `packaging/openxmljson.spec` | Bundles certifi's `cacert.pem` (`collect_data_files("certifi")` + hiddenimport) so TLS works in the frozen app. |

**Honest limitation:** any client-side gate can be patched out of a local binary.
This is a conversion mechanism, not DRM — pair it with a modest cache TTL and
accept that determined bypass is possible.

---

## 5. End-to-end flows

**Purchase → activation**
1. Buyer checks out an Essential/Premium product on Shopify.
2. Shopify → `POST /orders-paid` (HMAC-signed).
3. Netlify verifies, derives tier + days, mints the key, emails it, writes the
   order metafield.
4. Buyer opens the app → Help ▸ Activate… → email + key → **Verify**.
5. App `POST /verify` → `{valid, tier, expires_at}` → cached locally.
6. Badge turns blue/green, the 50 MB cap disappears.

**Monthly renewal:** a renewal is a new paid order ⇒ the webhook mints and emails
a fresh 30-day key; the customer pastes it when the old one lapses.

**Internal staff:** `sign-key.mjs … Unbxd 0` → lifetime key → activates once,
badge shows **Netcore Unbxd** (orange), never re-verifies.

---

## 6. Build prompt (rebuild from scratch)

> Build a serverless licensing system for a cross-platform desktop app, on
> **Netlify Functions + a Shopify store**, with **no per-user server storage** and
> **no secrets in the client**.
>
> 1. **Key format** — implement compact HMAC keys exactly as §1.5: 15 bytes
>    (version, tier index, days-since-epoch expiry, 3-byte email fingerprint,
>    8-byte truncated HMAC-SHA256), Crockford base32, grouped in 4s, lenient
>    decode. `issueKey({email, tier, days})` / `verifyKey(key, secret, {email})`.
>    Tier list is index-stable and append-only. Unit-test: round-trip, tamper,
>    wrong secret, expiry, email mismatch, no-expiry.
> 2. **`/verify`** — `POST {email, licenseKey}` → `{valid, tier, email, status,
>    expires_at, reason}`; signature + expiry + fingerprint only; requires just
>    the signing secret; must not import anything unnecessary.
> 3. **`/orders-paid`** — Shopify `orders/paid` webhook: HMAC-verify the **raw
>    body**, derive tier from line items ("premium"/"essential"), map to
>    365/30 days, mint the key, **email it** (SMTP with an app password), then
>    best-effort write it as an order metafield; always return 200.
> 4. **Shopify side** — two products (Essential monthly, Premium annual); a
>    custom app with `read_orders`+`write_orders` **and protected customer data
>    access**; Admin auth via the **client-credentials grant** against the
>    `*.myshopify.com` domain with an in-memory token cache; treat HTTP 200 with
>    a top-level `errors` array as failure.
> 5. **Custom domain** — serve the API from a subdomain via a CNAME so the store
>    keeps its existing DNS.
> 6. **Desktop client** — env-driven public config; verify over **stdlib HTTP with
>    an explicitly loaded CA bundle** (Windows); cache the entitlement in the OS
>    keyring with a file fallback and a TTL, caching **no-expiry keys
>    permanently**; expose `is_licensed()` / `tier()` / `badge()`.
> 7. **Gate** — one build; without a valid license cap all *data* formats at a
>    fixed size (plain text exempt) and show an upsell at the wall; any paid tier
>    removes the cap; expiry degrades gracefully to Trial.
> 8. **UI** — a tri/quad-state badge (Trial red, Essential blue, Premium green,
>    internal orange), a Membership card (status, plan, account, valid-until or
>    Lifetime), an Activate dialog (email + key), and a Buy/Manage link.
> 9. **Internal licenses** — a CLI that mints lifetime keys for staff.
> 10. **Secrets** — only in the host's env vars and a gitignored `.env`; ship the
>     CA bundle with the frozen app; never embed the signing secret or Admin
>     credentials in the client.
