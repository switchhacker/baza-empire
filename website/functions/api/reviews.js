/**
 * Cloudflare Pages Function — GET /api/reviews
 *
 * Serves the REAL customer reviews for the homepage, merged from every source
 * All Home Building already collects:
 *   • First-party "Leave a Review" (QR-code) submissions — the dashboard's
 *     /api/reviews/published endpoint (JSON files under artifacts/ahb123-reviews)
 *   • Thumbtack + Angi/HomeAdvisor reviews — the dashboard's
 *     /api/ahb/reviews/external endpoint (ahb_reviews table)
 *
 * No review text is ever hard-coded here — this only relays what the business
 * has actually collected. If nothing is wired/returned yet, it responds with an
 * empty list and the homepage gracefully shows the "Our Promise" cards instead.
 *
 * Bindings (Pages → Settings → Functions → Vars):
 *   env.REVIEWS_UPSTREAM           — e.g. https://dash.ahb123.com/api/reviews/published
 *   env.REVIEWS_EXTERNAL_UPSTREAM  — e.g. https://dash.ahb123.com/api/ahb/reviews/external
 *   env.REVIEWS_TOKEN              — optional bearer for those endpoints
 *   env.DB                         — optional D1 fallback (reviews table)
 *
 * Response: { reviews: [ { name, rating, text, platform, date, location } ], count }
 * Cached at the edge for 5 minutes.
 */

const CORS = { "Access-Control-Allow-Origin": "*" };

export async function onRequestGet({ env }) {
  const out = [];

  // 1) first-party (QR) published reviews
  await pull(env.REVIEWS_UPSTREAM, env.REVIEWS_TOKEN, "AHB123", out);
  // 2) external platform reviews (Thumbtack / Angi / HomeAdvisor)
  await pull(env.REVIEWS_EXTERNAL_UPSTREAM, env.REVIEWS_TOKEN, null, out);

  // 3) optional D1 fallback
  if (!out.length && env.DB) {
    try {
      const { results } = await env.DB.prepare(
        "SELECT reviewer_name AS name, rating, review_text AS text, platform, review_date AS date FROM ahb_reviews WHERE COALESCE(rating,5) >= 4 ORDER BY review_date DESC LIMIT 24"
      ).all();
      (results || []).forEach((r) => out.push(normalize(r, r.platform)));
    } catch (e) {
      console.error("D1 reviews query failed", e);
    }
  }

  // newest first, cap to a sensible number for the page
  const reviews = out
    .filter((r) => r.text && Number(r.rating) >= 4)
    .sort((a, b) => (b.date || "").localeCompare(a.date || ""))
    .slice(0, 24);

  return new Response(JSON.stringify({ reviews, count: reviews.length }), {
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": "public, max-age=300",
      ...CORS,
    },
  });
}

async function pull(url, token, defaultPlatform, out) {
  if (!url) return;
  try {
    const headers = {};
    if (token) headers["Authorization"] = `Bearer ${token}`;
    const r = await fetch(url, { headers });
    if (!r.ok) return;
    const data = await r.json();
    const list = Array.isArray(data) ? data : data.reviews || [];
    list.forEach((item) => out.push(normalize(item, defaultPlatform)));
  } catch (e) {
    console.error("reviews upstream failed", url, e);
  }
}

// Normalize the various shapes (first-party JSON vs external table) into one.
function normalize(r, defaultPlatform) {
  const rating = Number(r.rating ?? r.stars ?? r.score ?? 5) || 5;
  return {
    name: str(r.name || r.reviewer_name || r.reviewer || r.customer_name || "Verified customer"),
    rating,
    text: str(r.text || r.review_text || r.review || r.comment || r.body || ""),
    platform: str(r.platform || r.source || defaultPlatform || "AHB123"),
    date: str(r.date || r.review_date || r.created_at || r.submitted_at || ""),
    location: str(r.location || r.city || ""),
  };
}
function str(v) {
  return (typeof v === "string" ? v : v == null ? "" : String(v)).slice(0, 800).trim();
}
