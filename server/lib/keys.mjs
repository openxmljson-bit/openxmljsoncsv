// Compact, stateless, HMAC-signed license keys.
//
// Format (15 bytes -> 24 Crockford-base32 chars, grouped as
// XXXX-XXXX-XXXX-XXXX-XXXX-XXXX):
//   header (7 bytes):
//     [0]   version (1)
//     [1]   tier code (index into TIERS)
//     [2-3] expiry as days-since-epoch (uint16 BE); 0 = never expires
//     [4-6] first 3 bytes of sha256(email) — binds the key to the buyer
//   signature (8 bytes): first 8 bytes of HMAC-SHA256(header, secret)
//
// The signing secret (LICENSE_SIGNING_SECRET) lives only on the server, so a
// key can be issued and verified without any storage. 64-bit truncated HMAC is
// ample for a purchase gate (forging needs ~2^64 work and it's only a paywall).

import crypto from "node:crypto";

const VERSION = 1;
// Index-stable: never reorder/remove (the index is encoded in the key).
// Append new tiers at the end. Unknown tier -> index 0 (Essential).
//   Unbxd = internal Netcore Unbxd lifetime license (issued with days=0).
const TIERS = ["Essential", "Premium", "Unbxd"];
const CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"; // no I L O U
const DAY_MS = 86_400_000;

function tierCode(tier) {
  const i = TIERS.indexOf(tier);
  return i < 0 ? 0 : i;
}

function emailHash3(email) {
  return crypto.createHash("sha256")
    .update(String(email || "").toLowerCase()).digest().subarray(0, 3);
}

function b32encode(bytes) {
  let bits = 0, value = 0, out = "";
  for (const b of bytes) {
    value = (value << 8) | b;
    bits += 8;
    while (bits >= 5) {
      out += CROCKFORD[(value >>> (bits - 5)) & 31];
      bits -= 5;
    }
  }
  if (bits > 0) out += CROCKFORD[(value << (5 - bits)) & 31];
  return out;
}

function b32decode(str) {
  // Lenient: uppercase, strip dashes/spaces, map ambiguous chars.
  const clean = str.toUpperCase().replace(/[^0-9A-Z]/g, "")
    .replace(/O/g, "0").replace(/[IL]/g, "1").replace(/U/g, "V");
  let bits = 0, value = 0;
  const out = [];
  for (const ch of clean) {
    const idx = CROCKFORD.indexOf(ch);
    if (idx < 0) throw new Error("bad character");
    value = (value << 5) | idx;
    bits += 5;
    if (bits >= 8) {
      out.push((value >>> (bits - 8)) & 0xff);
      bits -= 8;
    }
  }
  return Buffer.from(out);
}

function group(str) {
  return str.match(/.{1,4}/g).join("-");
}

function buildHeader(tier, expDays, email) {
  const h = Buffer.alloc(7);
  h[0] = VERSION;
  h[1] = tierCode(tier);
  h.writeUInt16BE(expDays & 0xffff, 2);
  emailHash3(email).copy(h, 4);
  return h;
}

function sign(header, secret) {
  return crypto.createHmac("sha256", secret).update(header).digest()
    .subarray(0, 8);
}

// Issue a key valid for `days` (0 = never expires).
export function issueKey({ email, tier = "Pro", days = 365 }, secret) {
  const expDays = days === 0
    ? 0
    : Math.floor((Date.now() + days * DAY_MS) / DAY_MS);
  const header = buildHeader(tier, expDays, email);
  const full = Buffer.concat([header, sign(header, secret)]); // 15 bytes
  return group(b32encode(full));
}

// Verify a key. Returns { valid, tier, expires_at, reason }.
export function verifyKey(key, secret, { email } = {}) {
  const fail = (reason) => ({ valid: false, reason });
  let raw;
  try {
    raw = b32decode(String(key || ""));
  } catch {
    return fail("Malformed license key.");
  }
  if (raw.length !== 15) return fail("Malformed license key.");
  const header = raw.subarray(0, 7);
  const sig = raw.subarray(7);
  if (header[0] !== VERSION) return fail("Unsupported license key version.");

  const expected = sign(header, secret);
  if (sig.length !== expected.length || !crypto.timingSafeEqual(sig, expected)) {
    return fail("Invalid license key.");
  }

  const expDays = header.readUInt16BE(2);
  if (expDays !== 0 && Math.floor(Date.now() / DAY_MS) > expDays) {
    return fail("License key expired.");
  }
  if (email && !emailHash3(email).equals(header.subarray(4, 7))) {
    return fail("This key was issued for a different email.");
  }

  return {
    valid: true,
    tier: TIERS[header[1]] || "Essential",
    expires_at: expDays ? new Date(expDays * DAY_MS).toISOString() : "",
    reason: "Valid license key",
  };
}
