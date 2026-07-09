/**
 * Cloudflare Pages Function — POST /api/lead
 *
 * Captures a lead from any of the site's estimate forms and:
 *   1. (optional) verifies a Cloudflare Turnstile token to block spam bots
 *   2. writes the lead into a D1 database (table: leads)
 *   3. (optional) forwards the lead to the Baza agent webhook so an agent can
 *      score/assign/follow-up, and/or emails the office
 *
 * Bindings to configure in the Pages project (Settings → Functions):
 *   env.DB              — D1 database binding (see schema below)
 *   env.LEADS_WEBHOOK   — (optional) URL the Baza agents listen on
 *   env.TURNSTILE_SECRET— (optional) Turnstile secret key
 *
 * D1 schema (run once with `wrangler d1 execute ahb123 --command "..."`):
 *   CREATE TABLE IF NOT EXISTS leads (
 *     id INTEGER PRIMARY KEY AUTOINCREMENT,
 *     created_at TEXT DEFAULT CURRENT_TIMESTAMP,
 *     name TEXT, phone TEXT, email TEXT, zip TEXT,
 *     service TEXT, budget TEXT, timeline TEXT,
 *     estimate TEXT, message TEXT, source TEXT,
 *     ip TEXT, user_agent TEXT, status TEXT DEFAULT 'new'
 *   );
 */

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};

export async function onRequestOptions() {
  return new Response(null, { headers: CORS });
}

export async function onRequestPost(context) {
  const { request, env } = context;

  let data;
  try {
    data = await request.json();
  } catch {
    return json({ ok: false, error: "invalid JSON" }, 400);
  }

  // --- basic validation ---
  const name = str(data.name);
  const phone = str(data.phone);
  const email = str(data.email);
  if (!name || (!phone && !email)) {
    return json({ ok: false, error: "name and a phone or email are required" }, 422);
  }

  // --- optional Turnstile spam check ---
  if (env.TURNSTILE_SECRET && data.turnstileToken) {
    const ok = await verifyTurnstile(env.TURNSTILE_SECRET, data.turnstileToken, request);
    if (!ok) return json({ ok: false, error: "spam check failed" }, 403);
  }

  const lead = {
    name,
    phone,
    email,
    zip: str(data.zip),
    service: str(data.service),
    budget: str(data.budget),
    timeline: str(data.timeline),
    estimate: str(data.estimate),
    message: str(data.message),
    source: str(data.source) || "website",
    ip: request.headers.get("CF-Connecting-IP") || "",
    user_agent: request.headers.get("User-Agent") || "",
  };

  // --- persist to D1 (if bound) ---
  if (env.DB) {
    try {
      await env.DB.prepare(
        `INSERT INTO leads (name, phone, email, zip, service, budget, timeline, estimate, message, source, ip, user_agent)
         VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`
      ).bind(
        lead.name, lead.phone, lead.email, lead.zip, lead.service,
        lead.budget, lead.timeline, lead.estimate, lead.message,
        lead.source, lead.ip, lead.user_agent
      ).run();
    } catch (e) {
      // don't fail the visitor's submission if storage hiccups — log & continue
      console.error("D1 insert failed", e);
    }
  }

  // --- notify the Baza agents / office (fire-and-forget) ---
  if (env.LEADS_WEBHOOK) {
    context.waitUntil(
      fetch(env.LEADS_WEBHOOK, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ type: "new_lead", lead }),
      }).catch(() => {})
    );
  }

  return json({ ok: true, message: "Lead received. A specialist will follow up shortly." });
}

// --- helpers ---
function str(v) {
  return (typeof v === "string" ? v : v == null ? "" : String(v)).slice(0, 500).trim();
}
function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...CORS },
  });
}
async function verifyTurnstile(secret, token, request) {
  const form = new FormData();
  form.append("secret", secret);
  form.append("response", token);
  const ip = request.headers.get("CF-Connecting-IP");
  if (ip) form.append("remoteip", ip);
  try {
    const r = await fetch("https://challenges.cloudflare.com/turnstile/v0/siteverify", {
      method: "POST",
      body: form,
    });
    const out = await r.json();
    return !!out.success;
  } catch {
    return false;
  }
}
