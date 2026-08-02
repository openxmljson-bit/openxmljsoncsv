// Unit tests for the compact license-key logic (node --test).

import assert from "node:assert";
import { test } from "node:test";
import { issueKey, verifyKey } from "../lib/keys.mjs";

const SECRET = "test-secret-please-change";

test("valid key round-trips and is short", () => {
  const key = issueKey({ email: "A@B.com", tier: "Premium", days: 30 }, SECRET);
  assert.ok(key.length <= 34, `key too long: ${key.length}`);
  assert.match(key, /^[0-9A-Z-]+$/); // Crockford + dashes only
  const r = verifyKey(key, SECRET, { email: "a@b.com" });
  assert.equal(r.valid, true);
  assert.equal(r.tier, "Premium");
  assert.ok(r.expires_at);
});

test("dashes/spaces/lowercase are tolerated on input", () => {
  const key = issueKey({ email: "a@b.com" }, SECRET);
  const messy = key.replace(/-/g, "").toLowerCase();
  assert.equal(verifyKey(messy, SECRET, { email: "a@b.com" }).valid, true);
});

test("tampered key fails", () => {
  const key = issueKey({ email: "a@b.com" }, SECRET);
  // Flip one character (first char of the last group).
  const chars = key.split("");
  const i = key.lastIndexOf("-") + 1;
  chars[i] = chars[i] === "0" ? "1" : "0";
  assert.equal(verifyKey(chars.join(""), SECRET).valid, false);
});

test("wrong secret fails", () => {
  const key = issueKey({ email: "a@b.com" }, SECRET);
  assert.equal(verifyKey(key, "other-secret").valid, false);
});

test("expired key fails", () => {
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

test("tier is preserved", () => {
  const key = issueKey({ email: "a@b.com", tier: "Essential", days: 10 }, SECRET);
  assert.equal(verifyKey(key, SECRET, { email: "a@b.com" }).tier, "Essential");
});

test("Narik tier round-trips (NARIK Edition)", () => {
  const key = issueKey({ email: "a@b.com", tier: "Narik", days: 365 }, SECRET);
  const r = verifyKey(key, SECRET, { email: "a@b.com" });
  assert.equal(r.valid, true);
  assert.equal(r.tier, "Narik");
  assert.ok(r.expires_at);
});

test("existing tier indexes are unchanged by the Narik append", () => {
  // Guards the index-stability rule: previously issued keys must still decode
  // to the same tier after TIERS grew.
  for (const tier of ["Essential", "Premium", "Unbxd"]) {
    const key = issueKey({ email: "a@b.com", tier, days: 30 }, SECRET);
    assert.equal(verifyKey(key, SECRET, { email: "a@b.com" }).tier, tier);
  }
});

test("unknown tier falls back to Essential", () => {
  const key = issueKey({ email: "a@b.com", tier: "Nonsense", days: 10 }, SECRET);
  assert.equal(verifyKey(key, SECRET, { email: "a@b.com" }).tier, "Essential");
});
