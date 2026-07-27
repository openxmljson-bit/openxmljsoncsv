// Shopify Admin API helper — runs server-side only; uses the secret Admin
// token (env SHOPIFY_ADMIN_TOKEN) to look up a customer's entitlement by
// email. NEVER expose this token to the desktop app.

const API_VERSION = process.env.SHOPIFY_API_VERSION || "2025-07";

function storeDomain() {
  const store = process.env.SHOPIFY_STORE; // e.g. your-store.myshopify.com
  if (!store) throw new Error("SHOPIFY_STORE is not set");
  return store;
}

function adminUrl() {
  return `https://${storeDomain()}/admin/api/${API_VERSION}/graphql.json`;
}

// 2026+ custom apps issue a Client ID + Secret (no static shpat_ token). We
// exchange them for a short-lived Admin API access token via the
// client-credentials grant and cache it in memory. A legacy shpat_ token, if
// provided, is still used directly.
let _tokenCache = { token: null, exp: 0 };

async function getAccessToken() {
  const legacy = process.env.SHOPIFY_ADMIN_TOKEN;
  if (legacy && legacy.startsWith("shpat_")) return legacy;

  const now = Date.now();
  if (_tokenCache.token && now < _tokenCache.exp) return _tokenCache.token;

  const clientId = process.env.SHOPIFY_CLIENT_ID;
  const clientSecret = process.env.SHOPIFY_CLIENT_SECRET;
  if (!clientId || !clientSecret) {
    throw new Error(
      "Set SHOPIFY_CLIENT_ID and SHOPIFY_CLIENT_SECRET (2026 custom apps " +
      "use the client-credentials grant), or a legacy shpat_ token.");
  }
  const resp = await fetch(
    `https://${storeDomain()}/admin/oauth/access_token`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({
        client_id: clientId,
        client_secret: clientSecret,
        grant_type: "client_credentials",
      }),
    });
  if (!resp.ok) {
    throw new Error(`Token grant ${resp.status}: ${await resp.text()}`);
  }
  const data = await resp.json();
  if (!data.access_token) {
    throw new Error("Client-credentials grant returned no access_token");
  }
  const ttlMs = (data.expires_in ? data.expires_in : 3600) * 1000;
  _tokenCache = { token: data.access_token, exp: now + ttlMs - 60_000 };
  return data.access_token;
}

async function adminGraphQL(query, variables) {
  const token = await getAccessToken();
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
  const body = await resp.json();
  // Shopify returns HTTP 200 with a top-level `errors` array for permission
  // problems (e.g. missing write_orders scope), throttling, or bad fields.
  // Surface those instead of silently succeeding.
  if (body && Array.isArray(body.errors) && body.errors.length) {
    const msg = body.errors.map((e) => e.message || JSON.stringify(e))
      .join("; ");
    throw new Error(`Shopify Admin API error: ${msg}`);
  }
  return body;
}

// Escape a value for use inside a Shopify search query string.
function q(value) {
  return String(value).replace(/["\\]/g, "\\$&");
}

// Save a minted license key onto an order as a metafield
// (openxmljson.license_key) and append it to the order note, so you can see it
// in admin and surface it in a notification / Shopify Flow email.
export async function setOrderLicenseKey(orderGid, key) {
  const mutation = `
    mutation($metafields: [MetafieldsSetInput!]!) {
      metafieldsSet(metafields: $metafields) {
        metafields { id namespace key }
        userErrors { field message code }
      }
    }`;
  const variables = {
    metafields: [{
      ownerId: orderGid,
      namespace: "openxmljson",
      key: "license_key",
      type: "single_line_text_field",
      value: key,
    }],
  };
  const res = await adminGraphQL(mutation, variables);
  const result = res?.data?.metafieldsSet;
  const errs = result?.userErrors || [];
  if (errs.length) {
    throw new Error(
      `metafieldsSet: ${errs.map((e) => `${e.message}` +
        (e.code ? ` (${e.code})` : "")).join("; ")}`);
  }
  // Confirm a metafield actually came back — otherwise the write didn't stick.
  if (!result?.metafields?.length) {
    throw new Error(
      "metafieldsSet returned no metafield — likely a missing scope or " +
      "invalid owner. Check the Admin API app has write_orders.");
  }
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
