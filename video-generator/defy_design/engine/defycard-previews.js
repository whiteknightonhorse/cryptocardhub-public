/* =====================================================================
   DefyCard — YouTube Shorts PREVIEW (thumbnail) render engine
   ---------------------------------------------------------------------
   Exposes:
     window.PREVIEW_TEMPLATES                 -> array of 50 template specs
     window.renderPreviewHTML(spec, opts)     -> 1080x1920 HTML string

   spec : number (1..50)  OR  { index:Number, hook?:String, stat?:String }
   opts : { guides?:Boolean }   // guides:false by default

   Every template honors {{HOOK}} (spec.hook) and optional {{STAT}} (spec.stat),
   carries a DefyCard brand mark, lives inside 1080x1920 with >=60px safe
   margins, and is built from non-overlapping flex blocks.
   ===================================================================== */
(function () {
  var FONT = "'Space Grotesk','Helvetica Neue',Arial,sans-serif";
  var MONO = "'Space Mono','SFMono-Regular',Menlo,monospace";
  var GOLD = '#F7C204';
  var SAFE = 84; // safe inset (>60px requirement)

  /* ---------- palettes (8-10) -------------------------------------- */
  var PAL = {
    gold:    { name:'Gold Luxe',     a:'#F7C204', a2:'#FFE583', bg:'#0a0a0b', bg2:'#1c1606' },
    violet:  { name:'Electric Violet',a:'#8B5CFF', a2:'#C0A6FF', bg:'#0b0a14', bg2:'#170f2e' },
    cyan:    { name:'Neon Cyan',     a:'#22E0FF', a2:'#9CF1FF', bg:'#070d12', bg2:'#0a1c26' },
    lime:    { name:'Electric Lime', a:'#B6FF3A', a2:'#DCFF94', bg:'#0a0d06', bg2:'#161f08' },
    magenta: { name:'Magenta Glow',  a:'#FF3DAE', a2:'#FF9AD6', bg:'#100711', bg2:'#240b22' },
    ice:     { name:'Ice Blue',      a:'#6FB3FF', a2:'#BBDcFF', bg:'#080b12', bg2:'#0e1726' },
    emerald: { name:'Emerald',       a:'#18E08C', a2:'#86F2C4', bg:'#06100c', bg2:'#0a2218' },
    orange:  { name:'Sunset Orange', a:'#FF7A1A', a2:'#FFB779', bg:'#100904', bg2:'#241206' },
    mono:    { name:'Mono White',    a:'#FFFFFF', a2:'#C8CCD2', bg:'#0a0a0b', bg2:'#161618' },
    holo:    { name:'Holographic',   a:'#7AF9FF', a2:'#FF9AE0', bg:'#07090f', bg2:'#0e1326' }
  };

  /* coin brand colors */
  var BTC = ['#F7931A', '#FFC774'], ETH = ['#627EEA', '#A9BBF5'], USDC = ['#2775CA', '#7FB2EC'];

  /* ---------- tiny helpers ----------------------------------------- */
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
  function hexA(hex, a) {
    var c = hex.replace('#', '');
    if (c.length === 3) c = c.split('').map(function (x) { return x + x; }).join('');
    var n = parseInt(c, 16);
    return 'rgba(' + ((n >> 16) & 255) + ',' + ((n >> 8) & 255) + ',' + (n & 255) + ',' + a + ')';
  }

  /* ---------- decorative atoms ------------------------------------- */
  function glow(color, x, y, r, op) {
    return '<div style="position:absolute;left:' + x + ';top:' + y + ';width:' + r + 'px;height:' + r +
      'px;transform:translate(-50%,-50%);border-radius:50%;background:radial-gradient(circle,' +
      hexA(color, op == null ? .6 : op) + ',transparent 70%);"></div>';
  }
  function vignette() {
    return '<div style="position:absolute;inset:0;background:radial-gradient(120% 80% at 50% 30%,transparent 55%,rgba(0,0,0,.55));pointer-events:none;"></div>';
  }
  function noise() {
    return '<div style="position:absolute;inset:0;opacity:.05;background-image:radial-gradient(rgba(255,255,255,.8) 1px,transparent 1px);background-size:4px 4px;pointer-events:none;"></div>';
  }
  function gridFloor(color) {
    return '<div style="position:absolute;left:-20%;right:-20%;bottom:-2%;height:48%;background:' +
      'repeating-linear-gradient(90deg,transparent 0 86px,' + hexA(color, .55) + ' 86px 89px),' +
      'repeating-linear-gradient(0deg,transparent 0 86px,' + hexA(color, .55) + ' 86px 89px);' +
      'transform:perspective(640px) rotateX(64deg);transform-origin:bottom;mask-image:linear-gradient(transparent,#000 40%);-webkit-mask-image:linear-gradient(transparent,#000 40%);"></div>';
  }
  function broll(label) {
    return '<div style="position:absolute;inset:0;background:repeating-linear-gradient(125deg,#16161c 0 30px,#0f0f14 30px 60px);opacity:.6;"></div>' +
      '<div style="position:absolute;inset:0;background:linear-gradient(180deg,rgba(0,0,0,.2),rgba(0,0,0,.78));"></div>' +
      '<div style="position:absolute;left:' + SAFE + 'px;bottom:' + SAFE + 'px;font:700 22px/1 ' + MONO +
      ';color:rgba(255,255,255,.32);letter-spacing:3px;">' + (label || 'B-ROLL FRAME') + '</div>';
  }

  function glyph(P, s) {
    s = s || 38; var w = Math.round(s * 1.5);
    return '<div style="width:' + w + 'px;height:' + s + 'px;border-radius:' + Math.round(s * 0.22) +
      'px;background:linear-gradient(135deg,' + P.a + ',' + P.a2 + ');position:relative;flex:none;box-shadow:0 4px 12px rgba(0,0,0,.4);">' +
      '<div style="position:absolute;left:' + Math.round(s * 0.18) + 'px;top:' + Math.round(s * 0.24) +
      'px;width:' + Math.round(s * 0.3) + 'px;height:' + Math.round(s * 0.18) + 'px;border-radius:3px;background:rgba(0,0,0,.4);"></div>' +
      '<div style="position:absolute;left:' + Math.round(s * 0.18) + 'px;top:' + Math.round(s * 0.54) +
      'px;width:' + Math.round(s * 0.58) + 'px;height:' + Math.round(s * 0.12) + 'px;border-radius:2px;background:rgba(0,0,0,.45);"></div>' +
      '</div>';
  }
  function mark(P, o) {
    o = o || {}; var c = o.color || '#fff';
    return '<div style="display:flex;align-items:center;gap:14px;">' + glyph(P, o.s || 40) +
      '<span style="font:700 ' + (o.fs || 34) + 'px/1 ' + FONT + ';letter-spacing:-.5px;color:' + c +
      ';">Defy<span style="color:' + P.a + ';">Card</span></span></div>';
  }
  function cta(P, o) {
    o = o || {};
    return '<div style="display:inline-flex;align-items:center;gap:12px;align-self:' + (o.align || 'flex-start') +
      ';padding:20px 34px;border-radius:100px;background:' + (o.bg || P.a) + ';color:#0a0a0b;font:700 30px/1 ' +
      FONT + ';letter-spacing:.3px;">defycard.com&nbsp;<span style="font-weight:800;">&rarr;</span></div>';
  }
  function coin(sym, c1, c2, size, extra) {
    return '<div style="width:' + size + 'px;height:' + size + 'px;border-radius:50%;background:radial-gradient(circle at 36% 30%,' +
      c2 + ',' + c1 + ');display:flex;align-items:center;justify-content:center;font:800 ' + Math.round(size * 0.46) +
      'px/1 ' + FONT + ';color:rgba(0,0,0,.72);box-shadow:0 16px 36px rgba(0,0,0,.5),inset 0 3px 8px rgba(255,255,255,.55);' +
      (extra || '') + '">' + sym + '</div>';
  }
  function H(text, o) {
    o = o || {};
    return '<div style="font:' + (o.w || 800) + ' ' + (o.size || 130) + 'px/' + (o.lh || 0.92) + ' ' + FONT +
      ';letter-spacing:' + (o.ls || '-3px') + ';text-transform:' + (o.tt || 'uppercase') + ';color:' + (o.color || '#fff') +
      ';text-wrap:balance;' + (o.extra || '') + '">' + text + '</div>';
  }
  function eyebrow(text, P, o) {
    o = o || {};
    return '<div style="font:700 ' + (o.fs || 30) + 'px/1 ' + MONO + ';letter-spacing:6px;text-transform:uppercase;color:' +
      (o.color || P.a) + ';">' + text + '</div>';
  }
  function pill(text, P, o) {
    o = o || {};
    return '<div style="display:inline-flex;align-items:center;gap:14px;align-self:' + (o.align || 'flex-start') +
      ';padding:' + (o.py || 18) + 'px ' + (o.px || 36) + 'px;border-radius:100px;background:' + (o.bg || hexA(P.a, .15)) +
      ';border:2px solid ' + (o.bc || P.a) + ';color:' + (o.color || P.a) + ';font:800 ' + (o.fs || 42) + 'px/1 ' + FONT +
      ';letter-spacing:.5px;">' + text + '</div>';
  }
  function statBlock(stat, P, o) {
    o = o || {};
    if (!stat) return '';
    return '<div style="display:flex;flex-direction:column;align-items:' + (o.align || 'flex-start') + ';">' +
      '<div style="font:900 ' + (o.size || 230) + 'px/0.82 ' + FONT + ';letter-spacing:-8px;color:' + (o.color || P.a) +
      ';' + (o.extra || '') + '">' + stat + '</div>' +
      (o.label ? '<div style="font:700 ' + (o.lfs || 34) + 'px/1 ' + MONO + ';letter-spacing:5px;text-transform:uppercase;color:rgba(255,255,255,.6);margin-top:8px;">' + o.label + '</div>' : '') +
      '</div>';
  }

  function makeCard(P, o) {
    o = o || {};
    var w = o.w || 660, h = Math.round(w / 1.586), r = Math.round(w * 0.065), p = Math.round(w * 0.072);
    var surf, txt = '#fff', sub = 'rgba(255,255,255,.55)';
    switch (o.variant) {
      case 'metal': surf = 'linear-gradient(140deg,#23232a,#0b0b0d 55%,#1b1b20)'; break;
      case 'holo':  surf = 'conic-gradient(from 210deg at 60% 38%,#7af9ff,#b79bff,#ff7ae0,#ffe27a,#7af9ff)'; txt = 'rgba(0,0,0,.82)'; sub = 'rgba(0,0,0,.5)'; break;
      case 'gold':  surf = 'linear-gradient(140deg,#FFECAE,#F7C204 50%,#9a7a0c)'; txt = 'rgba(0,0,0,.85)'; sub = 'rgba(0,0,0,.5)'; break;
      case 'neon':  surf = 'linear-gradient(150deg,#0f1828,#0a0e18)'; break;
      case 'glass': surf = 'linear-gradient(140deg,rgba(255,255,255,.18),rgba(255,255,255,.03))'; break;
      case 'mono':  surf = 'linear-gradient(140deg,#34343b,#101012)'; break;
      default:      surf = 'linear-gradient(140deg,#1a1a22,#0b0b0d)';
    }
    var border = o.variant === 'neon' ? ('2px solid ' + P.a) :
      (o.variant === 'glass' ? '1px solid rgba(255,255,255,.32)' : '1px solid rgba(255,255,255,.1)');
    var glowCss = o.glow ? (',0 0 90px ' + hexA(P.a, .45)) : '';
    var chipBg = o.variant === 'gold' ? 'linear-gradient(135deg,#3a2e06,#6b540a)' : 'linear-gradient(135deg,' + P.a2 + ',' + P.a + ')';
    return '<div style="width:' + w + 'px;height:' + h + 'px;border-radius:' + r + 'px;background:' + surf +
      ';border:' + border + ';box-shadow:0 50px 100px rgba(0,0,0,.62)' + glowCss +
      ';position:relative;overflow:hidden;padding:' + p + 'px;display:flex;flex-direction:column;justify-content:space-between;' +
      (o.transform ? ('transform:' + o.transform + ';') : '') + '">' +
      '<div style="position:absolute;inset:0;background:linear-gradient(115deg,rgba(255,255,255,.2),transparent 42%);pointer-events:none;"></div>' +
      '<div style="display:flex;justify-content:space-between;align-items:flex-start;position:relative;">' +
        '<span style="font:700 ' + Math.round(w * 0.056) + 'px/1 ' + FONT + ';letter-spacing:-.5px;color:' + txt + ';">Defy<span style="opacity:.65;">Card</span></span>' +
        '<span style="font:700 ' + Math.round(w * 0.03) + 'px/1 ' + MONO + ';letter-spacing:3px;color:' + sub + ';">CRYPTO</span>' +
      '</div>' +
      '<div style="display:flex;align-items:center;position:relative;">' +
        '<div style="width:' + Math.round(w * 0.135) + 'px;height:' + Math.round(w * 0.1) + 'px;border-radius:' + Math.round(w * 0.022) +
          'px;background:' + chipBg + ';box-shadow:inset 0 0 0 2px rgba(0,0,0,.18);"></div>' +
      '</div>' +
      '<div style="position:relative;">' +
        '<div style="font:700 ' + Math.round(w * 0.056) + 'px/1 ' + MONO + ';letter-spacing:1px;color:' + txt + ';">&bull;&bull;&bull;&bull;&nbsp;&bull;&bull;&bull;&bull;&nbsp;&bull;&bull;&bull;&bull;&nbsp;4204</div>' +
        '<div style="display:flex;justify-content:space-between;align-items:flex-end;margin-top:' + Math.round(w * 0.04) + 'px;">' +
          '<span style="font:400 ' + Math.round(w * 0.03) + 'px/1.2 ' + MONO + ';color:' + sub + ';">VALID THRU&nbsp;12/30</span>' +
          '<span style="font:800 ' + Math.round(w * 0.05) + 'px/1 ' + FONT + ';letter-spacing:1px;color:' + txt + ';">DEFY</span>' +
        '</div>' +
      '</div>' +
    '</div>';
  }

  /* layout shell: a padded flex column inside the safe area */
  function col(inner, o) {
    o = o || {};
    return '<div style="position:absolute;inset:' + SAFE + 'px;display:flex;flex-direction:column;' +
      (o.justify ? ('justify-content:' + o.justify + ';') : '') +
      (o.align ? ('align-items:' + o.align + ';') : '') +
      (o.gap ? ('gap:' + o.gap + 'px;') : '') + (o.extra || '') + '">' + inner + '</div>';
  }
  function spacer() { return '<div style="flex:1;"></div>'; }

  /* =================================================================
     50 BUILDERS  — fn(P, hook, stat, guides) -> inner HTML string
     ================================================================= */
  var BUILD = {}, META = [];
  function def(index, name, category, palName, hook, stat, fn) {
    BUILD[index] = fn;
    META.push({ index: index, name: name, category: category, palette: palName, defaultHook: hook, defaultStat: stat || '' });
  }

  /* 1 — matte black metal card, gold accent */
  def(1, 'Matte Metal', 'Card hero', 'gold', 'SPEND CRYPTO ANYWHERE', '', function (P, h, s) {
    return glow(P.a, '50%', '34%', 900, .22) +
      col('<div style="display:flex;justify-content:space-between;align-items:center;">' + mark(P) + eyebrow('CRYPTO CARD', P) + '</div>' +
        spacer() +
        '<div style="display:flex;justify-content:center;">' + makeCard(P, { variant: 'metal', w: 720, glow: true, transform: 'rotate(-6deg)' }) + '</div>' +
        spacer() +
        H(h, { size: 132 }) +
        '<div style="margin-top:30px;">' + cta(P) + '</div>', { gap: 0 });
  });

  /* 2 — holographic card, tap pay */
  def(2, 'Holo Tap', 'Card hero', 'holo', 'TAP. PAY. DONE.', '', function (P, h, s) {
    return glow('#ff9ae0', '28%', '24%', 760, .26) + glow('#7af9ff', '74%', '70%', 760, .24) +
      col(eyebrow('HOLOGRAPHIC EDITION', P) +
        spacer() +
        '<div style="display:flex;justify-content:center;">' + makeCard(P, { variant: 'holo', w: 730, transform: 'rotate(5deg)' }) + '</div>' +
        spacer() +
        H(h, { size: 150 }) +
        '<div style="display:flex;justify-content:space-between;align-items:flex-end;margin-top:34px;">' + mark(P) + cta(P) + '</div>', { gap: 0 });
  });

  /* 3 — neon fintech glow */
  def(3, 'Neon Fintech', 'Card hero', 'cyan', 'YOUR CRYPTO, ANYWHERE', '', function (P, h, s) {
    return gridFloor(P.a) + glow(P.a, '50%', '40%', 820, .2) +
      col(mark(P) + spacer() +
        '<div style="display:flex;justify-content:center;">' + makeCard(P, { variant: 'neon', w: 700, glow: true }) + '</div>' +
        spacer() + H(h, { size: 124, color: '#fff' }) +
        '<div style="margin-top:28px;">' + pill('TAP &middot; PAY &middot; EARN', P, { fs: 36 }) + '</div>', { gap: 0 });
  });

  /* 4 — gold luxury */
  def(4, 'Gold Luxury', 'Card hero', 'gold', 'SPEND LIKE GOLD', '', function (P, h, s) {
    return '<div style="position:absolute;inset:0;background:radial-gradient(120% 90% at 50% 18%,' + P.bg2 + ',' + P.bg + ');"></div>' +
      glow(P.a, '50%', '30%', 900, .3) +
      col(eyebrow('PREMIUM METAL', P) + spacer() +
        '<div style="display:flex;justify-content:center;">' + makeCard(P, { variant: 'gold', w: 740, glow: true, transform: 'rotate(-4deg)' }) + '</div>' +
        spacer() + H(h, { size: 168, color: P.a2 }) +
        '<div style="display:flex;justify-content:space-between;align-items:flex-end;margin-top:30px;">' + mark(P) + cta(P) + '</div>', { gap: 0 });
  });

  /* 5 — floating coin burst */
  def(5, 'Coin Burst', 'Coins', 'violet', 'CRYPTO IN YOUR POCKET', '', function (P, h, s) {
    var coins = '<div style="display:flex;justify-content:center;align-items:center;gap:36px;">' +
      coin('&#8383;', BTC[0], BTC[1], 200, 'transform:translateY(40px) rotate(-12deg);') +
      coin('&#926;', ETH[0], ETH[1], 260, 'transform:translateY(-30px);') +
      coin('$', USDC[0], USDC[1], 200, 'transform:translateY(46px) rotate(12deg);') + '</div>';
    return glow(P.a, '50%', '40%', 880, .26) +
      col(mark(P) + spacer() + coins + spacer() + H(h, { size: 138 }) +
        '<div style="margin-top:30px;">' + cta(P) + '</div>', { gap: 0 });
  });

  /* 6 — tap to pay glow rings */
  def(6, 'Tap To Pay', 'Interaction', 'cyan', 'JUST TAP TO PAY', '', function (P, h, s) {
    var rings = '<div style="position:relative;width:520px;height:520px;display:flex;align-items:center;justify-content:center;">' +
      '<div style="position:absolute;width:520px;height:520px;border-radius:50%;border:3px solid ' + hexA(P.a, .25) + ';"></div>' +
      '<div style="position:absolute;width:380px;height:380px;border-radius:50%;border:3px solid ' + hexA(P.a, .45) + ';"></div>' +
      '<div style="position:absolute;width:240px;height:240px;border-radius:50%;border:3px solid ' + hexA(P.a, .7) + ';"></div>' +
      makeCard(P, { variant: 'neon', w: 460, glow: true, transform: 'rotate(-90deg)' }) + '</div>';
    return glow(P.a, '50%', '40%', 700, .22) +
      col(eyebrow('CONTACTLESS', P) + spacer() +
        '<div style="display:flex;justify-content:center;">' + rings + '</div>' + spacer() +
        H(h, { size: 150 }) + '<div style="display:flex;justify-content:space-between;align-items:flex-end;margin-top:30px;">' + mark(P) + cta(P) + '</div>', { gap: 0 });
  });

  /* 7 — apple-pay style lockscreen notification */
  def(7, 'Lockscreen', 'Lifestyle', 'mono', 'PAYMENT APPROVED', '', function (P, h, s) {
    var notif = '<div style="width:760px;background:rgba(255,255,255,.1);border:1px solid rgba(255,255,255,.16);backdrop-filter:blur(12px);border-radius:42px;padding:48px;display:flex;align-items:center;gap:30px;">' +
      glyph(P, 88) +
      '<div style="display:flex;flex-direction:column;gap:8px;"><span style="font:700 40px/1 ' + FONT + ';color:#fff;">DefyCard</span>' +
      '<span style="font:400 36px/1.3 ' + FONT + ';color:rgba(255,255,255,.7);">Apple&nbsp;Pay &bull; $42.80 at Blue Bottle</span></div></div>';
    return col('<div style="display:flex;flex-direction:column;align-items:center;gap:14px;">' +
      '<div style="font:300 200px/0.9 ' + FONT + ';color:#fff;letter-spacing:-6px;">9:41</div>' +
      '<div style="font:500 40px/1 ' + FONT + ';color:rgba(255,255,255,.6);">Monday, June 22</div></div>' +
      spacer() + notif + spacer() +
      H(h, { size: 116, color: P.a, align: 'center', extra: 'text-align:center;' }) +
      '<div style="display:flex;justify-content:center;margin-top:24px;">' + cta(P) + '</div>', { align: 'center', gap: 0 });
  });

  /* 8 — cashback confetti */
  def(8, 'Cashback Confetti', 'Stat', 'emerald', 'GET CASHBACK IN CRYPTO', '2% BACK', function (P, h, s) {
    var conf = '';
    var cs = [P.a, P.a2, GOLD, '#fff'];
    for (var i = 0; i < 26; i++) {
      var x = (i * 41 + 60) % 940 + 70, y = (i * 73) % 360 + 120, sz = 14 + (i % 4) * 8;
      conf += '<div style="position:absolute;left:' + x + 'px;top:' + y + 'px;width:' + sz + 'px;height:' + Math.round(sz * 0.5) +
        'px;background:' + cs[i % 4] + ';border-radius:3px;transform:rotate(' + (i * 47) + 'deg);opacity:.85;"></div>';
    }
    return conf + glow(P.a, '50%', '54%', 800, .2) +
      col(mark(P) + spacer() +
        statBlock(s || '2%', P, { size: 360, align: 'center', label: 'IN CRYPTO, EVERY SWIPE' }) + spacer() +
        H(h, { size: 116, align: 'center', extra: 'text-align:center;' }) +
        '<div style="display:flex;justify-content:center;margin-top:28px;">' + cta(P) + '</div>', { align: 'center', gap: 0 });
  });

  /* 9 — spend anywhere globe */
  def(9, 'Globe Anywhere', 'Concept', 'ice', 'SPEND CRYPTO ANYWHERE', '', function (P, h, s) {
    var globe = '<div style="width:560px;height:560px;border-radius:50%;position:relative;background:radial-gradient(circle at 38% 32%,' + hexA(P.a, .5) + ',' + P.bg2 + ' 70%);box-shadow:0 0 90px ' + hexA(P.a, .4) + ',inset -30px -30px 80px rgba(0,0,0,.6);overflow:hidden;border:1px solid ' + hexA(P.a, .4) + ';">' +
      '<div style="position:absolute;inset:0;background:repeating-linear-gradient(0deg,transparent 0 56px,' + hexA(P.a, .25) + ' 56px 58px),repeating-linear-gradient(90deg,transparent 0 56px,' + hexA(P.a, .18) + ' 56px 58px);border-radius:50%;"></div></div>';
    return col(eyebrow('190+ COUNTRIES', P) + spacer() +
      '<div style="display:flex;justify-content:center;">' + globe + '</div>' + spacer() +
      H(h, { size: 124 }) + '<div style="display:flex;justify-content:space-between;align-items:flex-end;margin-top:30px;">' + mark(P) + cta(P) + '</div>', { gap: 0 });
  });

  /* 10 — phone-in-hand lifestyle (b-roll) */
  def(10, 'Lifestyle B-roll', 'Lifestyle', 'violet', 'ONE TAP. REAL WORLD.', '', function (P, h, s) {
    return broll('LIFESTYLE / PHONE-IN-HAND') + glow(P.a, '50%', '78%', 900, .22) +
      col(mark(P, { color: '#fff' }) + spacer() +
        pill('TAP-TO-PAY', P) +
        '<div style="margin-top:24px;">' + H(h, { size: 142 }) + '</div>' +
        '<div style="margin-top:30px;">' + cta(P) + '</div>', { gap: 0 });
  });

  /* 11 — receipt / checkout */
  def(11, 'Checkout Receipt', 'Concept', 'gold', 'CHECKOUT IN CRYPTO', '2% BACK', function (P, h, s) {
    var receipt = '<div style="width:520px;background:#f6f4ec;color:#16140c;border-radius:14px;padding:46px;font-family:' + MONO + ';box-shadow:0 40px 90px rgba(0,0,0,.55);">' +
      '<div style="font:700 36px/1 ' + FONT + ';letter-spacing:1px;margin-bottom:24px;">DEFYCARD &bull; RECEIPT</div>' +
      '<div style="display:flex;justify-content:space-between;font-size:30px;margin:14px 0;">Subtotal<span>$128.00</span></div>' +
      '<div style="display:flex;justify-content:space-between;font-size:30px;margin:14px 0;">Paid in BTC<span>&#8383; 0.0019</span></div>' +
      '<div style="border-top:2px dashed #16140c;margin:22px 0;"></div>' +
      '<div style="display:flex;justify-content:space-between;font-size:34px;font-weight:700;color:' + '#9a7a0c' + ';">Cashback<span>+' + (s || '2% BACK') + '</span></div></div>';
    return glow(P.a, '50%', '34%', 760, .2) +
      col(mark(P) + spacer() + '<div style="display:flex;justify-content:center;">' + receipt + '</div>' + spacer() +
        H(h, { size: 124 }) + '<div style="margin-top:28px;">' + cta(P) + '</div>', { gap: 0 });
  });

  /* 12 — 3D card render */
  def(12, '3D Render', 'Card hero', 'magenta', 'THE CARD THAT GETS IT', '', function (P, h, s) {
    return glow(P.a, '40%', '36%', 820, .26) + glow(P.a2, '70%', '60%', 600, .2) +
      col(eyebrow('NEXT-GEN CRYPTO CARD', P) + spacer() +
        '<div style="display:flex;justify-content:center;perspective:1400px;">' +
        makeCard(P, { variant: 'metal', w: 760, glow: true, transform: 'rotateX(18deg) rotateY(-22deg) rotate(4deg)' }) + '</div>' +
        spacer() + H(h, { size: 120 }) +
        '<div style="display:flex;justify-content:space-between;align-items:flex-end;margin-top:30px;">' + mark(P) + cta(P) + '</div>', { gap: 0 });
  });

  /* 13 — liquid gradient */
  def(13, 'Liquid Gradient', 'Abstract', 'orange', 'MONEY THAT MOVES', '', function (P, h, s) {
    return '<div style="position:absolute;inset:0;background:linear-gradient(135deg,' + P.bg + ',' + P.bg2 + ');"></div>' +
      glow(P.a, '30%', '28%', 900, .5) + glow(P.a2, '78%', '62%', 760, .4) + glow('#ff3dae', '60%', '85%', 600, .25) +
      col(mark(P) + spacer() +
        '<div style="display:flex;justify-content:center;">' + makeCard(P, { variant: 'glass', w: 700, glow: true, transform: 'rotate(-8deg)' }) + '</div>' +
        spacer() + H(h, { size: 150 }) + '<div style="margin-top:30px;">' + cta(P) + '</div>', { gap: 0 });
  });

  /* 14 — dark glassmorphism */
  def(14, 'Glass Panel', 'Abstract', 'ice', 'CRYPTO, MADE SIMPLE', '', function (P, h, s) {
    var panel = '<div style="width:100%;background:rgba(255,255,255,.07);border:1px solid rgba(255,255,255,.18);backdrop-filter:blur(20px);border-radius:48px;padding:70px;display:flex;flex-direction:column;gap:34px;">' +
      '<div style="display:flex;justify-content:center;">' + makeCard(P, { variant: 'glass', w: 560 }) + '</div>' +
      '<div style="display:flex;gap:20px;justify-content:center;">' + pill('INSTANT', P, { fs: 32 }) + pill('SECURE', P, { fs: 32 }) + pill('NO FEES', P, { fs: 32 }) + '</div></div>';
    return glow(P.a, '30%', '24%', 760, .3) + glow(P.a2, '76%', '72%', 700, .24) +
      col(mark(P) + spacer() + panel + spacer() + H(h, { size: 120 }) +
        '<div style="margin-top:26px;">' + cta(P) + '</div>', { gap: 0 });
  });

  /* 15 — ticker / price HUD */
  def(15, 'Price HUD', 'Data', 'lime', 'WATCH IT. SPEND IT.', '', function (P, h, s) {
    function row(sym, name, price, chg) {
      return '<div style="display:flex;justify-content:space-between;align-items:center;padding:26px 36px;border:1px solid ' + hexA(P.a, .22) + ';border-radius:22px;background:' + hexA(P.a, .05) + ';">' +
        '<span style="font:700 40px/1 ' + FONT + ';color:#fff;">' + sym + ' <span style="color:rgba(255,255,255,.4);font-weight:400;font-size:32px;">' + name + '</span></span>' +
        '<span style="font:700 40px/1 ' + MONO + ';color:#fff;">' + price + ' <span style="color:' + P.a + ';">' + chg + '</span></span></div>';
    }
    var hud = '<div style="display:flex;flex-direction:column;gap:20px;width:100%;">' +
      row('BTC', 'Bitcoin', '$71,204', '&#9650;2.4%') + row('ETH', 'Ethereum', '$3,880', '&#9650;1.1%') + row('USDC', 'USD Coin', '$1.00', '&#9650;0.0%') + '</div>';
    return glow(P.a, '50%', '30%', 760, .16) +
      col(mark(P) + spacer() + hud + spacer() + H(h, { size: 138 }) +
        '<div style="margin-top:26px;">' + cta(P) + '</div>', { gap: 0 });
  });

  /* 16 — before / after split */
  def(16, 'Before / After', 'Concept', 'cyan', 'FROM WALLET TO REAL LIFE', '', function (P, h, s) {
    var split = '<div style="display:flex;gap:28px;width:100%;height:660px;">' +
      '<div style="flex:1;border-radius:34px;background:' + hexA('#ffffff', .04) + ';border:1px solid rgba(255,255,255,.1);display:flex;flex-direction:column;align-items:center;justify-content:center;gap:30px;">' +
      '<div style="font:700 30px/1 ' + MONO + ';letter-spacing:4px;color:rgba(255,255,255,.5);">CRYPTO WALLET</div>' + coin('&#8383;', BTC[0], BTC[1], 200) + '</div>' +
      '<div style="display:flex;align-items:center;font:800 90px/1 ' + FONT + ';color:' + P.a + ';">&rarr;</div>' +
      '<div style="flex:1;border-radius:34px;background:' + hexA(P.a, .08) + ';border:1px solid ' + hexA(P.a, .4) + ';display:flex;flex-direction:column;align-items:center;justify-content:center;gap:30px;">' +
      '<div style="font:700 30px/1 ' + MONO + ';letter-spacing:4px;color:' + P.a + ';">DEFYCARD</div>' + makeCard(P, { variant: 'neon', w: 360, glow: true }) + '</div></div>';
    return col(mark(P) + spacer() + split + spacer() + H(h, { size: 110 }) +
      '<div style="margin-top:24px;">' + cta(P) + '</div>', { gap: 0 });
  });

  /* 17 — big stat hero */
  def(17, 'Big Stat', 'Stat', 'gold', 'CASHBACK ON EVERY TAP', '2%', function (P, h, s) {
    return glow(P.a, '50%', '40%', 900, .26) +
      col(mark(P) + spacer() +
        '<div style="display:flex;flex-direction:column;align-items:center;">' +
        '<div style="font:900 620px/0.8 ' + FONT + ';letter-spacing:-30px;color:' + P.a + ';">' + (s || '2%') + '</div>' +
        '<div style="font:700 40px/1 ' + MONO + ';letter-spacing:8px;color:rgba(255,255,255,.6);margin-top:10px;">CRYPTO CASHBACK</div></div>' +
        spacer() + H(h, { size: 100, align: 'center', extra: 'text-align:center;' }) +
        '<div style="display:flex;justify-content:center;margin-top:26px;">' + cta(P) + '</div>', { align: 'center', gap: 0 });
  });

  /* 18 — three things numbered list */
  def(18, 'Three Things', 'List', 'mono', '3 THINGS BEFORE YOUR FIRST TAP', '', function (P, h, s) {
    function item(n, t) {
      return '<div style="display:flex;align-items:center;gap:32px;">' +
        '<div style="width:108px;height:108px;flex:none;border-radius:50%;border:3px solid ' + GOLD + ';display:flex;align-items:center;justify-content:center;font:800 56px/1 ' + FONT + ';color:' + GOLD + ';">' + n + '</div>' +
        '<div style="font:600 50px/1.1 ' + FONT + ';color:#fff;">' + t + '</div></div>';
    }
    return glow(GOLD, '50%', '20%', 700, .14) +
      col(mark(P) + '<div style="margin-top:20px;">' + H(h, { size: 96, color: '#fff' }) + '</div>' + spacer() +
        '<div style="display:flex;flex-direction:column;gap:46px;">' + item('1', 'Add it to Apple Pay') + item('2', 'Pick your crypto') + item('3', 'Tap anywhere') + '</div>' +
        spacer() + cta(P, { bg: GOLD }), { gap: 0 });
  });

  /* 19 — card fan */
  def(19, 'Card Fan', 'Card hero', 'violet', 'PICK YOUR CRYPTO', '', function (P, h, s) {
    var fan = '<div style="position:relative;width:760px;height:560px;display:flex;align-items:center;justify-content:center;">' +
      '<div style="position:absolute;transform:rotate(-18deg) translateX(-120px);">' + makeCard(P, { variant: 'metal', w: 520 }) + '</div>' +
      '<div style="position:absolute;transform:rotate(0deg);z-index:2;">' + makeCard(P, { variant: 'holo', w: 520, glow: true }) + '</div>' +
      '<div style="position:absolute;transform:rotate(18deg) translateX(120px);">' + makeCard(P, { variant: 'gold', w: 520 }) + '</div></div>';
    return glow(P.a, '50%', '40%', 880, .24) +
      col(mark(P) + spacer() + '<div style="display:flex;justify-content:center;">' + fan + '</div>' + spacer() +
        H(h, { size: 138 }) + '<div style="margin-top:28px;">' + cta(P) + '</div>', { gap: 0 });
  });

  /* 20 — synthwave horizon */
  def(20, 'Synthwave', 'Abstract', 'magenta', 'THE FUTURE OF SPENDING', '', function (P, h, s) {
    return '<div style="position:absolute;inset:0;background:linear-gradient(180deg,' + P.bg + ' 30%,' + hexA(P.a, .12) + ');"></div>' +
      '<div style="position:absolute;left:50%;top:40%;width:460px;height:460px;transform:translate(-50%,-50%);border-radius:50%;background:linear-gradient(' + P.a2 + ',' + P.a + ');box-shadow:0 0 120px ' + hexA(P.a, .6) + ';"></div>' +
      gridFloor(P.a) +
      col(eyebrow('// DEFYCARD 2.0', P, { color: P.a2 }) + spacer() +
        '<div style="display:flex;justify-content:center;">' + makeCard(P, { variant: 'neon', w: 560, glow: true, transform: 'rotate(-6deg)' }) + '</div>' +
        spacer() + H(h, { size: 122, color: '#fff' }) + '<div style="display:flex;justify-content:space-between;align-items:flex-end;margin-top:28px;">' + mark(P) + cta(P) + '</div>', { gap: 0 });
  });

  /* 21 — coin rain */
  def(21, 'Coin Rain', 'Coins', 'gold', 'RAIN OR SHINE, SPEND CRYPTO', '', function (P, h, s) {
    var rain = '';
    var defs = [['&#8383;', BTC], ['&#926;', ETH], ['$', USDC]];
    var rcx = [240, 540, 840], rcy = [210, 410, 610];
    for (var i = 0; i < 9; i++) {
      var d = defs[i % 3], sz = 110 + ((i * 37) % 3) * 22;
      var rl = rcx[i % 3] - sz / 2, rt = rcy[Math.floor(i / 3)] - sz / 2;
      rain += '<div style="position:absolute;left:' + rl + 'px;top:' + rt + 'px;">' + coin(d[0], d[1][0], d[1][1], sz, 'transform:rotate(' + ((i * 17) % 22 - 11) + 'deg);') + '</div>';
    }
    return glow(P.a, '50%', '30%', 760, .16) + rain +
      col('<div style="flex:1;"></div>' + H(h, { size: 132 }) +
        '<div style="display:flex;justify-content:space-between;align-items:flex-end;margin-top:30px;">' + mark(P) + cta(P) + '</div>', { gap: 0, justify: 'flex-end' });
  });

  /* 22 — map pins worldwide */
  def(22, 'Map Pins', 'Concept', 'emerald', 'ACCEPTED WORLDWIDE', '', function (P, h, s) {
    var pins = '';
    for (var i = 0; i < 7; i++) {
      var x = (i * 137 + 80) % 820 + 40, y = (i * 91) % 360 + 60;
      pins += '<div style="position:absolute;left:' + x + 'px;top:' + y + 'px;width:34px;height:34px;border-radius:50% 50% 50% 0;background:' + P.a + ';transform:rotate(-45deg);box-shadow:0 0 24px ' + hexA(P.a, .7) + ';"></div>';
    }
    var mapbox = '<div style="position:relative;width:100%;height:520px;border-radius:34px;border:1px solid ' + hexA(P.a, .3) + ';background:' +
      'repeating-linear-gradient(0deg,transparent 0 64px,' + hexA(P.a, .12) + ' 64px 65px),repeating-linear-gradient(90deg,transparent 0 64px,' + hexA(P.a, .1) + ' 64px 65px),' + hexA(P.a, .04) + ';overflow:hidden;">' + pins + '</div>';
    return col(mark(P) + spacer() + mapbox + spacer() + H(h, { size: 132 }) +
      '<div style="margin-top:28px;">' + pill('190+ COUNTRIES &bull; 80M+ MERCHANTS', P, { fs: 30 }) + '</div>', { gap: 0 });
  });

  /* 23 — scan / QR tap zone */
  def(23, 'Scan Zone', 'Interaction', 'cyan', 'SCAN. TAP. SPEND.', '', function (P, h, s) {
    var qr = '';
    for (var i = 0; i < 36; i++) { qr += '<div style="background:' + (Math.random() > 0.45 ? '#0a0a0b' : 'transparent') + ';"></div>'; }
    var qrbox = '<div style="width:420px;height:420px;background:#fff;border-radius:28px;padding:36px;box-shadow:0 0 80px ' + hexA(P.a, .4) + ';"><div style="width:100%;height:100%;display:grid;grid-template-columns:repeat(6,1fr);grid-template-rows:repeat(6,1fr);gap:6px;">' + qr + '</div></div>';
    return glow(P.a, '50%', '40%', 720, .2) +
      col(eyebrow('IN-APP', P) + spacer() + '<div style="display:flex;justify-content:center;">' + qrbox + '</div>' + spacer() +
        H(h, { size: 144 }) + '<div style="display:flex;justify-content:space-between;align-items:flex-end;margin-top:30px;">' + mark(P) + cta(P) + '</div>', { gap: 0 });
  });

  /* 24 — cash motion */
  def(24, 'Cash Motion', 'Stat', 'lime', 'CASH BACK, INSTANTLY', 'UP TO 5%', function (P, h, s) {
    var bills = '';
    var bx = [210, 540, 870], by = [165, 380];
    for (var i = 0; i < 6; i++) {
      var x = bx[i % 3] - 75, y = by[Math.floor(i / 3)] - 45;
      bills += '<div style="position:absolute;left:' + x + 'px;top:' + y + 'px;width:150px;height:90px;border-radius:10px;background:linear-gradient(135deg,' + P.a + ',' + P.a2 + ');display:flex;align-items:center;justify-content:center;font:800 44px/1 ' + FONT + ';color:rgba(0,0,0,.6);transform:rotate(' + ((i * 13) % 22 - 11) + 'deg);box-shadow:0 16px 36px rgba(0,0,0,.4);">$</div>';
    }
    return bills + glow(P.a, '50%', '60%', 800, .16) +
      col('<div style="flex:1;"></div>' + statBlock(s || '5%', P, { size: 300, label: 'CASHBACK IN CRYPTO' }) +
        '<div style="margin-top:24px;">' + H(h, { size: 118 }) + '</div>' +
        '<div style="display:flex;justify-content:space-between;align-items:flex-end;margin-top:28px;">' + mark(P) + cta(P) + '</div>', { gap: 0, justify: 'flex-end' });
  });

  /* 25 — mono manifesto */
  def(25, 'Mono Manifesto', 'Typography', 'mono', 'SPEND CRYPTO ANYWHERE', '', function (P, h, s) {
    return col(mark(P) + spacer() +
      H(h, { size: 230, lh: 0.9, ls: '-8px' }) +
      '<div style="margin-top:40px;font:400 44px/1.4 ' + FONT + ';color:rgba(255,255,255,.6);max-width:760px;">A real card. Real crypto. Tap to pay at 80M+ places.</div>' +
      spacer() + '<div style="display:flex;justify-content:space-between;align-items:flex-end;">' + pill('CRYPTO CARD', P, { color: '#fff', bc: 'rgba(255,255,255,.4)', bg: 'transparent' }) + cta(P, { bg: '#fff' }) + '</div>', { gap: 0 });
  });

  /* 26 — emerald cashback */
  def(26, 'Earn While You Spend', 'Stat', 'emerald', 'EARN WHILE YOU SPEND', '2% BACK', function (P, h, s) {
    return glow(P.a, '70%', '30%', 760, .22) +
      col(mark(P) + spacer() +
        '<div style="display:flex;align-items:center;gap:50px;">' +
        '<div style="font:900 360px/0.8 ' + FONT + ';letter-spacing:-16px;color:' + P.a + ';">' + ((s || '2%').match(/\d+%/) ? (s.match(/\d+%/)[0]) : '2%') + '</div>' +
        makeCard(P, { variant: 'neon', w: 380, glow: true, transform: 'rotate(8deg)' }) + '</div>' +
        spacer() + H(h, { size: 122 }) + '<div style="margin-top:28px;">' + cta(P) + '</div>', { gap: 0 });
  });

  /* 27 — orange burst rays */
  def(27, 'Orange Burst', 'Abstract', 'orange', 'INSTANT. ANYWHERE.', '', function (P, h, s) {
    return '<div style="position:absolute;left:50%;top:42%;width:1500px;height:1500px;transform:translate(-50%,-50%);background:repeating-conic-gradient(from 0deg,' + hexA(P.a, .14) + ' 0deg 12deg,transparent 12deg 24deg);"></div>' +
      glow(P.a, '50%', '42%', 700, .4) +
      col(eyebrow('NO WAITING', P) + spacer() +
        '<div style="display:flex;justify-content:center;">' + makeCard(P, { variant: 'metal', w: 640, glow: true, transform: 'rotate(-5deg)' }) + '</div>' +
        spacer() + H(h, { size: 150 }) + '<div style="display:flex;justify-content:space-between;align-items:flex-end;margin-top:30px;">' + mark(P) + cta(P) + '</div>', { gap: 0 });
  });

  /* 28 — magenta glow card + apple pay */
  def(28, 'Drop In Apple Pay', 'Interaction', 'magenta', 'DROP IN APPLE PAY', '', function (P, h, s) {
    var wallet = '<div style="width:680px;background:linear-gradient(160deg,#1c1c22,#0c0c0e);border:1px solid rgba(255,255,255,.12);border-radius:46px;padding:56px;display:flex;flex-direction:column;gap:-40px;box-shadow:0 50px 100px rgba(0,0,0,.6);">' +
      makeCard(P, { variant: 'metal', w: 560 }) +
      '<div style="margin-top:-220px;transform:translateY(0);">' + makeCard(P, { variant: 'gold', w: 560 }) + '</div></div>';
    return glow(P.a, '50%', '40%', 820, .24) +
      col(eyebrow('TAP TO ADD', P) + spacer() + '<div style="display:flex;justify-content:center;">' + wallet + '</div>' + spacer() +
        H(h, { size: 134 }) + '<div style="display:flex;justify-content:space-between;align-items:flex-end;margin-top:30px;">' + mark(P) + cta(P) + '</div>', { gap: 0 });
  });

  /* 29 — ice frost */
  def(29, 'Ice Frost', 'Card hero', 'ice', 'COOL, CALM, CRYPTO', '', function (P, h, s) {
    return '<div style="position:absolute;inset:0;background:linear-gradient(160deg,' + P.bg + ',' + P.bg2 + ');"></div>' +
      glow(P.a, '40%', '30%', 760, .3) + glow('#fff', '72%', '66%', 560, .14) +
      col(mark(P) + spacer() +
        '<div style="display:flex;justify-content:center;">' + makeCard(P, { variant: 'glass', w: 700, glow: true, transform: 'rotate(-4deg)' }) + '</div>' +
        spacer() + H(h, { size: 138, color: P.a2 }) + '<div style="margin-top:28px;">' + cta(P) + '</div>', { gap: 0 });
  });

  /* 30 — lime electric */
  def(30, 'Charge Up', 'Card hero', 'lime', 'CHARGE UP YOUR WALLET', '', function (P, h, s) {
    return glow(P.a, '50%', '38%', 820, .22) +
      '<div style="position:absolute;left:50%;top:40%;width:560px;height:560px;transform:translate(-50%,-50%);border-radius:50%;border:2px solid ' + hexA(P.a, .4) + ';"></div>' +
      col(eyebrow('POWERED BY CRYPTO', P) + spacer() +
        '<div style="display:flex;justify-content:center;">' + makeCard(P, { variant: 'neon', w: 680, glow: true }) + '</div>' +
        spacer() + H(h, { size: 130, color: '#fff' }) + '<div style="display:flex;justify-content:space-between;align-items:flex-end;margin-top:28px;">' + mark(P) + cta(P) + '</div>', { gap: 0 });
  });

  /* 31 — card from phone */
  def(31, 'Phone Is Your Card', 'Lifestyle', 'violet', 'YOUR PHONE IS YOUR CARD', '', function (P, h, s) {
    var phone = '<div style="width:420px;height:760px;border-radius:60px;background:linear-gradient(160deg,#1a1a22,#0a0a0c);border:10px solid #050507;position:relative;overflow:hidden;box-shadow:0 50px 100px rgba(0,0,0,.6);">' +
      '<div style="position:absolute;left:50%;top:30px;width:130px;height:32px;background:#050507;border-radius:20px;transform:translateX(-50%);"></div>' +
      '<div style="position:absolute;left:0;right:0;top:130px;display:flex;justify-content:center;">' + makeCard(P, { variant: 'holo', w: 330, glow: true, transform: 'translateY(-40px) rotate(-4deg)' }) + '</div>' +
      glow(P.a, '50%', '70%', 420, .4) + '</div>';
    return glow(P.a, '50%', '40%', 800, .2) +
      col(mark(P) + spacer() + '<div style="display:flex;justify-content:center;">' + phone + '</div>' + spacer() +
        H(h, { size: 120 }) + '<div style="margin-top:26px;">' + cta(P) + '</div>', { gap: 0 });
  });

  /* 32 — wallet row add to apple pay */
  def(32, 'Add To Wallet', 'Interaction', 'mono', 'ADD TO APPLE PAY', '', function (P, h, s) {
    var btn = '<div style="display:inline-flex;align-items:center;gap:20px;padding:32px 60px;border-radius:24px;background:#fff;color:#0a0a0b;font:700 56px/1 ' + FONT + ';">&#63743;&nbsp;Pay</div>';
    return col(mark(P) + spacer() +
      '<div style="display:flex;justify-content:center;margin-bottom:48px;">' + makeCard(P, { variant: 'metal', w: 640, glow: true, transform: 'rotate(-4deg)' }) + '</div>' +
      '<div style="display:flex;justify-content:center;">' + btn + '</div>' +
      spacer() + H(h, { size: 130, align: 'center', extra: 'text-align:center;' }) +
      '<div style="display:flex;justify-content:center;margin-top:26px;">' + cta(P, { bg: '#fff' }) + '</div>', { align: 'center', gap: 0 });
  });

  /* 33 — price ticker up */
  def(33, 'Spend The Gains', 'Data', 'emerald', 'SPEND THE GAINS', '', function (P, h, s) {
    var bars = '<div style="display:flex;align-items:flex-end;gap:18px;height:420px;">';
    var hs = [40, 55, 48, 70, 62, 85, 78, 100];
    for (var i = 0; i < hs.length; i++) bars += '<div style="width:88px;height:' + hs[i] + '%;border-radius:14px 14px 0 0;background:linear-gradient(' + P.a2 + ',' + P.a + ');box-shadow:0 0 30px ' + hexA(P.a, .4) + ';"></div>';
    bars += '</div>';
    return glow(P.a, '60%', '34%', 700, .18) +
      col(mark(P) + '<div style="margin-top:16px;display:flex;align-items:baseline;gap:20px;"><span style="font:900 120px/1 ' + FONT + ';color:#fff;">&#9650;</span><span style="font:700 60px/1 ' + MONO + ';color:' + P.a + ';">+38% YTD</span></div>' +
        spacer() + '<div style="display:flex;justify-content:center;">' + bars + '</div>' + spacer() +
        H(h, { size: 138 }) + '<div style="margin-top:26px;">' + cta(P) + '</div>', { gap: 0 });
  });

  /* 34 — merchant grid (b-roll tiles) */
  def(34, 'Works Where You Shop', 'Concept', 'cyan', 'WORKS WHERE YOU SHOP', '', function (P, h, s) {
    var tiles = '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:24px;width:100%;">';
    for (var i = 0; i < 9; i++) tiles += '<div style="aspect-ratio:1;border-radius:26px;background:repeating-linear-gradient(125deg,#15151a 0 22px,#0f0f14 22px 44px);border:1px solid ' + hexA(P.a, .2) + ';display:flex;align-items:center;justify-content:center;font:700 22px/1 ' + MONO + ';color:rgba(255,255,255,.3);letter-spacing:2px;">LOGO</div>';
    tiles += '</div>';
    return col(mark(P) + spacer() + tiles + spacer() + H(h, { size: 120 }) +
      '<div style="margin-top:24px;">' + cta(P) + '</div>', { gap: 0 });
  });

  /* 35 — big coin centerpiece */
  def(35, 'BTC To Burgers', 'Coins', 'gold', 'BTC TO BURGERS', '', function (P, h, s) {
    return glow(BTC[0], '50%', '40%', 820, .3) +
      col(eyebrow('SPEND IT IRL', P) + spacer() +
        '<div style="display:flex;justify-content:center;">' + coin('&#8383;', BTC[0], BTC[1], 520, 'box-shadow:0 30px 80px rgba(0,0,0,.6),inset 0 6px 16px rgba(255,255,255,.55);') + '</div>' +
        spacer() + H(h, { size: 150 }) + '<div style="display:flex;justify-content:space-between;align-items:flex-end;margin-top:30px;">' + mark(P) + cta(P) + '</div>', { gap: 0 });
  });

  /* 36 — holo text */
  def(36, 'Defy The Bank', 'Typography', 'holo', 'DEFY THE BANK', '', function (P, h, s) {
    var holoText = '<div style="font:900 280px/0.86 ' + FONT + ';letter-spacing:-10px;text-transform:uppercase;background:conic-gradient(from 200deg,#7af9ff,#b79bff,#ff7ae0,#ffe27a,#7af9ff);-webkit-background-clip:text;background-clip:text;color:transparent;text-wrap:balance;">' + h + '</div>';
    return glow('#ff9ae0', '30%', '40%', 700, .22) + glow('#7af9ff', '72%', '60%', 700, .22) +
      col(mark(P) + spacer() + holoText + spacer() +
        '<div style="display:flex;justify-content:space-between;align-items:flex-end;">' + pill('CRYPTO CARD', P, { color: '#fff', bc: 'rgba(255,255,255,.4)', bg: 'transparent' }) + cta(P) + '</div>', { gap: 0 });
  });

  /* 37 — spotlight card on pedestal */
  def(37, 'Spotlight', 'Card hero', 'gold', 'MEET DEFYCARD', '', function (P, h, s) {
    return '<div style="position:absolute;left:50%;top:0;width:760px;height:1100px;transform:translateX(-50%);background:linear-gradient(' + hexA(P.a, .26) + ',transparent 80%);clip-path:polygon(38% 0,62% 0,100% 100%,0 100%);"></div>' +
      col(eyebrow('NOW LIVE', P) + spacer() +
        '<div style="display:flex;justify-content:center;">' + makeCard(P, { variant: 'metal', w: 700, glow: true, transform: 'rotate(-3deg)' }) + '</div>' +
        '<div style="height:30px;margin-top:20px;background:radial-gradient(ellipse at center,rgba(0,0,0,.5),transparent 70%);"></div>' +
        spacer() + H(h, { size: 150, align: 'center', extra: 'text-align:center;' }) +
        '<div style="display:flex;justify-content:center;margin-top:26px;">' + cta(P) + '</div>', { align: 'center', gap: 0 });
  });

  /* 38 — receipt long, cashback highlighted */
  def(38, 'Every Swipe Pays', 'Concept', 'emerald', 'EVERY SWIPE PAYS BACK', '2% BACK', function (P, h, s) {
    function line(t, v, hi) { return '<div style="display:flex;justify-content:space-between;font:' + (hi ? 700 : 400) + ' ' + (hi ? 40 : 32) + 'px/1 ' + MONO + ';color:' + (hi ? P.a : '#fff') + ';margin:18px 0;">' + t + '<span>' + v + '</span></div>'; }
    var rec = '<div style="width:560px;background:' + hexA('#fff', .05) + ';border:1px solid ' + hexA(P.a, .3) + ';border-radius:24px;padding:50px;">' +
      '<div style="font:700 34px/1 ' + FONT + ';color:#fff;letter-spacing:1px;margin-bottom:20px;">RECENT ACTIVITY</div>' +
      line('Coffee &bull; ETH', '+2% &#8383;') + line('Groceries &bull; USDC', '+2% &#8383;') + line('Fuel &bull; BTC', '+2% &#8383;') +
      '<div style="border-top:2px dashed ' + hexA('#fff', .2) + ';margin:24px 0;"></div>' + line('Earned this month', s || '2% BACK', true) + '</div>';
    return glow(P.a, '50%', '34%', 720, .16) +
      col(mark(P) + spacer() + '<div style="display:flex;justify-content:center;">' + rec + '</div>' + spacer() +
        H(h, { size: 120 }) + '<div style="margin-top:26px;">' + cta(P) + '</div>', { gap: 0 });
  });

  /* 39 — notification stack */
  def(39, 'Approved In Seconds', 'Lifestyle', 'mono', 'APPROVED IN SECONDS', '', function (P, h, s) {
    function n(t, sub, op) {
      return '<div style="width:760px;background:rgba(255,255,255,' + (0.12 * op) + ');border:1px solid rgba(255,255,255,.14);backdrop-filter:blur(12px);border-radius:38px;padding:40px;display:flex;align-items:center;gap:26px;opacity:' + (0.5 + 0.5 * op) + ';">' +
        glyph(PAL.gold, 72) + '<div><div style="font:700 38px/1 ' + FONT + ';color:#fff;">' + t + '</div><div style="font:400 30px/1.3 ' + FONT + ';color:rgba(255,255,255,.6);margin-top:6px;">' + sub + '</div></div></div>';
    }
    return col(mark(P) + spacer() +
      '<div style="display:flex;flex-direction:column;gap:24px;align-items:center;">' + n('Approved', '$12.40 &bull; Tap to pay', 0.6) + n('Approved', '$54.00 &bull; Apple Pay', 0.8) + n('Approved', '$8.75 &bull; Online', 1) + '</div>' +
      spacer() + H(h, { size: 124, color: PAL.gold.a }) +
      '<div style="margin-top:26px;">' + cta(PAL.gold) + '</div>', { align: 'center', gap: 0 });
  });

  /* 40 — globe wireframe + card */
  def(40, 'Borderless', 'Concept', 'ice', 'BORDERLESS SPENDING', '', function (P, h, s) {
    var globe = '<div style="position:relative;width:620px;height:620px;border-radius:50%;border:2px solid ' + hexA(P.a, .5) + ';box-shadow:0 0 90px ' + hexA(P.a, .3) + ';overflow:hidden;">' +
      '<div style="position:absolute;inset:0;border-radius:50%;background:radial-gradient(circle at 40% 35%,' + hexA(P.a, .18) + ',transparent 70%);"></div>';
    for (var i = 1; i < 5; i++) globe += '<div style="position:absolute;left:50%;top:50%;width:' + (i * 24) + '%;height:100%;transform:translate(-50%,-50%);border-radius:50%;border:1px solid ' + hexA(P.a, .35) + ';"></div>';
    globe += '<div style="position:absolute;left:0;right:0;top:50%;height:1px;background:' + hexA(P.a, .4) + ';"></div></div>';
    return col(mark(P) + spacer() + '<div style="display:flex;justify-content:center;">' + globe + '</div>' + spacer() +
      H(h, { size: 126 }) + '<div style="display:flex;justify-content:space-between;align-items:flex-end;margin-top:28px;">' + pill('NO FX FEES', P) + cta(P) + '</div>', { gap: 0 });
  });

  /* 41 — diagonal duo-tone */
  def(41, 'Crypto Meets Life', 'Abstract', 'magenta', 'CRYPTO MEETS REAL LIFE', '', function (P, h, s) {
    return '<div style="position:absolute;inset:0;background:' + P.bg + ';"></div>' +
      '<div style="position:absolute;inset:0;background:linear-gradient(120deg,' + hexA(P.a, .9) + ' 0 48%,transparent 48%);"></div>' +
      '<div style="position:absolute;inset:0;background:linear-gradient(120deg,transparent 52%,' + hexA(P.a2, .14) + ' 52%);"></div>' +
      col('<div style="display:flex;justify-content:space-between;align-items:center;">' + mark(P, { color: '#0a0a0b' }) + eyebrow('DEFYCARD', P, { color: '#0a0a0b' }) + '</div>' +
        spacer() + '<div style="display:flex;justify-content:center;">' + makeCard(P, { variant: 'metal', w: 660, glow: true, transform: 'rotate(-8deg)' }) + '</div>' +
        spacer() + H(h, { size: 124, color: '#fff' }) + '<div style="margin-top:28px;">' + cta(P, { bg: '#0a0a0b' }) + '</div>', { gap: 0 });
  });

  /* 42 — card + confetti gold */
  def(42, 'Tap Into Gold', 'Card hero', 'gold', 'TAP INTO GOLD', '2% BACK', function (P, h, s) {
    var conf = '';
    for (var i = 0; i < 22; i++) { var x = (i * 53 + 50) % 960 + 60, y = (i * 89) % 320 + 90, sz = 12 + (i % 3) * 10; conf += '<div style="position:absolute;left:' + x + 'px;top:' + y + 'px;width:' + sz + 'px;height:' + sz + 'px;background:' + (i % 2 ? P.a : P.a2) + ';border-radius:3px;transform:rotate(' + (i * 31) + 'deg);"></div>'; }
    return conf + glow(P.a, '50%', '46%', 800, .24) +
      col('<div style="flex:1;"></div>' + '<div style="display:flex;justify-content:center;">' + makeCard(P, { variant: 'gold', w: 640, glow: true, transform: 'rotate(-5deg)' }) + '</div>' +
        spacer() + (s ? '<div style="margin-bottom:18px;">' + pill(s, P, { color: '#0a0a0b', bg: P.a, bc: P.a, fs: 40 }) + '</div>' : '') +
        H(h, { size: 150, color: P.a2 }) +
        '<div style="display:flex;justify-content:space-between;align-items:flex-end;margin-top:28px;">' + mark(P) + cta(P) + '</div>', { gap: 0 });
  });

  /* 43 — instant setup stopwatch */
  def(43, 'Set Up In 60s', 'Stat', 'cyan', 'SET UP IN 60 SECONDS', '60s', function (P, h, s) {
    var ring = '<div style="position:relative;width:520px;height:520px;border-radius:50%;background:conic-gradient(' + P.a + ' 0deg 300deg,' + hexA('#fff', .08) + ' 300deg);display:flex;align-items:center;justify-content:center;box-shadow:0 0 80px ' + hexA(P.a, .35) + ';">' +
      '<div style="width:430px;height:430px;border-radius:50%;background:' + P.bg + ';display:flex;flex-direction:column;align-items:center;justify-content:center;">' +
      '<div style="font:900 200px/0.8 ' + FONT + ';color:' + P.a + ';letter-spacing:-8px;">' + (s || '60s') + '</div>' +
      '<div style="font:700 32px/1 ' + MONO + ';letter-spacing:5px;color:rgba(255,255,255,.6);margin-top:10px;">TO FIRST TAP</div></div></div>';
    return col(mark(P) + spacer() + '<div style="display:flex;justify-content:center;">' + ring + '</div>' + spacer() +
      H(h, { size: 124, align: 'center', extra: 'text-align:center;' }) +
      '<div style="display:flex;justify-content:center;margin-top:26px;">' + cta(P) + '</div>', { align: 'center', gap: 0 });
  });

  /* 44 — POS terminal */
  def(44, 'Tap At Any Terminal', 'Interaction', 'violet', 'TAP AT ANY TERMINAL', '', function (P, h, s) {
    var term = '<div style="width:420px;background:linear-gradient(160deg,#1c1c24,#0c0c10);border:1px solid rgba(255,255,255,.1);border-radius:36px;padding:44px;box-shadow:0 50px 100px rgba(0,0,0,.6);">' +
      '<div style="width:100%;height:200px;border-radius:18px;background:' + hexA(P.a, .12) + ';border:1px solid ' + hexA(P.a, .4) + ';display:flex;align-items:center;justify-content:center;font:800 80px/1 ' + FONT + ';color:' + P.a + ';">$</div>' +
      '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:18px;margin-top:30px;">' +
      Array.apply(null, Array(9)).map(function () { return '<div style="aspect-ratio:1.4;border-radius:12px;background:rgba(255,255,255,.06);"></div>'; }).join('') + '</div></div>';
    return glow(P.a, '50%', '38%', 760, .2) +
      col(eyebrow('CONTACTLESS', P) + spacer() +
        '<div style="display:flex;justify-content:center;align-items:center;gap:40px;">' + term +
        makeCard(P, { variant: 'neon', w: 300, glow: true, transform: 'rotate(-90deg) translateX(20px)' }) + '</div>' +
        spacer() + H(h, { size: 122 }) + '<div style="display:flex;justify-content:space-between;align-items:flex-end;margin-top:28px;">' + mark(P) + cta(P) + '</div>', { gap: 0 });
  });

  /* 45 — coins orbit card */
  def(45, 'Crypto In Orbit', 'Coins', 'orange', 'CRYPTO IN ORBIT', '', function (P, h, s) {
    var orbit = '<div style="position:relative;width:760px;height:620px;display:flex;align-items:center;justify-content:center;">' +
      '<div style="position:absolute;width:680px;height:560px;border-radius:50%;border:2px dashed ' + hexA(P.a, .35) + ';"></div>' +
      makeCard(P, { variant: 'metal', w: 460, glow: true, transform: 'rotate(-6deg)' }) +
      '<div style="position:absolute;left:30px;top:60px;">' + coin('&#8383;', BTC[0], BTC[1], 150) + '</div>' +
      '<div style="position:absolute;right:40px;top:120px;">' + coin('&#926;', ETH[0], ETH[1], 130) + '</div>' +
      '<div style="position:absolute;left:120px;bottom:30px;">' + coin('$', USDC[0], USDC[1], 120) + '</div></div>';
    return glow(P.a, '50%', '40%', 820, .22) +
      col(mark(P) + spacer() + '<div style="display:flex;justify-content:center;">' + orbit + '</div>' + spacer() +
        H(h, { size: 138 }) + '<div style="margin-top:28px;">' + cta(P) + '</div>', { gap: 0 });
  });

  /* 46 — neon outline card */
  def(46, 'Outline The Future', 'Card hero', 'lime', 'OUTLINE THE FUTURE', '', function (P, h, s) {
    return '<div style="position:absolute;inset:0;background:repeating-linear-gradient(0deg,transparent 0 78px,' + hexA(P.a, .07) + ' 78px 80px);"></div>' +
      glow(P.a, '50%', '40%', 760, .18) +
      col(eyebrow('// CRYPTO CARD', P) + spacer() +
        '<div style="display:flex;justify-content:center;">' + makeCard(P, { variant: 'neon', w: 700, glow: true }) + '</div>' +
        spacer() + H(h, { size: 126, color: '#fff' }) + '<div style="display:flex;justify-content:space-between;align-items:flex-end;margin-top:28px;">' + mark(P) + cta(P) + '</div>', { gap: 0 });
  });

  /* 47 — mesh + glass card */
  def(47, 'Sleek Secure', 'Abstract', 'violet', 'SLEEK. SECURE. CRYPTO.', '', function (P, h, s) {
    return '<div style="position:absolute;inset:0;background:' + P.bg + ';"></div>' +
      glow(P.a, '22%', '20%', 760, .4) + glow('#22E0FF', '82%', '34%', 620, .26) + glow(P.a2, '50%', '82%', 760, .3) +
      col(mark(P) + spacer() +
        '<div style="display:flex;justify-content:center;">' + makeCard(P, { variant: 'glass', w: 700, glow: true, transform: 'rotate(4deg)' }) + '</div>' +
        spacer() + H(h, { size: 128 }) + '<div style="margin-top:28px;">' + cta(P) + '</div>', { gap: 0 });
  });

  /* 48 — percentage ring gauge */
  def(48, 'Cashback Gauge', 'Stat', 'emerald', 'CASHBACK ON EVERYTHING', '2%', function (P, h, s) {
    var num = (s || '2%');
    var ring = '<div style="position:relative;width:600px;height:600px;border-radius:50%;background:conic-gradient(' + P.a + ' 0deg 250deg,' + hexA('#fff', .07) + ' 250deg);display:flex;align-items:center;justify-content:center;box-shadow:0 0 90px ' + hexA(P.a, .35) + ';">' +
      '<div style="width:480px;height:480px;border-radius:50%;background:' + P.bg + ';display:flex;flex-direction:column;align-items:center;justify-content:center;">' +
      '<div style="font:900 280px/0.8 ' + FONT + ';color:' + P.a + ';letter-spacing:-12px;">' + num + '</div>' +
      '<div style="font:700 34px/1 ' + MONO + ';letter-spacing:5px;color:rgba(255,255,255,.6);">BACK IN CRYPTO</div></div></div>';
    return col(mark(P) + spacer() + '<div style="display:flex;justify-content:center;">' + ring + '</div>' + spacer() +
      H(h, { size: 116, align: 'center', extra: 'text-align:center;' }) +
      '<div style="display:flex;justify-content:center;margin-top:24px;">' + cta(P) + '</div>', { align: 'center', gap: 0 });
  });

  /* 49 — stat stack (why defycard) */
  def(49, 'Why DefyCard', 'List', 'gold', 'WHY DEFYCARD', '', function (P, h, s) {
    function stat(big, lab) {
      return '<div style="flex:1;background:' + hexA('#fff', .04) + ';border:1px solid ' + hexA(P.a, .25) + ';border-radius:28px;padding:46px;display:flex;flex-direction:column;gap:6px;">' +
        '<div style="font:900 120px/0.85 ' + FONT + ';color:' + P.a + ';letter-spacing:-4px;">' + big + '</div>' +
        '<div style="font:600 32px/1.2 ' + FONT + ';color:rgba(255,255,255,.7);">' + lab + '</div></div>';
    }
    return col(mark(P) + '<div style="margin-top:18px;">' + H(h, { size: 110 }) + '</div>' + spacer() +
      '<div style="display:flex;gap:26px;">' + stat('2%', 'crypto cashback') + stat('80M+', 'merchants') + '</div>' +
      '<div style="display:flex;gap:26px;margin-top:26px;">' + stat('60s', 'to set up') + stat('0', 'hidden fees') + '</div>' +
      spacer() + cta(P), { gap: 0 });
  });

  /* 50 — bold manifesto */
  def(50, 'Defy Spend Repeat', 'Typography', 'mono', 'DEFY. SPEND. REPEAT.', '', function (P, h, s) {
    var parts = h.split(/\s+/);
    var lines = parts.map(function (w, i) {
      return '<div style="font:900 ' + (Math.max(120, 280 - parts.length * 8)) + 'px/0.9 ' + FONT + ';letter-spacing:-8px;color:' + (i % 2 ? GOLD : '#fff') + ';text-transform:uppercase;">' + w + '</div>';
    }).join('');
    return glow(GOLD, '50%', '50%', 700, .12) +
      col(mark(P) + spacer() + '<div>' + lines + '</div>' + spacer() +
        '<div style="display:flex;justify-content:space-between;align-items:flex-end;">' + pill('CRYPTO CARD', P, { color: '#fff', bc: 'rgba(255,255,255,.4)', bg: 'transparent' }) + cta(P, { bg: GOLD }) + '</div>', { gap: 0 });
  });

  /* =================================================================
     wrap + public API
     ================================================================= */
  var FONT_IMPORT = "<style>@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;700&family=Space+Mono:wght@400;700&display=swap');</style>";

  function guideOverlay() {
    return '<div style="position:absolute;inset:60px;border:2px dashed ' + hexA('#00e5ff', .8) + ';pointer-events:none;"></div>' +
      '<div style="position:absolute;left:50%;top:0;bottom:0;width:1px;background:' + hexA('#ff00aa', .5) + ';pointer-events:none;"></div>' +
      '<div style="position:absolute;left:64px;top:64px;font:700 20px/1 ' + MONO + ';color:#00e5ff;pointer-events:none;">SAFE 60px</div>';
  }

  function rootWrap(inner, P, opts) {
    return FONT_IMPORT +
      '<div class="defycard-preview" style="position:relative;width:1080px;height:1920px;overflow:hidden;background:' + P.bg +
      ';font-family:' + FONT + ';color:#fff;">' +
      inner + vignette() + noise() + (opts.guides ? guideOverlay() : '') + '</div>';
  }

  function renderPreviewHTML(spec, opts) {
    opts = opts || {};
    var idx = (typeof spec === 'number') ? spec : (spec && spec.index) || 1;
    if (!BUILD[idx]) idx = 1;
    var m;
    for (var i = 0; i < META.length; i++) { if (META[i].index === idx) { m = META[i]; break; } }
    var P = PAL[m.palette];
    var hook = (spec && spec.hook != null) ? spec.hook : m.defaultHook;
    var stat = (spec && typeof spec.stat !== 'undefined') ? spec.stat : m.defaultStat;
    var inner = BUILD[idx](P, esc(hook), stat ? esc(stat) : '', !!opts.guides);
    return rootWrap(inner, P, opts);
  }

  /* PREVIEW_TEMPLATES: 50 specs (sorted by index), each with a render() helper */
  META.sort(function (a, b) { return a.index - b.index; });
  var TEMPLATES = META.map(function (m) {
    return {
      index: m.index,
      name: m.name,
      category: m.category,
      palette: m.palette,
      paletteName: PAL[m.palette].name,
      accent: PAL[m.palette].a,
      defaultHook: m.defaultHook,
      defaultStat: m.defaultStat,
      render: function (overrides, opts) {
        var spec = { index: m.index };
        if (overrides) { if (overrides.hook != null) spec.hook = overrides.hook; if (typeof overrides.stat !== 'undefined') spec.stat = overrides.stat; }
        return renderPreviewHTML(spec, opts || {});
      }
    };
  });

  window.PREVIEW_TEMPLATES = TEMPLATES;
  window.renderPreviewHTML = renderPreviewHTML;
})();
