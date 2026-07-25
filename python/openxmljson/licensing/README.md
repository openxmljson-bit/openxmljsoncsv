# Licensing (desktop side)

Client for the OPENXMLJSON licensing endpoint (`server/`, deployed to
`api.openxmljson.com`). The app never contacts Shopify directly and embeds no
secret — it calls our backend and shows read-only status.

## Modules

| File | Role |
|------|------|
| `config.py` | `ApiConfig.from_env()` — base URL, store URL, timeout, cache TTL |
| `client.py` | `LicenseClient`: `request_otp`, `verify_otp`, `verify_key` → `Entitlement` |
| `cache.py` | local entitlement cache (keyring, file fallback) with TTL |
| `ui.py` | `LicenseDialog` (email+code / key), `ensure_licensed()` |
| `oauth.py`, `pkce.py`, `entitlement.py` | **unused** alternative Shopify hosted-OAuth path; safe to delete |

## Usage

```python
from openxmljson.licensing.ui import ensure_licensed

ent = ensure_licensed(parent=main_window)   # cached, else opens the dialog
if ent and ent.valid:
    ...  # unlock
else:
    ...  # soft-gate: keep running, or open store
```

`ensure_licensed` returns a cached valid `Entitlement` without prompting; pass
`force=True` to always re-check. It never hard-blocks — the caller decides
whether to soft- or hard-gate.

## Configuration (env, all optional)

| Var | Default |
|-----|---------|
| `OXJ_API_BASE` | `https://api.openxmljson.com` |
| `OXJ_STORE_URL` | `https://www.openxmljson.com` |
| `OXJ_API_TIMEOUT` | `20` |
| `OXJ_LICENSE_CACHE_HOURS` | `24` |

## Notes

- Not wired into app startup — import and call it where you want the gate.
- Network runs on a background thread (`QThreadPool`); the UI never freezes.
- `keyring` is optional. If installed, the cached status is stored in the OS
  keychain; otherwise it falls back to `~/.config/OPENXMLJSON/license.json`.
  Add `keyring` to `pyproject.toml` when you wire this in if you want the
  keychain path in the shipped app.
- Client-side gates are a deterrent, not DRM. Keep `cache_hours` modest so a
  cancelled subscription is re-checked reasonably soon.
```
