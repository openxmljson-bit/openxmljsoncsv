// Issue a license key. Run at purchase time (or from a Shopify order-paid
// webhook) to hand a customer a key they paste into the app.
//
//   LICENSE_SIGNING_SECRET=... node scripts/sign-key.mjs <email> [tier] [days]
//
// Example:
//   LICENSE_SIGNING_SECRET=xxxx node scripts/sign-key.mjs a@b.com Pro 365

import { issueKey } from "../lib/keys.mjs";

const secret = process.env.LICENSE_SIGNING_SECRET;
if (!secret) {
  console.error("Set LICENSE_SIGNING_SECRET first.");
  process.exit(1);
}
const [email, tier = "Pro", days = "365"] = process.argv.slice(2);
if (!email) {
  console.error("Usage: sign-key.mjs <email> [tier] [days]");
  process.exit(1);
}
const key = issueKey(
  { email, tier, days: parseInt(days, 10) || 0 }, secret);
console.log(key);
