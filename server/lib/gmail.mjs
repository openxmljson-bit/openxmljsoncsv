// Send the license-key email via Gmail SMTP (nodemailer).
//
// Env: GMAIL_USER (the Gmail address), GMAIL_APP_PASSWORD (16-char App
// Password, NOT the account password). Requires 2-Step Verification on the
// account. Emails are sent FROM the Gmail address (Gmail can't spoof a custom
// domain). ~500 sends/day — plenty for license delivery.

import nodemailer from "nodemailer";

let _transport = null;

function transport() {
  if (_transport) return _transport;
  const user = process.env.GMAIL_USER;
  const pass = (process.env.GMAIL_APP_PASSWORD || "").replace(/\s+/g, "");
  if (!user || !pass) {
    throw new Error("GMAIL_USER / GMAIL_APP_PASSWORD are not set");
  }
  _transport = nodemailer.createTransport({
    service: "gmail",
    auth: { user, pass },
  });
  return _transport;
}

export async function sendLicenseEmail({ to, name, tier, key, expiresAt, days }) {
  const user = process.env.GMAIL_USER;
  const greeting = name ? `Hi ${name},` : "Hi,";
  const period = days === 365 ? "annual" : days === 30 ? "monthly" : "";
  const planLine = tier
    ? `Plan: ${tier}${period ? ` (${period} subscription)` : ""}`
    : "";
  const validLine = expiresAt
    ? `Valid until: ${expiresAt}`
    : "";
  const text =
`${greeting}

Thank you for purchasing OPENXMLJSON!

${planLine}
${validLine}

Your license key:

${key}

To activate:
  1. Open OPENXMLJSON.
  2. Go to Help > Activate...
  3. On the "License key" tab, enter this key and the email you used to
     purchase (${to}), then click Verify.
${period === "monthly"
  ? "\nThis is a monthly plan — you'll receive a new key each renewal.\n"
  : ""}
Keep this email for your records. If you have any trouble, just reply here.

— OPENXMLJSON`;

  await transport().sendMail({
    from: `OPENXMLJSON <${user}>`,
    to,
    subject: "Your OPENXMLJSON license key",
    text,
  });
}
