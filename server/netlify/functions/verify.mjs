// POST /verify
//   { email, code }        -> OTP mode: verify emailed code, then Shopify lookup
//   { email, licenseKey }  -> key mode: verify signed key (no Shopify needed)
// Returns { valid, tier, email, status, expires_at, reason }.

import { verifyCode } from "../../lib/otp.mjs";
import { checkEntitlementByEmail } from "../../lib/shopify.mjs";
import { verifyKey } from "../../lib/keys.mjs";

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

  try {
    // -- License-key mode (self-contained, signature-verified) -------------
    if (licenseKey) {
      const secret = process.env.LICENSE_SIGNING_SECRET;
      if (!secret) throw new Error("LICENSE_SIGNING_SECRET is not set");
      const result = verifyKey(licenseKey, secret, { email });
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
