// Shopify `orders/paid` webhook -> mint a license key and attach it to the
// order (metafield openxmljson.license_key + note). Hands-off issuance.
//
// Security: verifies Shopify's HMAC signature over the RAW body using
// SHOPIFY_WEBHOOK_SECRET. Always return 200 quickly so Shopify doesn't retry
// on our slow paths; log failures server-side.
//
// Env: SHOPIFY_WEBHOOK_SECRET, LICENSE_SIGNING_SECRET,
//      SHOPIFY_ADMIN_TOKEN, SHOPIFY_STORE
//      (optional) LICENSE_DEFAULT_TIER (default "Pro"),
//                 LICENSE_DEFAULT_DAYS (default "365")

import crypto from "node:crypto";
import { issueKey } from "../../lib/keys.mjs";
import { setOrderLicenseKey } from "../../lib/shopify.mjs";
import { sendLicenseEmail } from "../../lib/gmail.mjs";

function verifyShopifyHmac(rawBody, headerHmac, secret) {
  if (!secret || !headerHmac) return false;
  const digest = crypto.createHmac("sha256", secret)
    .update(rawBody, "utf8").digest("base64");
  const a = Buffer.from(digest);
  const b = Buffer.from(headerHmac);
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

// Decide the tier from the order's line items. Matches product title / name /
// SKU / variant against "premium" then "essential" (case-insensitive).
// Premium wins if both appear. Falls back to LICENSE_DEFAULT_TIER or Essential.
function deriveTier(order) {
  const items = order.line_items || [];
  const hay = items
    .map((li) => `${li.title || ""} ${li.name || ""} ${li.sku || ""} ` +
      `${li.variant_title || ""}`)
    .join(" ")
    .toLowerCase();
  // NARIK is a separate product (its own app) sold through the same store, so
  // check it first — its title never contains "essential"/"premium".
  if (hay.includes("narik")) return "Narik";
  if (hay.includes("premium")) return "Premium";
  if (hay.includes("essential")) return "Essential";
  return process.env.LICENSE_DEFAULT_TIER || "Essential";
}

// Validity per tier: Essential = monthly, Premium = annual, Narik = annual by
// default. All overridable via env so durations can change without a deploy.
function tierDays(tier) {
  if (tier === "Premium") {
    return parseInt(process.env.PREMIUM_DAYS || "365", 10);
  }
  if (tier === "Narik") {
    return parseInt(process.env.NARIK_DAYS || "365", 10);
  }
  return parseInt(process.env.ESSENTIAL_DAYS || "30", 10);
}

//: Human-facing plan name used in the email ("Narik" -> "NARIK Edition").
function planLabel(tier) {
  if (tier === "Narik") return "NARIK Edition";
  if (tier === "Unbxd") return "Netcore Unbxd";
  return tier;
}

export default async (req) => {
  if (req.method !== "POST") return new Response("Method not allowed", { status: 405 });

  // Raw body is required for HMAC — read text, verify, THEN parse.
  const raw = await req.text();
  const hmac = req.headers.get("x-shopify-hmac-sha256");
  if (!verifyShopifyHmac(raw, hmac, process.env.SHOPIFY_WEBHOOK_SECRET)) {
    return new Response("Invalid signature", { status: 401 });
  }

  let order;
  try {
    order = JSON.parse(raw);
  } catch {
    return new Response("Bad JSON", { status: 400 });
  }

  try {
    const email = (order.email || order.contact_email ||
      order.customer?.email || "").toLowerCase();
    if (!email) {
      console.warn("orders-paid: order has no email; skipping", order.id);
      return new Response("ok", { status: 200 });   // ack; nothing to do
    }

    // Tier comes from what was purchased (Essential / Premium / NARIK); its
    // validity follows the plan: Essential monthly, Premium and NARIK annual.
    const tier = deriveTier(order);
    const days = tierDays(tier);

    const secret = process.env.LICENSE_SIGNING_SECRET;
    if (!secret) throw new Error("LICENSE_SIGNING_SECRET is not set");
    const key = issueKey({ email, tier, days }, secret);
    const expiresAt = new Date(Date.now() + days * 86_400_000)
      .toISOString().slice(0, 10);   // YYYY-MM-DD

    // Email the key to the buyer — this is the primary delivery path. The
    // email shows the human-facing plan name ("NARIK Edition"), while the key
    // itself encodes the canonical tier ("Narik").
    const name = order.customer?.first_name || "";
    await sendLicenseEmail({
      to: email, name, tier: planLabel(tier), key, expiresAt, days,
      product: tier === "Narik" ? "NARIK" : "OPENXMLJSON",
    });
    console.log(
      `orders-paid: emailed ${tier} key (${days}d, exp ${expiresAt}) to ` +
      `${email} (order ${order.id})`);

    // Also record it on the order (metafield) as a best-effort backup so it's
    // visible in admin. A failure here must NOT stop delivery, since the email
    // already went out — so swallow its error separately.
    try {
      const gid = order.admin_graphql_api_id ||
        `gid://shopify/Order/${order.id}`;
      await setOrderLicenseKey(gid, key);
    } catch (metaErr) {
      console.warn("orders-paid: metafield write failed (email sent):", metaErr);
    }
  } catch (err) {
    // Log, but still 200 so Shopify doesn't hammer retries; you can re-issue
    // manually with sign-key if needed.
    console.error("orders-paid:", err);
  }
  return new Response("ok", { status: 200 });
};
