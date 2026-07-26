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

function verifyShopifyHmac(rawBody, headerHmac, secret) {
  if (!secret || !headerHmac) return false;
  const digest = crypto.createHmac("sha256", secret)
    .update(rawBody, "utf8").digest("base64");
  const a = Buffer.from(digest);
  const b = Buffer.from(headerHmac);
  return a.length === b.length && crypto.timingSafeEqual(a, b);
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

    // Tier/duration: customize by mapping line-item title or SKU. Defaults
    // apply to every paid order for now.
    const tier = process.env.LICENSE_DEFAULT_TIER || "Pro";
    const days = parseInt(process.env.LICENSE_DEFAULT_DAYS || "365", 10);

    const secret = process.env.LICENSE_SIGNING_SECRET;
    if (!secret) throw new Error("LICENSE_SIGNING_SECRET is not set");
    const key = issueKey({ email, tier, days }, secret);

    // Attach to the order so you can email it (via notification/Flow) or read
    // it in admin. admin_graphql_api_id is the order's GID.
    const gid = order.admin_graphql_api_id ||
      `gid://shopify/Order/${order.id}`;
    await setOrderLicenseKey(gid, key);

    console.log(`orders-paid: issued ${tier} key for ${email} (order ${order.id})`);
  } catch (err) {
    // Log, but still 200 so Shopify doesn't hammer retries; you can re-issue
    // manually with sign-key if needed.
    console.error("orders-paid:", err);
  }
  return new Response("ok", { status: 200 });
};
