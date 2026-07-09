/**
 * Cloudflare Pages Function — POST /api/estimate
 *
 * Server-side ballpark estimator. The Instant Estimate page (v3) can compute a
 * range client-side for instant feedback, but routing it through here means the
 * pricing logic lives in ONE place the Baza agents can tune (or later swap for
 * the dashboard's real estimate engine) without editing the HTML.
 *
 * Request  JSON: { service, size ("small"|"standard"|"large"), quality ("budget"|"mid"|"high") }
 * Response JSON: { ok, low, high, currency, label }
 *
 * Base ranges mirror the client defaults in v3-instant/index.html — keep them in sync,
 * or make this the single source of truth and have the page call it on the reveal step.
 */

const BASE = {
  kitchen:  [15000, 45000],
  bathroom: [8000, 28000],
  roofing:  [7000, 24000],
  addition: [40000, 150000],
  basement: [18000, 55000],
  deck:     [6000, 26000],
};
const SIZE = { small: 0.75, standard: 1, large: 1.35 };
const QUALITY = { budget: 0.85, mid: 1, high: 1.3 };

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};

export async function onRequestOptions() {
  return new Response(null, { headers: CORS });
}

export async function onRequestPost({ request }) {
  let data;
  try {
    data = await request.json();
  } catch {
    return json({ ok: false, error: "invalid JSON" }, 400);
  }

  const service = String(data.service || "").toLowerCase();
  const base = BASE[service];
  if (!base) {
    return json({ ok: false, error: "unknown service", services: Object.keys(BASE) }, 422);
  }

  const sm = SIZE[String(data.size || "standard").toLowerCase()] ?? 1;
  const q = QUALITY[String(data.quality || "mid").toLowerCase()] ?? 1;

  const low = round500(base[0] * sm * q);
  const high = round500(base[1] * sm * q);

  return json({
    ok: true,
    currency: "USD",
    low,
    high,
    label: `Estimated $${low.toLocaleString()} – $${high.toLocaleString()} for a ${service} project`,
    disclaimer: "Ballpark only. Your free written quote will be exact.",
  });
}

function round500(n) {
  return Math.round(n / 500) * 500;
}
function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...CORS },
  });
}
