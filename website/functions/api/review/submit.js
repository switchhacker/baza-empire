/**
 * Cloudflare Pages Function — POST /api/review/submit
 *
 * Receives a first-party review from the QR-code "Leave a Review" page
 * (website/review/index.html) and:
 *   1. stores it (D1 table `reviews`, pending moderation), and/or
 *   2. forwards it to the dashboard's existing review pipeline
 *      (env.REVIEW_SUBMIT_UPSTREAM → /api/review/submit) so it shows up in the
 *      Reviews tab for moderation exactly like today.
 *
 * New reviews default to unpublished — they only appear on the site after
 * they're approved (same moderation flow the business already uses).
 *
 * Bindings:
 *   env.DB                    — optional D1 (see schema below)
 *   env.REVIEW_SUBMIT_UPSTREAM— optional dashboard endpoint to forward to
 *   env.REVIEW_TOKEN          — optional bearer for the upstream
 *
 * D1 schema:
 *   CREATE TABLE IF NOT EXISTS reviews (
 *     id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
 *     name TEXT, rating INTEGER, text TEXT, service TEXT, location TEXT,
 *     email TEXT, phone TEXT, platform TEXT DEFAULT 'AHB123',
 *     published INTEGER DEFAULT 0, ip TEXT
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

  const rating = Math.max(1, Math.min(5, parseInt(data.stars ?? data.rating, 10) || 0));
  const text = str(data.text || data.review || "");
  if (!rating) return json({ ok: false, error: "please select a star rating" }, 422);

  const review = {
    name: str(data.name || "Anonymous"),
    rating,
    text,
    service: str(data.service),
    location: str(data.location),
    email: str(data.email),
    phone: str(data.phone),
    platform: "AHB123",
    ip: request.headers.get("CF-Connecting-IP") || "",
  };

  if (env.DB) {
    try {
      await env.DB.prepare(
        `INSERT INTO reviews (name, rating, text, service, location, email, phone, platform, published, ip)
         VALUES (?,?,?,?,?,?,?,?,0,?)`
      ).bind(
        review.name, review.rating, review.text, review.service,
        review.location, review.email, review.phone, review.platform, review.ip
      ).run();
    } catch (e) {
      console.error("D1 review insert failed", e);
    }
  }

  if (env.REVIEW_SUBMIT_UPSTREAM) {
    const headers = { "Content-Type": "application/json" };
    if (env.REVIEW_TOKEN) headers["Authorization"] = `Bearer ${env.REVIEW_TOKEN}`;
    context.waitUntil(
      fetch(env.REVIEW_SUBMIT_UPSTREAM, {
        method: "POST",
        headers,
        body: JSON.stringify(review),
      }).catch(() => {})
    );
  }

  return json({ ok: true, message: "Thank you! Your review was submitted." });
}

function str(v) {
  return (typeof v === "string" ? v : v == null ? "" : String(v)).slice(0, 2000).trim();
}
function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...CORS },
  });
}
