/* QuickRF image editor — shared between the AHB123 desktop tab and the mobile PWA.
 *
 *   QuickRFEditor.open(qid, {
 *     pairId:        string|null,        // truthy → enables Adjust Split mode
 *     parentImagePath: string|null,      // presence drives default mode
 *     onSave:        (qid, result) => {},
 *     onClose:       () => {}
 *   })
 *
 * Two modes share one canvas:
 *   - "edit": rotate / brightness / contrast / crop on the half's image. Saves a
 *     transformed JPEG dataURL via POST /api/ahb/receipts/queue/<qid>/edit-image.
 *   - "split": draggable vertical line on the parent image. Saves a re-cropped
 *     half plus a split_col so the server re-crops the sibling.
 */
(function (global) {
  'use strict';

  var host = null, backdrop = null, canvas = null, ctx = null;
  var imgHalf = null, imgParent = null, S = null;
  var rafToken = 0;

  function $$(html) {
    var d = document.createElement('div');
    d.innerHTML = html.trim();
    return d.firstElementChild;
  }

  function ensureHost() {
    if (host) return;
    host = $$(
      '<div id="qrfEditorRoot" style="' +
        'position:fixed;inset:0;z-index:5000;display:none;' +
        'background:rgba(0,0,0,.92);color:#eee;font-family:system-ui,-apple-system,sans-serif;' +
        'flex-direction:column;align-items:stretch">' +
      '</div>'
    );

    var bar = $$(
      '<div style="display:flex;gap:8px;align-items:center;padding:10px 12px;' +
        'background:#0a0a18;border-bottom:1px solid #1a1a2e;flex-shrink:0">' +
        '<button id="qrfBack" style="background:#1a1a2e;color:#eee;border:1px solid #2a2a4a;' +
          'border-radius:8px;padding:8px 14px;font-size:13px;cursor:pointer">✕ Close</button>' +
        '<div id="qrfModeTabs" style="display:flex;gap:6px;margin-left:8px"></div>' +
        '<div style="flex:1"></div>' +
        '<button id="qrfReset" style="background:#1a1a2e;color:#eee;border:1px solid #2a2a4a;' +
          'border-radius:8px;padding:8px 12px;font-size:12px;cursor:pointer">Reset</button>' +
        '<button id="qrfErase" title="Delete this queue item" style="background:#3a0f14;color:#ff8a8a;' +
          'border:1px solid #5a1a22;border-radius:8px;padding:8px 12px;font-size:12px;cursor:pointer">🗑 Erase</button>' +
        '<button id="qrfSave" style="background:#7c3aed;color:#fff;border:none;' +
          'border-radius:8px;padding:8px 16px;font-size:13px;font-weight:700;cursor:pointer">Save</button>' +
      '</div>'
    );

    var stage = $$(
      '<div id="qrfStage" style="' +
        'flex:1;position:relative;overflow:auto;background:#06060c;' +
        'display:flex;align-items:center;justify-content:center;touch-action:none">' +
        '<canvas id="qrfCanvas" style="display:block;touch-action:none;cursor:grab"></canvas>' +
        '<div id="qrfCropOverlay" style="position:absolute;inset:0;pointer-events:none;display:none"></div>' +
      '</div>'
    );

    var controls = $$(
      '<div id="qrfControls" style="' +
        'display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));' +
        'gap:12px;padding:12px;background:#0a0a18;border-top:1px solid #1a1a2e;flex-shrink:0">' +
        '<div style="display:flex;gap:6px;align-items:center;justify-content:center">' +
          '<button class="qrf-btn" id="qrfRotL">⟲ Rotate L</button>' +
          '<button class="qrf-btn" id="qrfRotR">⟳ Rotate R</button>' +
          '<button class="qrf-btn" id="qrfCropTog">Crop ▢</button>' +
          '<button class="qrf-btn active" id="qrfBwTog" title="Toggle B&W / Color preview">B&W</button>' +
        '</div>' +
        '<label style="display:flex;flex-direction:column;font-size:11px;color:#aaa">' +
          'Brightness <span id="qrfBriLbl" style="color:#eee">1.00</span>' +
          '<input id="qrfBri" type="range" min="0.5" max="1.6" step="0.02" value="1" style="width:100%">' +
        '</label>' +
        '<label style="display:flex;flex-direction:column;font-size:11px;color:#aaa">' +
          'Contrast <span id="qrfConLbl" style="color:#eee">1.00</span>' +
          '<input id="qrfCon" type="range" min="0.5" max="1.8" step="0.02" value="1" style="width:100%">' +
        '</label>' +
        '<div id="qrfHint" style="font-size:11px;color:#666;align-self:center;text-align:center">' +
          'Pinch / wheel = zoom · Drag = pan · Two-finger = pan' +
        '</div>' +
      '</div>'
    );

    var btnCss = document.createElement('style');
    btnCss.textContent = '#qrfEditorRoot .qrf-btn{background:#1a1a2e;color:#eee;border:1px solid #2a2a4a;' +
      'border-radius:6px;padding:8px 12px;font-size:12px;cursor:pointer;white-space:nowrap}' +
      '#qrfEditorRoot .qrf-btn.active{background:#7c3aed;border-color:#7c3aed}' +
      '#qrfEditorRoot input[type=range]{accent-color:#7c3aed}';
    document.head.appendChild(btnCss);

    host.appendChild(bar);
    host.appendChild(stage);
    host.appendChild(controls);
    document.body.appendChild(host);

    backdrop = host;
    canvas = host.querySelector('#qrfCanvas');
    ctx = canvas.getContext('2d');

    host.querySelector('#qrfBack').addEventListener('click', close);
    host.querySelector('#qrfReset').addEventListener('click', resetState);
    host.querySelector('#qrfErase').addEventListener('click', erase);
    host.querySelector('#qrfSave').addEventListener('click', save);
    host.querySelector('#qrfRotL').addEventListener('click', function () { S.rotation = (S.rotation + 270) % 360; schedule(); });
    host.querySelector('#qrfRotR').addEventListener('click', function () { S.rotation = (S.rotation + 90) % 360; schedule(); });
    host.querySelector('#qrfCropTog').addEventListener('click', toggleCrop);
    host.querySelector('#qrfBwTog').addEventListener('click', function () { setBwMode(!S.bw); });
    host.querySelector('#qrfBri').addEventListener('input', function (e) {
      S.brightness = parseFloat(e.target.value) || 1;
      host.querySelector('#qrfBriLbl').textContent = S.brightness.toFixed(2);
      schedule();
    });
    host.querySelector('#qrfCon').addEventListener('input', function (e) {
      S.contrast = parseFloat(e.target.value) || 1;
      host.querySelector('#qrfConLbl').textContent = S.contrast.toFixed(2);
      schedule();
    });

    bindStageGestures(stage);
  }

  function bindStageGestures(stage) {
    var dragging = false, dragKind = '', startX = 0, startY = 0;
    var startCrop = null, startSplit = 0;
    var pinchDist0 = 0, pinchScale0 = 1;
    var pinchMid0 = null;
    // Stage scroll position at drag start — pan now scrolls the stage so the
    // visible window expands with zoom (canvas can overflow naturally).
    var scrollX0 = 0, scrollY0 = 0;

    function pos(ev) {
      var rect = canvas.getBoundingClientRect();
      var t = ev.touches && ev.touches[0] ? ev.touches[0] : ev;
      return { x: t.clientX - rect.left, y: t.clientY - rect.top, rect: rect };
    }

    function onDown(ev) {
      ev.preventDefault();
      if (ev.touches && ev.touches.length === 2) {
        var t0 = ev.touches[0], t1 = ev.touches[1];
        pinchDist0 = Math.hypot(t1.clientX - t0.clientX, t1.clientY - t0.clientY);
        pinchScale0 = S.zoom;
        pinchMid0 = { x: (t0.clientX + t1.clientX) / 2, y: (t0.clientY + t1.clientY) / 2 };
        dragKind = 'pinch';
        return;
      }
      var p = pos(ev);
      if (S.mode === 'split') {
        dragging = true; dragKind = 'split'; startSplit = S.splitColNatural;
        var imgX = displayToImageX(p.x);
        S.splitColNatural = imgX;
        schedule();
        return;
      }
      if (S.cropOn && hitCrop(p)) {
        dragging = true; dragKind = 'crop-' + hitCrop(p); startCrop = Object.assign({}, S.crop);
        startX = p.x; startY = p.y;
        return;
      }
      dragging = true; dragKind = 'pan';
      // Capture stage scroll at drag start; pan now scrolls the stage so the
      // canvas can overflow with zoom and remain navigable.
      var st = host.querySelector('#qrfStage');
      scrollX0 = st.scrollLeft; scrollY0 = st.scrollTop;
      var t = ev.touches && ev.touches[0] ? ev.touches[0] : ev;
      startX = t.clientX; startY = t.clientY;
      canvas.style.cursor = 'grabbing';
    }

    function onMove(ev) {
      // Tier-upgrade hook: also fires on pinch zoom.
      if (dragKind === 'pinch') { maybeUpgradeTier(); }
      if (dragKind === 'pinch' && ev.touches && ev.touches.length === 2) {
        var t0 = ev.touches[0], t1 = ev.touches[1];
        var d = Math.hypot(t1.clientX - t0.clientX, t1.clientY - t0.clientY);
        var newZoom = clamp(pinchScale0 * (d / Math.max(1, pinchDist0)), 0.5, 8);
        var prevZoom = S.zoom;
        S.zoom = newZoom;
        zoomAroundClient(pinchMid0.x, pinchMid0.y, prevZoom, newZoom);
        schedule();
        return;
      }
      if (!dragging) return;
      ev.preventDefault();
      var p = pos(ev);
      if (dragKind === 'pan') {
        // Translate drag into stage scroll so zoom can overflow the stage.
        var tt = ev.touches && ev.touches[0] ? ev.touches[0] : ev;
        var st = host.querySelector('#qrfStage');
        st.scrollLeft = scrollX0 - (tt.clientX - startX);
        st.scrollTop  = scrollY0 - (tt.clientY - startY);
        return;
      } else if (dragKind === 'split') {
        var imgX = displayToImageX(p.x);
        S.splitColNatural = clamp(imgX, 1, S.imageW - 1);
        schedule();
      } else if (dragKind && dragKind.indexOf('crop-') === 0) {
        var which = dragKind.slice(5);
        var dx = p.x - startX, dy = p.y - startY;
        moveCropHandle(which, dx, dy, startCrop);
        schedule();
      }
    }

    function onUp() {
      dragging = false; dragKind = '';
      canvas.style.cursor = 'grab';
    }

    function onWheel(ev) {
      ev.preventDefault();
      var factor = ev.deltaY < 0 ? 1.1 : 0.9;
      var prevZoom = S.zoom;
      S.zoom = clamp(S.zoom * factor, 0.5, 8);
      // Keep the point under the cursor anchored as the canvas grows.
      zoomAroundClient(ev.clientX, ev.clientY, prevZoom, S.zoom);
      maybeUpgradeTier();
      schedule();
    }

    // After a zoom change, recompute the canvas size and shift stage scroll
    // so the pixel the user was hovering stays under the cursor (instead of
    // the receipt sliding off to the corner). cx/cy are client-space coords.
    function zoomAroundClient(cx, cy, prevZoom, newZoom) {
      var st = host.querySelector('#qrfStage');
      var stRect = st.getBoundingClientRect();
      // Position relative to the inner-scrollable origin
      var contentX = (cx - stRect.left) + st.scrollLeft;
      var contentY = (cy - stRect.top)  + st.scrollTop;
      applyZoomLayout();
      var ratio = newZoom / prevZoom;
      var newContentX = contentX * ratio;
      var newContentY = contentY * ratio;
      st.scrollLeft = newContentX - (cx - stRect.left);
      st.scrollTop  = newContentY - (cy - stRect.top);
    }

    stage.addEventListener('mousedown', onDown);
    stage.addEventListener('touchstart', onDown, { passive: false });
    window.addEventListener('mousemove', onMove);
    window.addEventListener('touchmove', onMove, { passive: false });
    window.addEventListener('mouseup', onUp);
    window.addEventListener('touchend', onUp);
    stage.addEventListener('wheel', onWheel, { passive: false });
  }

  function hitCrop(p) {
    if (!S.cropOn || !S.crop) return null;
    var c = S.crop;
    var hx = 24, hy = 24;
    var corners = {
      tl: { x: c.x, y: c.y },
      tr: { x: c.x + c.w, y: c.y },
      bl: { x: c.x, y: c.y + c.h },
      br: { x: c.x + c.w, y: c.y + c.h }
    };
    for (var k in corners) {
      var cn = corners[k];
      if (Math.abs(p.x - cn.x) < hx && Math.abs(p.y - cn.y) < hy) return k;
    }
    if (p.x > c.x + 8 && p.x < c.x + c.w - 8 && p.y > c.y + 8 && p.y < c.y + c.h - 8) return 'move';
    return null;
  }

  function moveCropHandle(which, dx, dy, start) {
    var c = S.crop;
    if (which === 'move') {
      c.x = start.x + dx; c.y = start.y + dy;
    } else if (which === 'tl') {
      c.x = start.x + dx; c.y = start.y + dy;
      c.w = start.w - dx; c.h = start.h - dy;
    } else if (which === 'tr') {
      c.y = start.y + dy;
      c.w = start.w + dx; c.h = start.h - dy;
    } else if (which === 'bl') {
      c.x = start.x + dx;
      c.w = start.w - dx; c.h = start.h + dy;
    } else if (which === 'br') {
      c.w = start.w + dx; c.h = start.h + dy;
    }
    if (c.w < 30) c.w = 30;
    if (c.h < 30) c.h = 30;
  }

  function clamp(v, lo, hi) { return Math.min(hi, Math.max(lo, v)); }

  function buildModeTabs() {
    var tabs = host.querySelector('#qrfModeTabs');
    var canSplit = !!(S.opts.pairId);
    var html = '<button class="qrf-btn ' + (S.mode === 'edit' ? 'active' : '') + '" data-mode="edit">Edit</button>';
    if (canSplit) {
      html += '<button class="qrf-btn ' + (S.mode === 'split' ? 'active' : '') + '" data-mode="split">Adjust Split</button>';
    }
    tabs.innerHTML = html;
    tabs.querySelectorAll('button').forEach(function (b) {
      b.addEventListener('click', function () { setMode(b.dataset.mode); });
    });
  }

  function setMode(m) {
    if (m === S.mode) return;
    S.mode = m;
    S.panX = 0; S.panY = 0; S.zoom = 1;
    if (m === 'split' && S.opts.pairId) {
      ensureParentImage().then(function () { afterImageReady(true); });
    } else {
      afterImageReady(true);
    }
  }

  function loadImage(url) {
    return new Promise(function (resolve, reject) {
      var im = new Image();
      im.crossOrigin = 'anonymous';
      im.onload = function () { resolve(im); };
      im.onerror = function () { reject(new Error('image load failed: ' + url)); };
      im.src = url + (url.indexOf('?') >= 0 ? '&' : '?') + '_t=' + Date.now();
    });
  }

  // Progressive resolution tiers. The first one (1600) is requested on open
  // — already 3-5x larger than the raw 480x640 source so zoom 1-3 looks
  // sharp. We upgrade as the user zooms further: pinch/wheel past 2.5x
  // fetches the 2400 tier, past 4x fetches 3200, past 6x fetches 4000.
  // Each tier is server-side Lanczos+UnsharpMask and disk-cached, so the
  // second visit to the same tier is instant.
  var HI_TIERS = [1600, 2400, 3200, 4000];
  function tierForZoom(z) {
    if (z >= 6) return HI_TIERS[3];
    if (z >= 4) return HI_TIERS[2];
    if (z >= 2.5) return HI_TIERS[1];
    return HI_TIERS[0];
  }
  function _bwSuffix() { return (S.bw === false) ? '' : '&bw=1'; }
  var _tierLoading = false;
  function maybeUpgradeTier() {
    if (S.mode === 'split') return; // split adjuster uses imgParent path
    var want = tierForZoom(S.zoom);
    if (want <= S.loadedTier || _tierLoading) return;
    _tierLoading = true;
    loadImage('/api/ahb/receipts/queue/image/' + S.qid + '?w=' + want + _bwSuffix())
      .then(function (im) {
        imgHalf = im;
        S.loadedTier = want;
        S.imageW = im.naturalWidth;
        S.imageH = im.naturalHeight;
        fitToStage();
        schedule();
      })
      .catch(function () { /* keep current tier */ })
      .then(function () { _tierLoading = false; });
  }
  // Switch between sharp-color and adaptive-threshold B&W. Reloads only the
  // current tier so the swap is instant after the first per-mode fetch.
  function setBwMode(on) {
    S.bw = !!on;
    var btn = host && host.querySelector('#qrfBwTog');
    if (btn) btn.classList.toggle('active', S.bw);
    var want = S.loadedTier || HI_TIERS[0];
    loadImage('/api/ahb/receipts/queue/image/' + S.qid + '?w=' + want + _bwSuffix())
      .then(function (im) {
        imgHalf = im;
        S.imageW = im.naturalWidth;
        S.imageH = im.naturalHeight;
        fitToStage();
        schedule();
      })
      .catch(function () { /* keep current */ });
  }

  function ensureParentImage() {
    if (imgParent) return Promise.resolve(imgParent);
    return loadImage('/api/ahb/receipts/queue/image/' + S.qid + '?parent=1&w=' + HI_TIERS[1] + _bwSuffix()).then(function (i) {
      imgParent = i;
      return i;
    });
  }

  function afterImageReady(rebuildTabs) {
    if (rebuildTabs) buildModeTabs();
    var src = (S.mode === 'split') ? imgParent : imgHalf;
    if (!src) { schedule(); return; }
    S.imageW = src.naturalWidth;
    S.imageH = src.naturalHeight;
    fitToStage();
    if (S.mode === 'split') {
      S.splitColNatural = Math.floor(S.imageW / 2);
    } else {
      S.crop = null; S.cropOn = false;
      host.querySelector('#qrfCropTog').classList.remove('active');
    }
    var splitOnlyHint = (S.mode === 'split')
      ? 'Drag the line to set the split column · pinch / wheel = zoom'
      : 'Pinch / wheel = zoom · Drag = pan · Crop button toggles a marquee';
    host.querySelector('#qrfHint').textContent = splitOnlyHint;
    schedule();
  }

  function fitToStage() {
    var stage = host.querySelector('#qrfStage');
    var sw = stage.clientWidth - 24, sh = stage.clientHeight - 24;
    var rotated = (S.rotation % 180) !== 0;
    var iw = rotated ? S.imageH : S.imageW;
    var ih = rotated ? S.imageW : S.imageH;
    // baseScale = "zoom=1" fit-into-stage scale; cap at 1:1 so tiny images
    // don't get cheaply blown up.
    var baseScale = Math.min(sw / iw, sh / ih, 1);
    S.fitScale = baseScale;
    applyZoomLayout();
  }

  // Resize the canvas bitmap to match (image × fitScale × zoom). With the
  // stage set to overflow:auto, zooming in grows the canvas past the stage
  // edges and the user gets real scrollbars to navigate the larger receipt
  // instead of a clipped fixed window. Capped at 8000px on the long edge
  // so we don't allocate gigantic bitmaps if Serge cranks the zoom.
  function applyZoomLayout() {
    if (!canvas) return;
    var rotated = (S.rotation % 180) !== 0;
    var iw = rotated ? S.imageH : S.imageW;
    var ih = rotated ? S.imageW : S.imageH;
    var dw = iw * S.fitScale * S.zoom;
    var dh = ih * S.fitScale * S.zoom;
    var MAX = 8000;
    var longEdge = Math.max(dw, dh);
    if (longEdge > MAX) {
      var clamp = MAX / longEdge;
      dw *= clamp; dh *= clamp;
    }
    canvas.width = Math.max(1, Math.floor(dw));
    canvas.height = Math.max(1, Math.floor(dh));
  }

  function schedule() {
    if (rafToken) return;
    rafToken = requestAnimationFrame(function () { rafToken = 0; render(); });
  }

  function render() {
    if (!ctx) return;
    var src = (S.mode === 'split') ? imgParent : imgHalf;
    if (!src) return;
    ctx.save();
    ctx.fillStyle = '#06060c';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    // Canvas dims already encode fitScale × zoom (see applyZoomLayout);
    // just center and draw at canvas size. Pan is handled by stage scroll.
    ctx.translate(canvas.width / 2, canvas.height / 2);
    ctx.rotate(S.rotation * Math.PI / 180);
    var rotated = (S.rotation % 180) !== 0;
    var dw = rotated ? canvas.height : canvas.width;
    var dh = rotated ? canvas.width  : canvas.height;
    ctx.filter = 'brightness(' + S.brightness + ') contrast(' + S.contrast + ')';
    ctx.drawImage(src, -dw / 2, -dh / 2, dw, dh);
    ctx.filter = 'none';
    ctx.restore();

    if (S.mode === 'split' && S.splitColNatural != null) {
      var sx = imageToDisplayX(S.splitColNatural);
      ctx.save();
      ctx.strokeStyle = '#7c3aed';
      ctx.lineWidth = 2;
      ctx.setLineDash([8, 6]);
      ctx.beginPath();
      ctx.moveTo(sx, 0);
      ctx.lineTo(sx, canvas.height);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = '#7c3aed';
      ctx.fillRect(sx - 6, canvas.height / 2 - 16, 12, 32);
      ctx.restore();
    }
    if (S.mode === 'edit' && S.cropOn && S.crop) {
      var c = S.crop;
      ctx.save();
      ctx.fillStyle = 'rgba(0,0,0,.55)';
      ctx.fillRect(0, 0, canvas.width, c.y);
      ctx.fillRect(0, c.y + c.h, canvas.width, canvas.height - (c.y + c.h));
      ctx.fillRect(0, c.y, c.x, c.h);
      ctx.fillRect(c.x + c.w, c.y, canvas.width - (c.x + c.w), c.h);
      ctx.strokeStyle = '#7c3aed';
      ctx.lineWidth = 2;
      ctx.strokeRect(c.x, c.y, c.w, c.h);
      ctx.fillStyle = '#7c3aed';
      [[c.x, c.y], [c.x + c.w, c.y], [c.x, c.y + c.h], [c.x + c.w, c.y + c.h]].forEach(function (p) {
        ctx.fillRect(p[0] - 6, p[1] - 6, 12, 12);
      });
      ctx.restore();
    }
  }

  function displayToImageX(displayX) {
    var rotated = (S.rotation % 180) !== 0;
    var iw = rotated ? S.imageH : S.imageW;
    var dw = iw * S.fitScale * S.zoom;
    var cx = canvas.width / 2 + S.panX;
    var ratio = dw > 0 ? (displayX - cx + dw / 2) / dw : 0;
    return clamp(Math.round(ratio * S.imageW), 1, S.imageW - 1);
  }

  function imageToDisplayX(imageX) {
    var rotated = (S.rotation % 180) !== 0;
    var iw = rotated ? S.imageH : S.imageW;
    var dw = iw * S.fitScale * S.zoom;
    var cx = canvas.width / 2 + S.panX;
    return cx - dw / 2 + (imageX / S.imageW) * dw;
  }

  function toggleCrop() {
    var btn = host.querySelector('#qrfCropTog');
    S.cropOn = !S.cropOn;
    if (S.cropOn) {
      btn.classList.add('active');
      var w = canvas.width, h = canvas.height;
      var cw = Math.floor(w * 0.75), ch = Math.floor(h * 0.75);
      S.crop = { x: (w - cw) / 2, y: (h - ch) / 2, w: cw, h: ch };
    } else {
      btn.classList.remove('active');
      S.crop = null;
    }
    schedule();
  }

  function resetState() {
    S.rotation = 0;
    S.brightness = 1;
    S.contrast = 1;
    S.zoom = 1;
    S.panX = 0;
    S.panY = 0;
    S.cropOn = false;
    S.crop = null;
    if (S.mode === 'split') S.splitColNatural = Math.floor(S.imageW / 2);
    host.querySelector('#qrfBri').value = '1';
    host.querySelector('#qrfCon').value = '1';
    host.querySelector('#qrfBriLbl').textContent = '1.00';
    host.querySelector('#qrfConLbl').textContent = '1.00';
    host.querySelector('#qrfCropTog').classList.remove('active');
    fitToStage();
    schedule();
  }

  function exportEdit() {
    // Apply rotate + brightness/contrast to a full-res canvas, then crop if active.
    var rotated = (S.rotation % 180) !== 0;
    var w = rotated ? imgHalf.naturalHeight : imgHalf.naturalWidth;
    var h = rotated ? imgHalf.naturalWidth : imgHalf.naturalHeight;
    var c1 = document.createElement('canvas');
    c1.width = w; c1.height = h;
    var c1ctx = c1.getContext('2d');
    c1ctx.fillStyle = '#fff';
    c1ctx.fillRect(0, 0, w, h);
    c1ctx.filter = 'brightness(' + S.brightness + ') contrast(' + S.contrast + ')';
    c1ctx.translate(w / 2, h / 2);
    c1ctx.rotate(S.rotation * Math.PI / 180);
    c1ctx.drawImage(imgHalf, -imgHalf.naturalWidth / 2, -imgHalf.naturalHeight / 2);
    if (!S.cropOn || !S.crop) return c1.toDataURL('image/jpeg', 0.92);
    // Map display crop rect back to image-space (already rotated).
    var rotatedW = rotated ? S.imageH : S.imageW;
    var rotatedH = rotated ? S.imageW : S.imageH;
    var dw = rotatedW * S.fitScale * S.zoom;
    var dh = rotatedH * S.fitScale * S.zoom;
    var cx = canvas.width / 2 + S.panX;
    var cy = canvas.height / 2 + S.panY;
    var ix = (S.crop.x - (cx - dw / 2)) / dw * w;
    var iy = (S.crop.y - (cy - dh / 2)) / dh * h;
    var iw = S.crop.w / dw * w;
    var ih = S.crop.h / dh * h;
    ix = clamp(Math.round(ix), 0, w - 1);
    iy = clamp(Math.round(iy), 0, h - 1);
    iw = clamp(Math.round(iw), 1, w - ix);
    ih = clamp(Math.round(ih), 1, h - iy);
    var c2 = document.createElement('canvas');
    c2.width = iw; c2.height = ih;
    c2.getContext('2d').drawImage(c1, ix, iy, iw, ih, 0, 0, iw, ih);
    return c2.toDataURL('image/jpeg', 0.92);
  }

  function exportSplitHalf() {
    // For Adjust Split: crop the half from the parent at the chosen split_col.
    var parent = imgParent;
    var pw = parent.naturalWidth, ph = parent.naturalHeight;
    var splitCol = clamp(S.splitColNatural || Math.floor(pw / 2), 1, pw - 1);
    var thisSide = (S.opts.mode || '').indexOf('left') >= 0 ? 'left' : 'right';
    var box = (thisSide === 'left')
      ? { x: 0, y: 0, w: splitCol, h: ph }
      : { x: splitCol, y: 0, w: pw - splitCol, h: ph };
    var c = document.createElement('canvas');
    c.width = box.w; c.height = box.h;
    c.getContext('2d').drawImage(parent, box.x, box.y, box.w, box.h, 0, 0, box.w, box.h);
    return { dataUrl: c.toDataURL('image/jpeg', 0.92), splitCol: splitCol };
  }

  function save() {
    var btn = host.querySelector('#qrfSave');
    btn.textContent = 'Saving…'; btn.disabled = true;
    var body;
    try {
      if (S.mode === 'split') {
        var sp = exportSplitHalf();
        body = { image_b64: sp.dataUrl, split_col: sp.splitCol, rescan: true };
      } else {
        body = { image_b64: exportEdit(), rescan: true };
      }
    } catch (e) {
      btn.textContent = 'Save'; btn.disabled = false;
      alert('Export failed: ' + e.message);
      return;
    }
    fetch('/api/ahb/receipts/queue/' + S.qid + '/edit-image', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    }).then(function (r) { return r.json(); }).then(function (d) {
      btn.textContent = 'Save'; btn.disabled = false;
      if (!d || !d.success) {
        alert('Save failed: ' + (d && d.error || 'unknown'));
        return;
      }
      var cb = S.opts.onSave;
      close();
      if (cb) try { cb(S.qid, d); } catch (_) {}
    }).catch(function (e) {
      btn.textContent = 'Save'; btn.disabled = false;
      alert('Save failed: ' + e.message);
    });
  }

  function erase() {
    if (!confirm('Erase this receipt? This cannot be undone.')) return;
    var btn = host.querySelector('#qrfErase');
    btn.textContent = 'Erasing…'; btn.disabled = true;
    var qid = S.qid;
    var onDeleteCb = S.opts.onDelete || S.opts.onSave;
    fetch('/api/ahb/receipts/queue/' + qid + '/reject', { method: 'POST' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        btn.textContent = '🗑 Erase'; btn.disabled = false;
        if (!d || !d.success) {
          alert('Erase failed: ' + (d && d.error || 'unknown'));
          return;
        }
        close();
        if (onDeleteCb) try { onDeleteCb(qid, d); } catch (_) {}
      })
      .catch(function (e) {
        btn.textContent = '🗑 Erase'; btn.disabled = false;
        alert('Erase failed: ' + e.message);
      });
  }

  function close() {
    var cb = S && S.opts && S.opts.onClose;
    if (host) host.style.display = 'none';
    imgHalf = null; imgParent = null;
    if (cb) try { cb(); } catch (_) {}
  }

  function open(qid, opts) {
    ensureHost();
    S = {
      qid: qid,
      opts: opts || {},
      mode: 'edit',
      rotation: 0,
      brightness: 1,
      contrast: 1,
      zoom: 1,
      panX: 0,
      panY: 0,
      cropOn: false,
      crop: null,
      splitColNatural: null,
      imageW: 1,
      imageH: 1,
      fitScale: 1,
      loadedTier: 0, // current resolution tier (px wide); 0 until first load
      bw: true       // B&W default per Serge — toggle with Color/BW button
    };
    host.querySelector('#qrfBri').value = '1';
    host.querySelector('#qrfCon').value = '1';
    host.querySelector('#qrfBriLbl').textContent = '1.00';
    host.querySelector('#qrfConLbl').textContent = '1.00';
    host.querySelector('#qrfCropTog').classList.remove('active');
    host.style.display = 'flex';
    // Open with first tier + B&W default. Higher tiers fetched on zoom.
    loadImage('/api/ahb/receipts/queue/image/' + qid + '?w=' + HI_TIERS[0] + _bwSuffix()).then(function (im) {
      imgHalf = im;
      S.loadedTier = HI_TIERS[0];
      afterImageReady(true);
    }).catch(function (e) {
      alert('Could not load image: ' + e.message);
      close();
    });
    window.addEventListener('resize', onResize);
  }

  function onResize() { if (host && host.style.display !== 'none') { fitToStage(); schedule(); } }

  global.QuickRFEditor = { open: open };
})(window);
