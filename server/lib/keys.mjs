// HMAC-signed license keys (stateless, server-verified).
//
// A key is  base64url(payload) + "." + base64url(HMAC-SHA256(payload,secret)).
// Payload JSON: { email, tier, iat, exp }.  The signing secret lives ONLY on
// the server (LICENSE_SIGNING_SECRET), so keys can be issued at purchase time
// and verified here without a database. Verification checks the signature and
// the expiry.

import crypto from "node:crypto";

function b64url(buf) {
  return Buffer.from(buf).toString("base64url");
}

function signPayload(payloadObj, secret) {
  const payload = b64url(JSON.stringify(payloadObj));
  const sig = crypto.createHmac("sha256", secret).update(payload).digest();
  return `${payload}.${b64url(sig)}`;
}

// Issue a key valid for `days` (0 = no expiry).
export function issueKey({ email, tier = "Pro", days = 365 }, secret) {
  const now = Math.floor(Date.now() / 1000);
  const payload = {
    email: String(email || "").toLowerCase(),
    tier,
    iat: now,
    exp: days === 0 ? 0 : now + days * 86400,  // 0 = never expires
  };
  return signPayload(payload, secret);
}

// Verify a key. Returns { valid, tier, email, expires_at, reason }.
export function verifyKey(key, secret, { email } = {}) {
  const fail = (reason) => ({ valid: false, reason });
  if (!key || typeof key !== "string" || !key.includes(".")) {
    return fail("Malformed license key.");
  }
  const [payload, sig] = key.split(".");
  const expected = b64url(
    crypto.createHmac("sha256", secret).update(payload).digest());
  // Constant-time compare.
  const a = Buffer.from(sig || "");
  const b = Buffer.from(expected);
  if (a.length !== b.length || !crypto.timingSafeEqual(a, b)) {
    return fail("Invalid license key.");
  }
  let claims;
  try {
    claims = JSON.parse(Buffer.from(payload, "base64url").toString("utf8"));
  } catch {
    return fail("Corrupt license key.");
  }
  const now = Math.floor(Date.now() / 1000);
  if (claims.exp && claims.exp < now) return fail("License key expired.");
  if (email && claims.email &&
      claims.email !== String(email).toLowerCase()) {
    return fail("This key was issued for a different email.");
  }
  return {
    valid: true,
    tier: claims.tier || "Pro",
    email: claims.email || "",
    expires_at: claims.exp ? new Date(claims.exp * 1000).toISOString() : "",
    reason: "Valid license key",
  };
}
