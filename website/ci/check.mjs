#!/usr/bin/env node
/**
 * Lightweight, dependency-free CI checker for the website/ folder.
 *
 * Runs three deterministic, offline checks over every page:
 *   1. JS syntax — every .js file and every inline <script> block compiles.
 *   2. Internal links — every relative href/src resolves to a real file on disk.
 *   3. Duplicate ids — no id="" appears twice within a single HTML document.
 *
 * No network, no npm install — safe and fast in CI. Exit code 1 on any error.
 *
 * Usage:  node website/ci/check.mjs   (run from repo root)
 */
import { readFileSync, existsSync, statSync, readdirSync } from "node:fs";
import { join, dirname, resolve, extname } from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), ".."); // website/
const errors = [];
const rel = (p) => p.slice(ROOT.length + 1);

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    const s = statSync(p);
    if (s.isDirectory()) {
      if (name === "node_modules" || name === ".git") continue;
      walk(p, out);
    } else out.push(p);
  }
  return out;
}

const files = walk(ROOT);
const htmlFiles = files.filter((f) => extname(f) === ".html");
// Shipped JS only — the ci/ folder is Node tooling (ESM imports), validated by running it.
const jsFiles = files.filter(
  (f) => (extname(f) === ".js" || extname(f) === ".mjs") && !rel(f).startsWith("ci/")
);

// ---------- 1. JS syntax ----------
function checkJs(code, label) {
  try {
    new vm.Script(code, { filename: label });
  } catch (e) {
    errors.push(`JS syntax: ${label} — ${e.message}`);
  }
}
for (const f of jsFiles) {
  // Pages Functions use ESM export syntax → wrap so vm can compile it.
  const src = readFileSync(f, "utf8");
  const wrapped = "(async()=>{" + src.replace(/\bexport\s+(async\s+function|function|const|let|var|default)/g, "$1") + "})";
  checkJs(wrapped, rel(f));
}
for (const f of htmlFiles) {
  const html = readFileSync(f, "utf8");
  const re = /<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/gi;
  let m, i = 0;
  while ((m = re.exec(html))) checkJs(m[1], `${rel(f)} <script#${++i}>`);
}

// ---------- 2. internal links ----------
const SKIP = /^(https?:|\/\/|mailto:|tel:|data:|javascript:|#)/i;
for (const f of htmlFiles) {
  const html = readFileSync(f, "utf8");
  const dir = dirname(f);
  const re = /\b(?:href|src)\s*=\s*"([^"]+)"/gi;
  let m;
  while ((m = re.exec(html))) {
    let target = m[1].trim();
    if (!target || SKIP.test(target)) continue;
    target = target.split("#")[0].split("?")[0];
    if (!target) continue;
    const base = target.startsWith("/") ? ROOT : dir;
    let p = join(base, target.replace(/^\//, ""));
    if (existsSync(p) && statSync(p).isDirectory()) p = join(p, "index.html");
    if (!existsSync(p)) errors.push(`Broken link: ${rel(f)} → "${m[1]}" (missing ${rel(p)})`);
  }
}

// ---------- 3. duplicate ids ----------
for (const f of htmlFiles) {
  const html = readFileSync(f, "utf8");
  const seen = new Map();
  const re = /\bid\s*=\s*"([^"]+)"/gi;
  let m;
  while ((m = re.exec(html))) seen.set(m[1], (seen.get(m[1]) || 0) + 1);
  for (const [id, n] of seen) if (n > 1) errors.push(`Duplicate id: ${rel(f)} — id="${id}" used ${n}×`);
}

// ---------- report ----------
console.log(`Checked ${htmlFiles.length} HTML + ${jsFiles.length} JS files under website/`);
if (errors.length) {
  console.error(`\n✗ ${errors.length} problem(s):`);
  for (const e of errors) console.error("  - " + e);
  process.exit(1);
}
console.log("✓ All checks passed (JS syntax, internal links, duplicate ids).");
