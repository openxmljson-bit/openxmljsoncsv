// Unit tests for the license-key HMAC logic (node --test).

import assert from "node:assert";
import { test } from "node:test";
import { issueKey, verifyKey } from "../lib/keys.mjs";

const SECRET = "test-secret-please-change";

test("valid key round-trips", () => {
  const key = issueKey({ email: "A@B.com", tier: "Pro", days: 30 }, SECRET);
  const r = verifyKey(key, SECRET, { email: "a@b.com" });
  assert.equal(r.valid, true);
  assert.equal(r.tier, "Pro");
  assert.equal(r.email, "a@b.com");
  assert.ok(r.expires_at);
});

test("tampered payload fails", () => {
  const key = issueKey({ email: "a@b.com" }, SECRET);
  const [p, s] = key.split(".");
  const forged = Buffer.from(
    JSON.stringify({ email: "a@b.com", tier: "Enterprise", exp: 0 }))
    .toString("base64url");
  assert.equal(verifyKey(`${forged}.${s}`, SECRET).valid, false);
  // sanity: original still valid
  assert.equal(verifyKey(`${p}.${s}`, SECRET).valid, true);
});

test("wrong secret fails", () => {
  const key = issueKey({ email: "a@b.com" }, SECRET);
  assert.equal(verifyKey(key, "other-secret").valid, false);
});

test("expired key fails", () => {
  // days<0 -> exp in the past
  const key = issueKey({ email: "a@b.com", days: -1 }, SECRET);
  const r = verifyKey(key, SECRET);
  assert.equal(r.valid, false);
  assert.match(r.reason, /expired/i);
});

test("email mismatch fails", () => {
  const key = issueKey({ email: "a@b.com" }, SECRET);
  assert.equal(verifyKey(key, SECRET, { email: "c@d.com" }).valid, false);
});

test("no-expiry key stays valid", () => {
  const key = issueKey({ email: "a@b.com", days: 0 }, SECRET);
  const r = verifyKey(key, SECRET);
  assert.equal(r.valid, true);
  assert.equal(r.expires_at, "");
});
