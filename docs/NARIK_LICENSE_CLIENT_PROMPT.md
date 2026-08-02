# Build Prompt — NARIK license validation (Electron client)

Give this to the NARIK app. The licensing **backend already exists and is
live** — NARIK only has to call it correctly. The single most important detail:
**send `product: "narik"`**, otherwise the server can't tell a NARIK key from an
OPENXMLJSON one.

---

## Prompt

> Add license activation to the NARIK Electron app. A shared licensing API
> already exists — do **not** build a server, mint keys, or talk to Shopify.
>
> ### Endpoint
> ```
> POST https://checkapi.openxmljson.com/verify
> Content-Type: application/json
>
> { "email": "buyer@acme.com",
>   "licenseKey": "041N-1GMF-138K-K5BN-AXJQ-JAER",
>   "product": "narik" }            ← REQUIRED, always exactly "narik"
> ```
>
> Response (always HTTP 200 for a well-formed request):
> ```json
> { "valid": true,
>   "tier": "Narik",
>   "email": "buyer@acme.com",
>   "status": "active",
>   "expires_at": "2027-08-02T00:00:00.000Z",
>   "reason": "Valid license key" }
> ```
> * `valid: false` also returns a human-readable `reason` — **show it verbatim**
>   (e.g. *"License key expired."*, *"This key is for OPENXMLJSON, not this
>   application."*, *"This key was issued for a different email."*).
> * HTTP 429 → rate limited; 4xx/5xx → `{ message }`. Treat any non-200 as a
>   transient failure and keep the previous cached state.
>
> ### Product scoping (why `product` matters)
> Several products are sold through the same store and share one signing secret.
> The server accepts, for `product: "narik"`, only:
> * **`Narik`** — the NARIK Edition license (annual by default; trials are issued
>   manually for 7/30 days)
> * **`Unbxd`** — the internal lifetime license, valid in every product
>
> An `Essential`/`Premium` key (OPENXMLJSON) is rejected with a clear reason.
> As a local backstop, after a successful response **also verify**
> `["Narik", "Unbxd"].includes(tier)` before treating the app as licensed —
> so a stale cached entitlement can't unlock NARIK either.
>
> ### Where the call runs
> * Do the request in the **main process** (never the renderer): keep
>   `contextIsolation: true`, `nodeIntegration: false`, `sandbox: true`, and
>   expose `activate(email, key)` / `getLicense()` through a typed
>   `contextBridge` preload API.
> * Use Node's built-in `fetch`/`https` — no secrets are involved, the endpoint
>   is public, and **nothing secret may ship in the app**.
>
> ### Caching
> * On success, cache `{ valid, tier, email, expires_at }` locally:
>   **OS keychain** (`safeStorage` or `keytar`) with a JSON-file fallback under
>   `app.getPath('userData')`.
> * Re-verify at most **once every 24 h** while a license is active; use the
>   cache in between and when offline.
> * **`expires_at` empty ⇒ lifetime key ⇒ cache permanently** and never
>   re-verify (this is how internal `Unbxd` keys behave).
> * Clear the cache on "Sign out" / "Remove license".
>
> ### UI
> * **Activate dialog**: Email + License key fields, a **Verify** button
>   (Enter submits), an inline status line, plus **Buy / Manage** (opens the
>   store in the default browser) and **Close**. Run the request off the UI
>   thread and disable Verify while it's in flight.
> * The key is 29 characters, `XXXX-XXXX-XXXX-XXXX-XXXX-XXXX`. Accept it
>   **case-insensitively and with or without dashes/spaces** (the server is
>   lenient) — trim before sending.
> * **Badge**: show the state somewhere persistent (title bar or status bar) —
>   e.g. *Trial* (red) when unlicensed, **NARIK Edition** when licensed. Show
>   the account email and `Valid until <date>` (or **Lifetime**) on an
>   about/membership panel.
>
> ### Gate
> * Decide one clear limitation for the unlicensed state (mirroring
>   OPENXMLJSON's approach: a file-size / feature cap, not a countdown), show an
>   upsell at the moment the user hits it, and remove it entirely once
>   `valid && tier ∈ {Narik, Unbxd}`.
> * An expired license degrades back to the limited state — never hard-lock or
>   discard user work.
>
> ### Acceptance
> * A NARIK key activates; an OPENXMLJSON key is refused with the server's
>   reason; a key issued for another email is refused.
> * A 7-day trial key works and stops working after expiry.
> * Activation survives restart (cached) and works offline afterwards.
> * No secret, signing key, or Shopify credential exists anywhere in the app.

---

## Notes for whoever wires this up

* **Test keys** (generate on the licensing repo, they work immediately):
  ```bash
  cd server
  LICENSE_SIGNING_SECRET=… node scripts/sign-key.mjs tester@acme.com Narik 7 \
      --company "Acme Corp"
  ```
* **Purchases are automatic**: a paid Shopify order whose product title contains
  "NARIK" triggers the webhook, which mints an annual key and emails it to the
  buyer. NARIK's client does nothing in that flow.
* **TLS**: Node ships its own CA bundle, so Electron has none of the certificate
  trouble the Python client hit on Windows.
* A client-side gate is a conversion mechanism, not DRM — it can be patched out
  of any local binary. Keep the check simple and don't over-invest.
