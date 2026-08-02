# Build Prompt — Email a License Key from Gmail (Netlify Function)

Reusable spec for the piece that **mints a license key on purchase and emails it
to the buyer from a Gmail address**. Stack-agnostic on the client side: the
email/minting lives in a serverless function, so an **Electron** app (or PySide,
or anything else) reuses it unchanged — the desktop app only ever calls
`/verify`.

> **Important framing for an Electron rebuild:** do **not** move email sending
> into Electron. The Gmail credential and the license signing secret must never
> ship in a distributed app. Electron's only job is: collect `email + key` →
> `POST /verify` → cache the result. Everything below stays server-side.

---

## Prompt

> Build a **Netlify Function** that issues and emails a license key when a
> Shopify order is paid, plus a small mailer module, in **Node ESM**.
>
> ### 1. Mailer module (`lib/gmail.mjs`)
> - Send transactional mail through **Gmail SMTP** using **nodemailer**
>   (`service: "gmail"`), authenticating with `GMAIL_USER` and a Google
>   **App Password** (`GMAIL_APP_PASSWORD`) — not the account password.
>   *Requires 2-Step Verification on the account; strip spaces from the
>   16-character app password before use.*
> - Create the transport **lazily and cache it** in module scope (one SMTP
>   connection pool per warm function instance, not one per call).
> - Throw a clear error if either env var is missing.
> - Export one function:
>   ```js
>   sendLicenseEmail({ to, name, tier, key, expiresAt, days })
>   ```
>   which sends:
>   - **From:** `"<Product> <${GMAIL_USER}>"` (Gmail cannot spoof another
>     domain — the sender will visibly be the Gmail address; accept this or
>     switch providers)
>   - **Subject:** `Your <Product> license key`
>   - **Plain-text body** containing, in this order: a greeting using the
>     buyer's first name when present; the **plan** (`tier`) with the billing
>     period rendered from `days` (365 → "annual subscription", 30 → "monthly
>     subscription"); **Valid until** `expiresAt` (omit the line for lifetime
>     keys, i.e. no expiry); a blank line; the **license key** on its own line;
>     numbered **activation steps** naming the exact UI path (e.g. *Help ▸
>     Activate… ▸ enter this key and the email you used to purchase*); for
>     monthly plans a note that a new key arrives each renewal; and a closing
>     line inviting a reply for help.
>   - Keep it plain text (deliverability, and the key must be trivially
>     selectable). No HTML required.
>
> ### 2. Wire it into the purchase webhook (`netlify/functions/orders-paid.mjs`)
> On the Shopify `orders/paid` webhook, **in this order**:
> 1. Read the **raw body** and verify the `X-Shopify-Hmac-Sha256` HMAC-SHA256
>    against `SHOPIFY_WEBHOOK_SECRET` using a constant-time compare; reject with
>    `401` on mismatch. (Parse JSON only *after* verifying — parsing first breaks
>    the signature.)
> 2. Extract the buyer email (`order.email` → `contact_email` →
>    `customer.email`); if absent, log and ack with `200`.
> 3. Derive the **tier** from the line items (title / name / SKU / variant
>    matched case-insensitively against your product names) and the **validity
>    in days** from that tier (e.g. annual 365, monthly 30), both overridable by
>    env vars.
> 4. **Mint the key** with your signing function and `LICENSE_SIGNING_SECRET`;
>    compute `expiresAt` as an ISO `YYYY-MM-DD`.
> 5. **`await sendLicenseEmail(...)`** — this is the *primary* delivery path.
>    Log a single success line with tier, days, expiry and recipient.
> 6. **Then**, best-effort and inside its own `try/catch`, record the key on the
>    order (e.g. a metafield) so it's visible in the store admin. A failure here
>    must **never** prevent or undo the email.
> 7. Always return **200** (even on internal failure) so the store doesn't
>    retry-storm; surface problems through logs.
>
> ### 3. Dependencies & config
> - `package.json`: `"type": "module"`, dependency **`nodemailer`**.
> - Env vars (host dashboard + a gitignored local `.env`):
>   `GMAIL_USER`, `GMAIL_APP_PASSWORD`, `LICENSE_SIGNING_SECRET`,
>   `SHOPIFY_WEBHOOK_SECRET`, plus the tier/day overrides.
> - **No secret may appear in the repo or in any shipped client.**
>
> ### 4. Acceptance
> - A paid test order results in an email arriving at the buyer's address,
>   sent from the Gmail account, containing a correct plan, expiry and key.
> - Tampering with the webhook body yields `401`; a valid body always yields
>   `200`.
> - The email still sends when the metafield write fails.
> - Wrong/missing app password surfaces as a clear `Invalid login` in logs
>   rather than a silent failure.

---

## Notes worth keeping (learned in production)

- **Gmail limits:** ~500 messages/day on a consumer account — fine for license
  delivery, not for marketing. The `From:` is always the Gmail address.
- **Why not the store's own email?** Shopify Flow's "Send internal email" can't
  use a variable recipient, "Send order invoice" only works on *unpaid* orders,
  and the order-confirmation template races the metafield write (the key isn't
  saved yet). Sending from the webhook is the only reliable path.
- **Swapping providers** is a one-file change: keep the
  `sendLicenseEmail(...)` signature and re-implement the transport with
  Resend / SendGrid / Mailgun / SES if you outgrow Gmail or want your own
  domain in the `From:`.

---

## Electron client side (for completeness)

The desktop app never mints or emails anything. It only:

1. Shows an **Activate** dialog: email + license key.
2. `POST https://<your-api>/verify  { email, licenseKey }`
   → `{ valid, tier, email, status, expires_at, reason }`.
3. Caches the entitlement locally (OS keychain via `keytar`/`safeStorage`, with
   a file fallback) with a TTL; cache **no-expiry keys permanently** so lifetime
   users never re-verify.
4. Renders a tier badge and gates whatever the free tier limits.

Do the `/verify` call from the **main process** (not the sandboxed renderer) and
expose it to the UI through a typed `contextBridge` method.
