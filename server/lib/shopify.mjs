// Shopify Admin API helper — runs server-side only; uses the secret Admin
// token (env SHOPIFY_ADMIN_TOKEN) to look up a customer's entitlement by
// email. NEVER expose this token to the desktop app.

const API_VERSION = process.env.SHOPIFY_API_VERSION || "2025-07";

function adminUrl() {
  const store = process.env.SHOPIFY_STORE; // e.g. your-store.myshopify.com
  if (!store) throw new Error("SHOPIFY_STORE is not set");
  return `https://${store}/admin/api/${API_VERSION}/graphql.json`;
}

async function adminGraphQL(query, variables) {
  const token = process.env.SHOPIFY_ADMIN_TOKEN;
  if (!token) throw new Error("SHOPIFY_ADMIN_TOKEN is not set");
  const resp = await fetch(adminUrl(), {
    method: "POST",
    headers: {
      "X-Shopify-Access-Token": token,
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    body: JSON.stringify({ query, variables }),
  });
  if (!resp.ok) {
    throw new Error(`Shopify Admin API ${resp.status}: ${await resp.text()}`);
  }
  return resp.json();
}

// Escape a value for use inside a Shopify search query string.
function q(value) {
  return String(value).replace(/["\\]/g, "\\$&");
}

// Look up entitlement for an email. Returns { valid, tier, status,
// expires_at, reason }. Active subscription wins; otherwise a paid order.
export async function checkEntitlementByEmail(email) {
  const none = {
    valid: false, tier: "", status: "none", expires_at: "",
    reason: "No active subscription or paid order found",
  };
  if (!email) return { ...none, reason: "No email provided" };

  // 1) Active subscription contract for this customer.
  const subQuery = `
    query($q: String!) {
      customers(first: 1, query: $q) {
        edges { node {
          subscriptionContracts(first: 10) {
            edges { node {
              status
              lines(first: 1) { edges { node { title } } }
              nextBillingDate
            } }
          }
        } }
      }
    }`;
  const subs = await adminGraphQL(subQuery, { q: `email:"${q(email)}"` });
  const customer = subs?.data?.customers?.edges?.[0]?.node;
  const contracts = customer?.subscriptionContracts?.edges || [];
  for (const { node } of contracts) {
    if (String(node.status).toUpperCase() === "ACTIVE") {
      const tier = node.lines?.edges?.[0]?.node?.title || "Subscription";
      return {
        valid: true, tier, status: "active",
        expires_at: node.nextBillingDate || "",
        reason: "Active subscription contract",
      };
    }
  }

  // 2) Fallback: any paid order for this email.
  const orderQuery = `
    query($q: String!) {
      orders(first: 5, query: $q, sortKey: PROCESSED_AT, reverse: true) {
        edges { node { displayFinancialStatus processedAt } }
      }
    }`;
  const orders = await adminGraphQL(
    orderQuery, { q: `email:"${q(email)}"` });
  const edges = orders?.data?.orders?.edges || [];
  if (edges.some((e) =>
    String(e.node.displayFinancialStatus).toUpperCase() === "PAID")) {
    return {
      valid: true, tier: "Purchased", status: "paid",
      expires_at: "", reason: "Paid order on record",
    };
  }
  return none;
}
