/**
 * Cloudflare Pages Function — POST /api/chat
 *
 * Backend for the Nova Sterling chat widget (assets/nova-chat.js). Relays the
 * visitor conversation to the live Nova agent and returns her reply.
 *
 * In production, set env.NOVA_WEBHOOK to the Baza endpoint that runs Nova
 * (nova_sterling) — e.g. a small HTTP shim in front of the dashboard/agent
 * runtime. This function forwards the message + history and passes Nova's reply
 * straight back to the browser. When a lead is captured, Nova's own skills
 * (artifact_save / DISPATCH to Rex or Simon) handle the hand-off server-side.
 *
 * Bindings (Pages → Settings → Functions):
 *   env.NOVA_WEBHOOK  — URL of the Nova agent chat endpoint (optional)
 *   env.NOVA_TOKEN    — bearer token for that endpoint (optional)
 *   env.DB            — D1, to log transcripts / leads (optional)
 *
 * Request  JSON: { sessionId, message, history:[{role,text}], lead:{name,phone,email}, source }
 * Response JSON: { reply, quick?:[string] }
 *
 * If NOVA_WEBHOOK is not configured (e.g. before cutover), the widget's own
 * built-in fallback keeps the conversation warm — so this endpoint simply
 * signals that, and the client handles it gracefully.
 */

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};

export async function onRequestOptions() {
  return new Response(null, { headers: CORS });
}

export async function onRequestPost({ request, env }) {
  let data;
  try {
    data = await request.json();
  } catch {
    return json({ error: "invalid JSON" }, 400);
  }

  const message = String(data.message || "").slice(0, 2000);
  if (!message) return json({ error: "empty message" }, 422);

  // No live agent wired yet → let the widget use its built-in fallback.
  if (!env.NOVA_WEBHOOK) {
    return json({ reply: null, fallback: true });
  }

  // Relay to the live Nova agent.
  try {
    const headers = { "Content-Type": "application/json" };
    if (env.NOVA_TOKEN) headers["Authorization"] = `Bearer ${env.NOVA_TOKEN}`;

    const upstream = await fetch(env.NOVA_WEBHOOK, {
      method: "POST",
      headers,
      body: JSON.stringify({
        agent: "nova_sterling",
        sessionId: data.sessionId || "",
        message,
        history: Array.isArray(data.history) ? data.history.slice(-20) : [],
        lead: data.lead || {},
        source: data.source || "ahb123-web",
        ip: request.headers.get("CF-Connecting-IP") || "",
      }),
    });

    if (!upstream.ok) return json({ reply: null, fallback: true });
    const out = await upstream.json();

    return json({
      reply: out.reply || out.message || null,
      quick: Array.isArray(out.quick) ? out.quick : undefined,
      fallback: !out.reply && !out.message,
    });
  } catch {
    // network hiccup — keep the visitor's chat alive via the client fallback
    return json({ reply: null, fallback: true });
  }
}

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...CORS },
  });
}
