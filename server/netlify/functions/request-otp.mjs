// POST /request-otp  { email }  ->  { ok: true }
// Emails a one-time code. Always returns ok:true for known-shaped emails so it
// can't be used to enumerate which addresses are customers.

import { createCode } from "../../lib/otp.mjs";
import { sendOtpEmail } from "../../lib/email.mjs";

const json = (status, body) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

export default async (req) => {
  if (req.method !== "POST") return json(405, { message: "Use POST." });
  let email;
  try {
    ({ email } = await req.json());
  } catch {
    return json(400, { message: "Invalid JSON body." });
  }
  email = String(email || "").trim().toLowerCase();
  if (!email.includes("@")) return json(400, { message: "Invalid email." });

  try {
    const { code } = await createCode(email);
    await sendOtpEmail(email, code);
  } catch (err) {
    if (err.rateLimited) return json(429, { message: err.message });
    // Don't leak internal errors; log server-side.
    console.error("request-otp:", err);
    return json(500, { message: "Could not send code. Try again later." });
  }
  return json(200, { ok: true });
};
