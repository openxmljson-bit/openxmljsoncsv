// Issue a license key by hand — for trials, comps, internal staff, or
// re-issuing after a support request. (Purchases are handled automatically by
// the orders/paid webhook.)
//
//   LICENSE_SIGNING_SECRET=… node scripts/sign-key.mjs <email> [tier] [days] \
//       [--company "Acme Corp"] [--note "POC via Ravi"]
//
// tier : Essential | Premium | Unbxd | Narik      (default: Essential)
// days : number of days the key is valid, or 0 for a lifetime key
//        (default: 365)
//
// --company / --note are OPTIONAL and are recorded in a local issuance log
// (issued-keys.jsonl, gitignored) so you can see who each key went to. They are
// NOT encoded in the key: the key is a fixed 15 bytes (version, tier, expiry,
// email fingerprint, HMAC), which is what keeps it 29 characters — carrying a
// company string would make it long and unwieldy. The app therefore shows the
// buyer's EMAIL, not the company.
//
// Examples
//   # NARIK 7-day trial for a company
//   node scripts/sign-key.mjs tester@acme.com Narik 7 --company "Acme Corp"
//   # NARIK 30-day trial
//   node scripts/sign-key.mjs tester@acme.com Narik 30
//   # OPENXMLJSON monthly / annual
//   node scripts/sign-key.mjs buyer@acme.com Essential 30 --company "Acme Corp"
//   node scripts/sign-key.mjs buyer@acme.com Premium 365
//   # internal lifetime (works in every product)
//   node scripts/sign-key.mjs staff@netcoreunbxd.com Unbxd 0

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { issueKey, verifyKey } from "../lib/keys.mjs";

const TIERS = ["Essential", "Premium", "Unbxd", "Narik"];

const secret = process.env.LICENSE_SIGNING_SECRET;
if (!secret) {
  console.error("Set LICENSE_SIGNING_SECRET first.");
  process.exit(1);
}

// Split positional args from --flags so the flags can appear anywhere.
const argv = process.argv.slice(2);
const positional = [];
const flags = {};
for (let i = 0; i < argv.length; i += 1) {
  const a = argv[i];
  if (a.startsWith("--")) {
    const [name, inline] = a.slice(2).split("=");
    flags[name] = inline !== undefined ? inline : (argv[++i] ?? "");
  } else {
    positional.push(a);
  }
}
const [email, tierArg = "Essential", daysArg = "365"] = positional;
const company = (flags.company || "").trim();
const note = (flags.note || "").trim();

if (!email || !email.includes("@")) {
  console.error("Usage: sign-key.mjs <email> [tier] [days] "
    + '[--company "Acme Corp"] [--note "..."]');
  console.error(`       tier: ${TIERS.join(" | ")}   days: number, 0 = lifetime`);
  process.exit(1);
}

// Accept any capitalization ("narik" -> "Narik") but reject unknown tiers
// outright — silently falling back to Essential would hand out the wrong key.
const tier = TIERS.find((t) => t.toLowerCase() === tierArg.toLowerCase());
if (!tier) {
  console.error(`Unknown tier "${tierArg}". Valid: ${TIERS.join(", ")}`);
  process.exit(1);
}

const days = Number.parseInt(daysArg, 10);
if (!Number.isFinite(days) || days < 0) {
  console.error(`Invalid days "${daysArg}" (use a positive number, or 0 for `
    + "lifetime).");
  process.exit(1);
}

const key = issueKey({ email, tier, days }, secret);
const check = verifyKey(key, secret, { email });   // sanity-check what we hand out

//: Which product this tier unlocks — handy in the log when several products
//: share the store and the signing secret.
const PRODUCT_OF = {
  Essential: "OPENXMLJSON", Premium: "OPENXMLJSON",
  Narik: "NARIK", Unbxd: "internal (all products)",
};

// Append an issuance record. The key can't carry a company name (it's a fixed
// 15 bytes), so this local log is the record of who received what.
const here = path.dirname(fileURLToPath(import.meta.url));
const logPath = path.join(here, "..", "issued-keys.jsonl");
try {
  fs.appendFileSync(logPath, JSON.stringify({
    issued_at: new Date().toISOString(),
    email,
    company: company || null,
    note: note || null,
    tier,
    product: PRODUCT_OF[tier] || "",
    days,
    expires_at: days === 0 ? null : check.expires_at,
    key,
  }) + "\n", "utf8");
} catch (err) {
  console.error(`  (warning: couldn't write ${logPath}: ${err.message})`);
}

// The key goes to stdout (pipe/copy friendly); the summary to stderr.
console.log(key);
console.error(
  `  tier=${check.tier} (${PRODUCT_OF[tier] || "?"})  email=${email}` +
  (company ? `  company="${company}"` : "") +
  (note ? `  note="${note}"` : "") +
  "  " +
  (days === 0
    ? "valid=lifetime"
    : `valid=${days}d (until ${check.expires_at.slice(0, 10)})`) +
  `\n  logged to ${path.relative(process.cwd(), logPath)}`);
