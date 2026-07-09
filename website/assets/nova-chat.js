/**
 * Nova Sterling — client-facing chat widget for ahb123.com
 * Director of Client Relations · All Home Building Co. LLC
 *
 * Self-contained: injects its own styles + DOM, no dependencies. Include once
 * per page before </body>:  <script src="assets/nova-chat.js" defer></script>
 * (use ../assets/nova-chat.js from a subdirectory).
 *
 * It POSTs the conversation to /api/chat (see functions/api/chat.js), which
 * relays to the live Nova agent when configured. If the API is unreachable
 * (e.g. local preview), a warm built-in fallback keeps Nova conversational and
 * still captures a lead.
 */
(function () {
  "use strict";
  var PHONE = "215-554-5488", TEL = "2155545488";
  function el(tag, cls, html) { var e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; }
  var sessionId = "nova-" + Math.random().toString(36).slice(2) + "-" + (window.performance ? Math.round(performance.now()) : 0);
  var history = []; // {role:'nova'|'user', text}
  var lead = { name: "", phone: "", email: "" };
  var fallbackStep = 0;

  // ---------- styles ----------
  var css = `
  .nova-launch{position:fixed;right:20px;bottom:20px;z-index:9998;display:flex;align-items:center;gap:10px;
    background:linear-gradient(120deg,#25c3ef,#1668cf 55%,#f7911e);color:#04121e;border:0;border-radius:40px;
    padding:12px 18px 12px 12px;font:800 14px/1 "Segoe UI",system-ui,sans-serif;cursor:pointer;
    box-shadow:0 12px 30px rgba(37,195,239,.4);transition:transform .15s,box-shadow .2s}
  .nova-launch:hover{transform:translateY(-2px);box-shadow:0 18px 40px rgba(37,195,239,.55)}
  .nova-launch .av{width:34px;height:34px;border-radius:50%;background:#04121e;color:#25c3ef;display:flex;
    align-items:center;justify-content:center;font-weight:900;font-size:16px;flex:0 0 34px}
  .nova-launch .dot{width:9px;height:9px;border-radius:50%;background:#22c55e;box-shadow:0 0 0 2px #04121e;
    position:absolute;left:36px;top:12px}
  .nova-launch.hide{display:none}
  .nova-panel{position:fixed;right:20px;bottom:20px;z-index:9999;width:370px;max-width:calc(100vw - 32px);
    height:560px;max-height:calc(100vh - 40px);background:#0f131d;color:#eaf0f7;border:1px solid rgba(255,255,255,.1);
    border-radius:18px;box-shadow:0 30px 70px rgba(0,0,0,.6);display:none;flex-direction:column;overflow:hidden;
    font-family:"Segoe UI",system-ui,sans-serif}
  .nova-panel.open{display:flex;animation:novaIn .25s ease}
  @keyframes novaIn{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:none}}
  .nova-head{display:flex;align-items:center;gap:12px;padding:16px;background:linear-gradient(120deg,#25c3ef,#1668cf 60%,#f7911e);color:#04121e}
  .nova-head .av{width:42px;height:42px;border-radius:50%;background:#04121e;color:#25c3ef;display:flex;align-items:center;justify-content:center;font-weight:900;font-size:18px}
  .nova-head b{font-size:15px;display:block}.nova-head small{font-size:11.5px;opacity:.85;display:flex;align-items:center;gap:5px}
  .nova-head .on{width:8px;height:8px;border-radius:50%;background:#0a7d2c;display:inline-block}
  .nova-head .x{margin-left:auto;background:rgba(0,0,0,.15);border:0;color:#04121e;width:30px;height:30px;border-radius:8px;cursor:pointer;font-size:18px;font-weight:900}
  .nova-body{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:10px;background:#0b0e14}
  .nova-msg{max-width:82%;padding:11px 14px;border-radius:14px;font-size:14px;line-height:1.5}
  .nova-msg.nova{align-self:flex-start;background:#1a2130;border:1px solid rgba(255,255,255,.07);border-bottom-left-radius:4px}
  .nova-msg.user{align-self:flex-end;background:linear-gradient(120deg,#25c3ef,#1668cf);color:#04121e;font-weight:600;border-bottom-right-radius:4px}
  .nova-typing{align-self:flex-start;display:flex;gap:4px;padding:12px 14px}
  .nova-typing i{width:7px;height:7px;border-radius:50%;background:#5b6577;animation:novaBlink 1.2s infinite}
  .nova-typing i:nth-child(2){animation-delay:.2s}.nova-typing i:nth-child(3){animation-delay:.4s}
  @keyframes novaBlink{0%,60%,100%{opacity:.3}30%{opacity:1}}
  .nova-quick{display:flex;flex-wrap:wrap;gap:6px;padding:0 16px 8px;background:#0b0e14}
  .nova-quick button{background:rgba(37,195,239,.1);border:1px solid rgba(37,195,239,.3);color:#25c3ef;font-size:12.5px;font-weight:700;padding:7px 11px;border-radius:16px;cursor:pointer}
  .nova-quick button:hover{background:rgba(37,195,239,.2)}
  .nova-foot{display:flex;gap:8px;padding:12px;border-top:1px solid rgba(255,255,255,.08);background:#0f131d}
  .nova-foot input{flex:1;background:#080b12;border:1px solid rgba(255,255,255,.1);border-radius:10px;padding:11px;color:#eaf0f7;font-size:14px}
  .nova-foot input:focus{outline:2px solid #25c3ef;border-color:transparent}
  .nova-foot button{background:linear-gradient(120deg,#25c3ef,#1668cf);border:0;color:#04121e;font-weight:900;padding:0 16px;border-radius:10px;cursor:pointer;font-size:16px}
  .nova-cta{margin:2px 16px 10px;text-align:center;font-size:12px;color:#93a1b5;background:#0b0e14}
  .nova-cta a{color:#25c3ef;font-weight:800}
  @media(max-width:760px){
    .nova-launch{bottom:72px}
    .nova-panel{bottom:0;right:0;width:100vw;max-width:100vw;height:100vh;max-height:100vh;border-radius:0}
  }`;
  var style = document.createElement("style");
  style.textContent = css;
  document.head.appendChild(style);

  // ---------- DOM ----------
  var launch = el('button', 'nova-launch', '<span class="av">N</span><span class="dot"></span><span>Chat with Nova</span>');
  var panel = el('div', 'nova-panel', `
    <div class="nova-head">
      <div class="av">N</div>
      <div><b>Nova Sterling</b><small><span class="on"></span> All Home Building · replies in seconds</small></div>
      <button class="x" aria-label="Close chat">×</button>
    </div>
    <div class="nova-body" id="nova-body"></div>
    <div class="nova-quick" id="nova-quick"></div>
    <div class="nova-cta">Prefer to talk? <a href="tel:${TEL}">Call ${PHONE}</a></div>
    <div class="nova-foot">
      <input id="nova-input" type="text" placeholder="Type your message…" autocomplete="off">
      <button id="nova-send" aria-label="Send">➤</button>
    </div>`);
  document.body.appendChild(launch);
  document.body.appendChild(panel);

  var body = panel.querySelector('#nova-body');
  var quick = panel.querySelector('#nova-quick');
  var input = panel.querySelector('#nova-input');

  launch.addEventListener('click', open);
  panel.querySelector('.x').addEventListener('click', close);
  panel.querySelector('#nova-send').addEventListener('click', sendCurrent);
  input.addEventListener('keydown', function (e) { if (e.key === 'Enter') sendCurrent(); });

  var greeted = false;
  function open() {
    launch.classList.add('hide'); panel.classList.add('open'); input.focus();
    if (!greeted) {
      greeted = true;
      novaSay("Hi there! 👋 I'm Nova with All Home Building. What kind of project are you thinking about?");
      setQuick(["Kitchen remodel", "Bathroom remodel", "Roofing", "Addition"]);
    }
  }
  function close() { panel.classList.remove('open'); launch.classList.remove('hide'); }

  function setQuick(items) {
    quick.innerHTML = '';
    (items || []).forEach(function (t) {
      var b = document.createElement('button');
      b.textContent = t;
      b.addEventListener('click', function () { send(t); });
      quick.appendChild(b);
    });
  }

  function novaSay(text) {
    history.push({ role: 'nova', text: text });
    addBubble('nova', text);
  }
  function addBubble(role, text) {
    var d = document.createElement('div');
    d.className = 'nova-msg ' + role;
    d.textContent = text;
    body.appendChild(d);
    body.scrollTop = body.scrollHeight;
  }
  function typing(on) {
    var t = body.querySelector('.nova-typing');
    if (on && !t) {
      t = document.createElement('div'); t.className = 'nova-typing';
      t.innerHTML = '<i></i><i></i><i></i>'; body.appendChild(t); body.scrollTop = body.scrollHeight;
    } else if (!on && t) { t.remove(); }
  }

  function sendCurrent() { var v = input.value.trim(); if (v) send(v); }
  function send(text) {
    input.value = '';
    setQuick([]);
    addBubble('user', text);
    history.push({ role: 'user', text: text });
    captureContact(text);
    typing(true);

    fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sessionId: sessionId, message: text, history: history, lead: lead, source: 'ahb123-web' })
    })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(); })
      .then(function (data) {
        typing(false);
        novaSay(data && data.reply ? data.reply : fallbackReply(text));
        if (data && data.quick) setQuick(data.quick);
      })
      .catch(function () { typing(false); novaSay(fallbackReply(text)); });
  }

  // very light client-side contact capture
  function captureContact(text) {
    var ph = text.replace(/[^0-9]/g, '');
    if (ph.length >= 10 && !lead.phone) lead.phone = text.match(/[\d().\-\s]{10,}/)[0].trim();
    var em = text.match(/[^\s@]+@[^\s@]+\.[^\s@]+/);
    if (em && !lead.email) lead.email = em[0];
    if (/^[a-z ,.'-]{2,40}$/i.test(text) && !lead.name && fallbackStep >= 4) lead.name = text.trim();
  }

  // Warm scripted fallback so Nova stays useful even without the live agent.
  function fallbackReply(text) {
    fallbackStep++;
    if (lead.phone || lead.email) {
      return "Perfect — thank you! I've got your details and a project specialist will reach out shortly with your free estimate. In the meantime you can reach us anytime at " + PHONE + ". Anything else I can help with?";
    }
    switch (fallbackStep) {
      case 1: return "Great choice — we do a lot of those across Philadelphia and Bucks County. Is this for your home nearby, and are you hoping to start in the next few months or still planning?";
      case 2: return "Got it. So I can have the right specialist follow up with a free estimate, do you have a rough budget in mind? (Most of our projects start around $10,000.)";
      case 3: return "That's helpful, thank you! What's the best name and phone number or email to send your free estimate to?";
      default: return "Thanks so much! The quickest way to lock in your free estimate is a quick call — you can reach us at " + PHONE + ", or drop your name and number here and we'll come to you.";
    }
  }
})();
