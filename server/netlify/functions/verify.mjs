// POST /verify
//   { email, code }                    -> OTP mode: emailed code + Shopify lookup
//   { email, licenseKey, product? }    -> key mode: verify signed key
// Returns { valid, tier, email, status, expires_at, reason }.
//
// PRODUCT SEPARATION: several products are sold through the same store and
// share one signing secret, so a key must only unlock the product it was sold
// for. When the caller sends `product`, a key whose tier belongs to a different
// product is rejected here (server-side, so it can't be patched out of a
// client). Omitting `product` keeps the old permissive behavior.

import { verifyKey } from "../../lib/keys.mjs";

//: product -> tiers it accepts. "Unbxd" is the internal lifetime license and
//: is honored by every product.
const PRODUCT_TIERS = {
  openxmljson: ["Essential", "Premium", "Unbxd"],
  narik: ["Narik", "Unbxd"],
};

//: tier -> the product it was sold for (for a helpful error message).
const TIER_PRODUCT = {
  Essential: "OPENXMLJSON",
  Premium: "OPENXMLJSON",
  Narik: "NARIK",
  Unbxd: "internal",
};
// NOTE: the OTP path (verifyCode, checkEntitlementByEmail) is imported lazily
// inside the OTP branch below, so license-key verification pulls in no extra
// dependencies (e.g. @netlify/blobs is only needed when OTP is actually used).

const json = (status, body) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

export default async (req) => {
  if (req.method !== "POST") return json(405, { message: "Use POST." });

  let body;
  try {
    body = await req.json();
  } catch {
    return json(400, { message: "Invalid JSON body." });
  }
  const email = String(body.email || "").trim().toLowerCase();
  const code = body.code ? String(body.code).trim() : "";
  const licenseKey = body.licenseKey ? String(body.licenseKey).trim() : "";
  const product = String(body.product || "").trim().toLowerCase();

  try {
    // -- License-key mode (self-contained, signature-verified) -------------
    if (licenseKey) {
      const secret = process.env.LICENSE_SIGNING_SECRET;
      if (!secret) throw new Error("LICENSE_SIGNING_SECRET is not set");
      const result = verifyKey(licenseKey, secret, { email });

      // Reject a key sold for a different product.
      if (result.valid && product && PRODUCT_TIERS[product]) {
        if (!PRODUCT_TIERS[product].includes(result.tier)) {
          const owner = TIER_PRODUCT[result.tier] || "another product";
          return json(200, {
            valid: false,
            tier: result.tier,
            email: result.email || email,
            status: "none",
            expires_at: result.expires_at || "",
            reason: `This key is for ${owner}, not this application.`,
          });
        }
      }

      return json(200, {
        valid: !!result.valid,
        tier: result.tier || "",
        email: result.email || email,
        status: result.valid ? "active" : "none",
        expires_at: result.expires_at || "",
        reason: result.reason || "",
      });
    }

    // -- OTP mode (email proof, then live Shopify entitlement) -------------
    if (!email.includes("@")) return json(400, { message: "Invalid email." });
    if (!code) return json(400, { message: "Enter the emailed code." });

    // Lazy: only load OTP/Shopify code (and their deps) for the OTP path.
    const { verifyCode } = await import("../../lib/otp.mjs");
    const { checkEntitlementByEmail } = await import("../../lib/shopify.mjs");

    let proven;
    try {
      proven = await verifyCode(email, code);
    } catch (err) {
      if (err.rateLimited) return json(429, { message: err.message });
      throw err;
    }
    if (!proven) {
      return json(200, {
        valid: false, status: "none", email, tier: "", expires_at: "",
        reason: "That code is invalid or expired.",
      });
    }

    const ent = await checkEntitlementByEmail(email);
    return json(200, {
      valid: !!ent.valid,
      tier: ent.tier || "",
      email,
      status: ent.status || (ent.valid ? "active" : "none"),
      expires_at: ent.expires_at || "",
      reason: ent.reason || "",
    });
  } catch (err) {
    console.error("verify:", err);
    return json(500, { message: "Verification failed. Try again later." });
  }
};
