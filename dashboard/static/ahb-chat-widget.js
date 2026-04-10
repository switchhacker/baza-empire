/**
 * AHB123 Chat Widget — All Home Building Co LLC
 * Drop this script on any page to add a live chat powered by Nova Sterling AI.
 *
 * Usage:
 *   <script src="http://100.127.118.103:8888/ahb-chat-widget.js"></script>
 *   or
 *   <script src="/ahb-chat-widget.js"></script>
 */
(function(){
  'use strict';

  const API_BASE = (document.currentScript && document.currentScript.src)
    ? new URL(document.currentScript.src).origin
    : window.location.origin;

  let chatId = sessionStorage.getItem('ahb_chat_id') || '';
  let isOpen = false;
  let isTyping = false;

  // ── Inject Styles ──
  const style = document.createElement('style');
  style.textContent = `
    #ahb-chat-widget *{box-sizing:border-box;margin:0;padding:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}
    #ahb-chat-btn{position:fixed;bottom:24px;right:24px;width:60px;height:60px;border-radius:50%;background:linear-gradient(135deg,#e94560,#c0392b);color:#fff;border:none;cursor:pointer;box-shadow:0 4px 20px rgba(233,69,96,.4),0 2px 8px rgba(0,0,0,.2);z-index:99998;display:flex;align-items:center;justify-content:center;transition:transform .2s,box-shadow .2s}
    #ahb-chat-btn:hover{transform:scale(1.08);box-shadow:0 6px 28px rgba(233,69,96,.5)}
    #ahb-chat-btn svg{width:28px;height:28px;fill:#fff}
    #ahb-chat-btn .close-icon{display:none}
    #ahb-chat-btn.open .chat-icon{display:none}
    #ahb-chat-btn.open .close-icon{display:block}
    #ahb-chat-badge{position:absolute;top:-2px;right:-2px;width:18px;height:18px;border-radius:50%;background:#4caf50;border:2px solid #fff;display:none;font-size:9px;color:#fff;align-items:center;justify-content:center;font-weight:700}

    #ahb-chat-window{position:fixed;bottom:96px;right:24px;width:380px;max-width:calc(100vw - 32px);height:560px;max-height:calc(100vh - 120px);background:#fff;border-radius:16px;box-shadow:0 12px 48px rgba(0,0,0,.2),0 2px 12px rgba(0,0,0,.1);z-index:99997;display:none;flex-direction:column;overflow:hidden;animation:ahbSlideUp .25s ease-out}
    #ahb-chat-window.open{display:flex}

    @keyframes ahbSlideUp{from{opacity:0;transform:translateY(20px)}to{opacity:1;transform:translateY(0)}}
    @keyframes ahbTypingDot{0%,60%,100%{opacity:.3}30%{opacity:1}}

    .ahb-chat-header{background:linear-gradient(135deg,#e94560,#c0392b);color:#fff;padding:16px 20px;display:flex;align-items:center;gap:12px;flex-shrink:0}
    .ahb-chat-avatar{width:40px;height:40px;border-radius:50%;background:#fff;display:flex;align-items:center;justify-content:center;font-size:20px;flex-shrink:0}
    .ahb-chat-header-info{flex:1}
    .ahb-chat-header-name{font-size:15px;font-weight:700}
    .ahb-chat-header-status{font-size:11px;opacity:.85;display:flex;align-items:center;gap:4px}
    .ahb-chat-header-status::before{content:'';width:6px;height:6px;border-radius:50%;background:#4caf50}

    .ahb-chat-body{flex:1;overflow-y:auto;padding:16px;background:#f8f9fa;display:flex;flex-direction:column;gap:10px}
    .ahb-chat-body::-webkit-scrollbar{width:4px}
    .ahb-chat-body::-webkit-scrollbar-thumb{background:#ddd;border-radius:2px}

    .ahb-msg{max-width:82%;padding:10px 14px;border-radius:16px;font-size:14px;line-height:1.5;word-wrap:break-word;animation:ahbSlideUp .2s ease-out}
    .ahb-msg.user{align-self:flex-end;background:linear-gradient(135deg,#e94560,#d63550);color:#fff;border-bottom-right-radius:4px}
    .ahb-msg.assistant{align-self:flex-start;background:#fff;color:#333;border:1px solid #e8e8e8;border-bottom-left-radius:4px;box-shadow:0 1px 3px rgba(0,0,0,.05)}
    .ahb-msg.assistant .msg-agent{font-size:10px;color:#e94560;font-weight:600;margin-bottom:3px}
    .ahb-msg-time{font-size:9px;opacity:.5;margin-top:4px;text-align:right}

    .ahb-typing{align-self:flex-start;background:#fff;border:1px solid #e8e8e8;border-radius:16px;border-bottom-left-radius:4px;padding:12px 18px;display:flex;gap:5px;box-shadow:0 1px 3px rgba(0,0,0,.05)}
    .ahb-typing span{width:7px;height:7px;border-radius:50%;background:#ccc;animation:ahbTypingDot 1.2s infinite}
    .ahb-typing span:nth-child(2){animation-delay:.2s}
    .ahb-typing span:nth-child(3){animation-delay:.4s}

    .ahb-chat-footer{padding:12px 16px;background:#fff;border-top:1px solid #eee;display:flex;gap:8px;flex-shrink:0;align-items:flex-end}
    .ahb-chat-input{flex:1;border:1px solid #ddd;border-radius:22px;padding:10px 16px;font-size:14px;outline:none;resize:none;max-height:100px;min-height:40px;line-height:1.4;font-family:inherit;transition:border-color .2s}
    .ahb-chat-input:focus{border-color:#e94560}
    .ahb-chat-input::placeholder{color:#aaa}
    .ahb-chat-send{width:40px;height:40px;border-radius:50%;background:linear-gradient(135deg,#e94560,#c0392b);color:#fff;border:none;cursor:pointer;display:flex;align-items:center;justify-content:center;transition:transform .15s,opacity .15s;flex-shrink:0}
    .ahb-chat-send:hover{transform:scale(1.05)}
    .ahb-chat-send:disabled{opacity:.4;cursor:default;transform:none}
    .ahb-chat-send svg{width:18px;height:18px;fill:#fff}

    .ahb-chat-welcome{text-align:center;padding:20px;color:#888;font-size:13px;line-height:1.6}
    .ahb-chat-welcome-logo{font-size:32px;margin-bottom:8px}
    .ahb-chat-welcome h3{color:#333;font-size:16px;margin-bottom:4px}

    .ahb-powered{text-align:center;padding:6px;font-size:9px;color:#bbb;background:#fff;border-top:1px solid #f0f0f0}
    .ahb-powered a{color:#e94560;text-decoration:none}

    @media(max-width:480px){
      #ahb-chat-window{bottom:0;right:0;width:100vw;height:100vh;max-height:100vh;border-radius:0}
      #ahb-chat-btn{bottom:16px;right:16px;width:54px;height:54px}
    }
  `;
  document.head.appendChild(style);

  // ── Build DOM ──
  const btn = document.createElement('button');
  btn.id = 'ahb-chat-btn';
  btn.setAttribute('aria-label', 'Chat with us');
  btn.innerHTML = `
    <svg class="chat-icon" viewBox="0 0 24 24"><path d="M20 2H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h14l4 4V4c0-1.1-.9-2-2-2zm0 15.2L18.8 16H4V4h16v13.2z"/></svg>
    <svg class="close-icon" viewBox="0 0 24 24"><path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg>
    <span id="ahb-chat-badge">1</span>
  `;
  btn.onclick = toggleChat;

  const win = document.createElement('div');
  win.id = 'ahb-chat-window';
  win.innerHTML = `
    <div class="ahb-chat-header">
      <div class="ahb-chat-avatar">&#127968;</div>
      <div class="ahb-chat-header-info">
        <div class="ahb-chat-header-name">All Home Building Co</div>
        <div class="ahb-chat-header-status">Nova Sterling is online</div>
      </div>
    </div>
    <div class="ahb-chat-body" id="ahb-chat-body">
      <div class="ahb-chat-welcome">
        <div class="ahb-chat-welcome-logo">&#127968;</div>
        <h3>Welcome to All Home Building!</h3>
        <p>Hi there! I'm Nova, your virtual assistant. Ask me about our remodeling services, get a free estimate, or tell me about your project.</p>
      </div>
    </div>
    <div class="ahb-chat-footer">
      <textarea class="ahb-chat-input" id="ahb-chat-input" placeholder="Type your message..." rows="1"></textarea>
      <button class="ahb-chat-send" id="ahb-chat-send" onclick="window._ahbSend()">
        <svg viewBox="0 0 24 24"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/></svg>
      </button>
    </div>
    <div class="ahb-powered">Powered by <a href="https://ahb123.com" target="_blank">AHB123</a></div>
  `;

  document.body.appendChild(win);
  document.body.appendChild(btn);

  // Auto-resize textarea
  const input = document.getElementById('ahb-chat-input');
  input.addEventListener('input', function(){
    this.style.height = 'auto';
    this.style.height = Math.min(this.scrollHeight, 100) + 'px';
  });
  input.addEventListener('keydown', function(e){
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); window._ahbSend(); }
  });

  // ── Toggle ──
  function toggleChat(){
    isOpen = !isOpen;
    btn.classList.toggle('open', isOpen);
    win.classList.toggle('open', isOpen);
    document.getElementById('ahb-chat-badge').style.display = 'none';
    if (isOpen) {
      input.focus();
      // Load history if resuming
      if (chatId && document.querySelectorAll('#ahb-chat-body .ahb-msg').length === 0) {
        loadHistory();
      }
    }
  }

  // ── Load History ──
  async function loadHistory(){
    if (!chatId) return;
    try {
      const r = await fetch(API_BASE + '/api/ahb/widget/history?chat_id=' + chatId);
      const msgs = await r.json();
      if (msgs.length) {
        const body = document.getElementById('ahb-chat-body');
        const welcome = body.querySelector('.ahb-chat-welcome');
        if (welcome) welcome.remove();
        msgs.forEach(m => appendMessage(m.role, m.content));
      }
    } catch(e) {}
  }

  // ── Send ──
  window._ahbSend = async function(){
    const text = input.value.trim();
    if (!text || isTyping) return;
    input.value = '';
    input.style.height = 'auto';

    // Remove welcome
    const welcome = document.querySelector('.ahb-chat-welcome');
    if (welcome) welcome.remove();

    appendMessage('user', text);
    showTyping();

    try {
      const r = await fetch(API_BASE + '/api/ahb/widget/chat', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ message: text, chat_id: chatId })
      });
      const data = await r.json();
      hideTyping();

      if (data.reply) {
        appendMessage('assistant', data.reply, data.agent);
      }
      if (data.chat_id) {
        chatId = data.chat_id;
        sessionStorage.setItem('ahb_chat_id', chatId);
      }
    } catch(e) {
      hideTyping();
      appendMessage('assistant', 'Sorry, I had a brief hiccup. Please try again or call us at 800-484-6404.', 'Nova Sterling');
    }
  };

  // ── Message Rendering ──
  function appendMessage(role, content, agent){
    const body = document.getElementById('ahb-chat-body');
    const msg = document.createElement('div');
    msg.className = 'ahb-msg ' + role;
    const now = new Date().toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
    msg.innerHTML = (role === 'assistant' && agent ? '<div class="msg-agent">' + escHtml(agent) + '</div>' : '') +
      escHtml(content) +
      '<div class="ahb-msg-time">' + now + '</div>';
    body.appendChild(msg);
    body.scrollTop = body.scrollHeight;
  }

  function showTyping(){
    isTyping = true;
    document.getElementById('ahb-chat-send').disabled = true;
    const body = document.getElementById('ahb-chat-body');
    const el = document.createElement('div');
    el.className = 'ahb-typing';
    el.id = 'ahb-typing';
    el.innerHTML = '<span></span><span></span><span></span>';
    body.appendChild(el);
    body.scrollTop = body.scrollHeight;
  }

  function hideTyping(){
    isTyping = false;
    document.getElementById('ahb-chat-send').disabled = false;
    const el = document.getElementById('ahb-typing');
    if (el) el.remove();
  }

  function escHtml(s){
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
  }

  // ── Auto-greeting after 3 seconds ──
  setTimeout(function(){
    if (!isOpen && !chatId) {
      document.getElementById('ahb-chat-badge').style.display = 'flex';
    }
  }, 3000);

})();
