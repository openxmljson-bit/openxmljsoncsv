// One-time-code generation, storage and rate-limiting via Netlify Blobs.
//
// Codes are stored hashed (never in plaintext) with a short TTL and an attempt
// counter. Rate-limiting caps how often an email can request/verify, so the
// endpoint can't be used to probe which emails are customers.

import crypto from "node:crypto";
import { getStore } from "@netlify/blobs";

const CODE_TTL_MS = 10 * 60 * 1000;       // 10 minutes
const MAX_VERIFY_ATTEMPTS = 5;
const REQUEST_COOLDOWN_MS = 60 * 1000;    // 1 request/min/email
const MAX_REQUESTS_PER_DAY = 10;

function store() {
  return getStore("otp");
}

function hash(value) {
  return crypto.createHash("sha256").update(String(value)).digest("hex");
}

function key(email) {
  return `otp:${hash(email.toLowerCase())}`;
}

function sixDigits() {
  return String(crypto.randomInt(0, 1_000_000)).padStart(6, "0");
}

// Create + persist a code. Returns { code } or throws on rate limit.
export async function createCode(email) {
  const s = store();
  const k = key(email);
  const now = Date.now();
  const existing = await s.get(k, { type: "json" });
  if (existing) {
    if (now - (existing.lastRequest || 0) < REQUEST_COOLDOWN_MS) {
      const err = new Error("Please wait a minute before requesting another code.");
      err.rateLimited = true;
      throw err;
    }
    if ((existing.requestsToday || 0) >= MAX_REQUESTS_PER_DAY &&
        now - (existing.dayStart || 0) < 86400_000) {
      const err = new Error("Too many code requests today. Try again tomorrow.");
      err.rateLimited = true;
      throw err;
    }
  }
  const code = sixDigits();
  const dayActive = existing && now - (existing.dayStart || 0) < 86400_000;
  await s.setJSON(k, {
    codeHash: hash(code),
    expires: now + CODE_TTL_MS,
    attempts: 0,
    lastRequest: now,
    dayStart: dayActive ? existing.dayStart : now,
    requestsToday: dayActive ? (existing.requestsToday || 0) + 1 : 1,
  });
  return { code };
}

// Verify a submitted code. Returns true/false; throws on rate limit.
export async function verifyCode(email, code) {
  const s = store();
  const k = key(email);
  const rec = await s.get(k, { type: "json" });
  if (!rec) return false;
  if (Date.now() > rec.expires) {
    await s.delete(k);
    return false;
  }
  if ((rec.attempts || 0) >= MAX_VERIFY_ATTEMPTS) {
    const err = new Error("Too many attempts. Request a new code.");
    err.rateLimited = true;
    throw err;
  }
  const ok = hash(String(code).trim()) === rec.codeHash;
  if (ok) {
    await s.delete(k);                 // one-time use
    return true;
  }
  rec.attempts = (rec.attempts || 0) + 1;
  await s.setJSON(k, rec);
  return false;
}
