// Transactional email via Resend (https://resend.com). Set RESEND_API_KEY and
// OTP_FROM_EMAIL (a verified sender/domain). Swap this file for SendGrid/
// Mailgun/SES by keeping the same sendOtpEmail(email, code) signature.

export async function sendOtpEmail(email, code) {
  const apiKey = process.env.RESEND_API_KEY;
  const from = process.env.OTP_FROM_EMAIL || "OPENXMLJSON <login@openxmljson.com>";
  if (!apiKey) throw new Error("RESEND_API_KEY is not set");

  const resp = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${apiKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      from,
      to: [email],
      subject: `Your OPENXMLJSON sign-in code: ${code}`,
      text: `Your OPENXMLJSON verification code is ${code}. ` +
            `It expires in 10 minutes. If you didn't request this, ignore ` +
            `this email.`,
    }),
  });
  if (!resp.ok) {
    throw new Error(`Email send failed (${resp.status}): ${await resp.text()}`);
  }
}
