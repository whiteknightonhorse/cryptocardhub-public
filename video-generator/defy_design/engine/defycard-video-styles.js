/* =====================================================================
   DefyCard — YouTube Shorts VIDEO design styles (overlay / caption skins)
   ---------------------------------------------------------------------
   These are TRANSPARENT overlays designed for a 1080x1920 canvas: the
   real b-roll footage shows through everywhere the overlay is empty.
   We only style the SKIN layer — captions, accents, badges, brand frame,
   card-themed motion accents, flash/pop, FTC banner, end CTA.
   The footage / voice / format / FTC / CTA destination are UNCHANGED.

   Exposes:
     window.VIDEO_STYLES                  -> array of 50 style kits
     window.renderStyle(spec, opts)       -> overlay HTML string (1080x1920)

   spec : number (1..50)  OR
          { index:Number, hook?:String, word?:String, activeIndex?:Number,
            stat?:String, phase?:'caption'|'cta', cta?:String, showFTC?:Boolean }
   opts : { guides?:Boolean, demoBroll?:Boolean }

   Placeholders honored: {{HOOK}} -> spec.hook, {{WORD}} -> active karaoke
   word (spec.word / spec.activeIndex), {{CTA}} -> spec.cta (= defycard.com).
   ===================================================================== */
(function () {
  var MONO = "'Space Mono','SFMono-Regular',Menlo,monospace";
  var GOLD = '#F7C204';
  var SAFE = 64;           // >= 60px safe margin requirement
  var CAP_BOTTOM = 372;    // caption sits in the lower third (~MarginV 480 feel)

  /* condensed-display / clean-sans / heavy-slab font feels */
  var FONTS = {
    anton:   { stack: "'Anton','Oswald',Impact,sans-serif",          size: 84,  weight: 400, ls: '0px',   lh: 1.0  },
    bebas:   { stack: "'Bebas Neue','Oswald',sans-serif",            size: 104, weight: 400, ls: '1.5px', lh: 0.94 },
    oswald:  { stack: "'Oswald','Anton',sans-serif",                 size: 80,  weight: 700, ls: '0px',   lh: 1.04 },
    archivo: { stack: "'Archivo Black','Arial Black',sans-serif",    size: 72,  weight: 400, ls: '-1px',  lh: 1.06 },
    zilla:   { stack: "'Zilla Slab',Georgia,serif",                  size: 78,  weight: 700, ls: '0px',   lh: 1.04 },
    grotesk: { stack: "'Space Grotesk','Helvetica Neue',Arial,sans-serif", size: 74, weight: 700, ls: '-1px', lh: 1.1  }
  };

  /* ---------- palettes (one accent per style; 10 across the 50) ----- */
  var PAL = {
    gold:    { name: 'Gold Luxe',        a: '#F7C204', a2: '#FFE583' },
    violet:  { name: 'Electric Violet',  a: '#9B6BFF', a2: '#C8B0FF' },
    cyan:    { name: 'Neon Cyan',        a: '#2FD9F5', a2: '#A6EEFB' },
    lime:    { name: 'Electric Lime',    a: '#B6FF3A', a2: '#DCFF94' },
    magenta: { name: 'Magenta Glow',     a: '#FF4FB0', a2: '#FF9FD6' },
    ice:     { name: 'Ice Blue',         a: '#79B6FF', a2: '#C2DDFF' },
    emerald: { name: 'Emerald',          a: '#26E091', a2: '#8EF2C7' },
    orange:  { name: 'Sunset Orange',    a: '#FF8A2A', a2: '#FFBE82' },
    mono:    { name: 'Mono White',       a: '#FFFFFF', a2: '#D2D6DC' },
    holo:    { name: 'Holographic',      a: '#7AF9FF', a2: '#FF9AE0' }
  };
  var BTC = ['#F7931A', '#FFC774'], ETH = ['#627EEA', '#A9BBF5'], USDC = ['#2775CA', '#7FB2EC'];

  /* ---------- helpers ---------------------------------------------- */
  function esc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
  function hexA(hex, a) {
    var c = hex.replace('#', '');
    if (c.length === 3) c = c.split('').map(function (x) { return x + x; }).join('');
    var n = parseInt(c, 16);
    return 'rgba(' + ((n >> 16) & 255) + ',' + ((n >> 8) & 255) + ',' + (n & 255) + ',' + a + ')';
  }
  function outline(px, color) {
    color = color || '#000';
    var s = [];
    for (var a = 0; a < 360; a += 30) {
      var dx = Math.round(Math.cos(a * Math.PI / 180) * px), dy = Math.round(Math.sin(a * Math.PI / 180) * px);
      s.push(dx + 'px ' + dy + 'px 0 ' + color);
    }
    s.push('0 6px 16px rgba(0,0,0,.55)');
    return s.join(',');
  }
  function pickActive(words) {
    var bi = 0, bl = -1;
    for (var i = 0; i < words.length; i++) {
      var l = words[i].replace(/[^\w]/g, '').length;
      if (l >= bl) { bl = l; bi = i; }
    }
    return bi;
  }

  /* ---------- brand mark atoms ------------------------------------- */
  function glyph(P, s) {
    s = s || 34; var w = Math.round(s * 1.5);
    return '<div style="width:' + w + 'px;height:' + s + 'px;border-radius:' + Math.round(s * 0.22) +
      'px;background:linear-gradient(135deg,' + P.a + ',' + P.a2 + ');position:relative;flex:none;box-shadow:0 4px 12px rgba(0,0,0,.45);">' +
      '<div style="position:absolute;left:' + Math.round(s * 0.18) + 'px;top:' + Math.round(s * 0.26) +
      'px;width:' + Math.round(s * 0.3) + 'px;height:' + Math.round(s * 0.18) + 'px;border-radius:3px;background:rgba(0,0,0,.4);"></div>' +
      '<div style="position:absolute;left:' + Math.round(s * 0.18) + 'px;top:' + Math.round(s * 0.56) +
      'px;width:' + Math.round(s * 0.58) + 'px;height:' + Math.round(s * 0.12) + 'px;border-radius:2px;background:rgba(0,0,0,.45);"></div></div>';
  }
  function brandChip(P) {
    return '<div style="position:absolute;left:' + SAFE + 'px;top:' + SAFE + 'px;display:inline-flex;align-items:center;gap:14px;' +
      'padding:14px 22px 14px 16px;border-radius:100px;background:rgba(10,10,11,.55);border:1px solid rgba(255,255,255,.16);' +
      'backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);box-shadow:0 8px 24px rgba(0,0,0,.4);">' +
      glyph(P, 34) +
      '<span style="font:700 32px/1 ' + FONTS.grotesk.stack + ';letter-spacing:-.4px;color:#fff;">Defy<span style="color:' + P.a + ';">Card</span></span></div>';
  }

  /* ---------- brand FRAME (one per style) -------------------------- */
  function frameHTML(cfg, P) {
    switch (cfg.frame) {
      case 'topbar':
        return '<div style="position:absolute;left:0;right:0;top:0;height:10px;background:linear-gradient(90deg,' + P.a + ',' + P.a2 + ');"></div>';
      case 'bottomrule':
        return '<div style="position:absolute;left:' + SAFE + 'px;right:' + SAFE + 'px;bottom:128px;height:5px;border-radius:3px;' +
          'background:linear-gradient(90deg,transparent,' + hexA(P.a, .9) + ',transparent);"></div>';
      case 'sideline':
        return '<div style="position:absolute;left:34px;top:' + (SAFE + 120) + 'px;bottom:' + (SAFE + 120) + 'px;width:6px;border-radius:4px;' +
          'background:linear-gradient(180deg,transparent,' + P.a + ',transparent);"></div>';
      case 'brackets':
        var b = function (pos) {
          var c = hexA(P.a, .9), L = 'position:absolute;width:84px;height:84px;';
          var corners = {
            tl: 'left:46px;top:46px;border-left:7px solid ' + c + ';border-top:7px solid ' + c + ';border-top-left-radius:14px;',
            tr: 'right:46px;top:46px;border-right:7px solid ' + c + ';border-top:7px solid ' + c + ';border-top-right-radius:14px;',
            bl: 'left:46px;bottom:46px;border-left:7px solid ' + c + ';border-bottom:7px solid ' + c + ';border-bottom-left-radius:14px;',
            br: 'right:46px;bottom:46px;border-right:7px solid ' + c + ';border-bottom:7px solid ' + c + ';border-bottom-right-radius:14px;'
          };
          return '<div style="' + L + corners[pos] + '"></div>';
        };
        return b('tl') + b('tr') + b('bl') + b('br');
      default:
        return '';
    }
  }

  /* ---------- card-themed MOTION accents (subtle, never over caption) */
  function motionHTML(cfg, P) {
    switch (cfg.motion) {
      case 'tap-ring':
        return '<div style="position:absolute;left:50%;top:760px;transform:translate(-50%,-50%);width:560px;height:560px;">' +
          ['1', '.62', '.38'].map(function (s, i) {
            var d = 560 * parseFloat(s);
            return '<div style="position:absolute;left:50%;top:50%;width:' + d + 'px;height:' + d + 'px;transform:translate(-50%,-50%);' +
              'border-radius:50%;border:4px solid ' + hexA(P.a, 0.18 + i * 0.22) + ';box-shadow:0 0 40px ' + hexA(P.a, .25) + ';"></div>';
          }).join('') +
          '<div style="position:absolute;left:50%;top:50%;width:120px;height:120px;transform:translate(-50%,-50%);border-radius:50%;' +
          'background:radial-gradient(circle,' + hexA(P.a, .9) + ',' + hexA(P.a, .2) + ' 70%);box-shadow:0 0 60px ' + hexA(P.a, .6) + ';"></div></div>';
      case 'coin-burst':
        var defs = [['&#8383;', BTC, 130, 230, 380, -10], ['&#926;', ETH, 168, 540, 300, 0], ['$', USDC, 124, 850, 400, 12]];
        return defs.map(function (d) {
          var sz = d[2];
          return '<div style="position:absolute;left:' + (d[3] - sz / 2) + 'px;top:' + (d[4] - sz / 2) + 'px;width:' + sz + 'px;height:' + sz +
            'px;border-radius:50%;background:radial-gradient(circle at 36% 30%,' + d[1][1] + ',' + d[1][0] + ');display:flex;align-items:center;justify-content:center;' +
            'font:800 ' + Math.round(sz * 0.46) + 'px/1 ' + FONTS.grotesk.stack + ';color:rgba(0,0,0,.72);transform:rotate(' + d[5] + 'deg);' +
            'box-shadow:0 16px 40px rgba(0,0,0,.5),inset 0 3px 8px rgba(255,255,255,.55);">' + d[0] + '</div>';
        }).join('');
      case 'neon-edge':
        return '<div style="position:absolute;inset:36px;border-radius:42px;border:3px solid ' + hexA(P.a, .55) + ';' +
          'box-shadow:0 0 60px ' + hexA(P.a, .3) + ',inset 0 0 60px ' + hexA(P.a, .14) + ';pointer-events:none;"></div>';
      case 'ticker':
        var row = function (sym, px, chg) {
          return '<span style="display:inline-flex;align-items:center;gap:12px;font:700 30px/1 ' + MONO + ';color:#fff;">' +
            sym + ' <span style="color:' + P.a + ';">' + px + '</span> <span style="color:' + P.a + ';">&#9650;' + chg + '</span></span>';
        };
        return '<div style="position:absolute;left:0;right:0;top:188px;height:74px;display:flex;align-items:center;gap:54px;padding:0 ' + SAFE + 'px;' +
          'background:linear-gradient(90deg,rgba(10,10,11,.0),rgba(10,10,11,.6) 12%,rgba(10,10,11,.6) 88%,rgba(10,10,11,.0));overflow:hidden;">' +
          row('BTC', '$71,204', '2.4%') + row('ETH', '$3,880', '1.1%') + row('SOL', '$182', '3.0%') + row('USDC', '$1.00', '0.0%') + '</div>';
      case 'swipe':
        return '<div style="position:absolute;right:-90px;top:150px;width:520px;height:330px;border-radius:34px;transform:rotate(-18deg);' +
          'background:linear-gradient(140deg,#23232a,#0b0b0d 60%,#1b1b20);border:1px solid rgba(255,255,255,.14);box-shadow:-30px 30px 70px rgba(0,0,0,.55),0 0 70px ' + hexA(P.a, .25) + ';overflow:hidden;">' +
          '<div style="position:absolute;left:38px;top:34px;font:700 38px/1 ' + FONTS.grotesk.stack + ';color:#fff;">Defy<span style="color:' + P.a + ';">Card</span></div>' +
          '<div style="position:absolute;left:38px;top:150px;width:84px;height:62px;border-radius:12px;background:linear-gradient(135deg,' + P.a2 + ',' + P.a + ');"></div>' +
          '<div style="position:absolute;left:38px;bottom:34px;font:700 30px/1 ' + MONO + ';letter-spacing:2px;color:rgba(255,255,255,.85);">&bull;&bull;&bull;&bull; 4204</div></div>' +
          '<div style="position:absolute;right:330px;top:230px;width:240px;height:8px;border-radius:6px;background:linear-gradient(90deg,transparent,' + hexA(P.a, .8) + ');transform:rotate(-18deg);"></div>';
      case 'check':
        return '<div style="position:absolute;left:50%;top:740px;transform:translate(-50%,-50%);width:300px;height:300px;border-radius:50%;' +
          'background:radial-gradient(circle at 40% 34%,' + P.a2 + ',' + P.a + ');display:flex;align-items:center;justify-content:center;' +
          'box-shadow:0 24px 70px ' + hexA(P.a, .5) + ',inset 0 4px 12px rgba(255,255,255,.5);">' +
          '<div style="width:120px;height:64px;border-left:18px solid rgba(0,0,0,.78);border-bottom:18px solid rgba(0,0,0,.78);transform:rotate(-50deg) translate(6px,-12px);border-radius:4px;"></div></div>';
      default:
        return '';
    }
  }

  /* ---------- flash / POP accents ---------------------------------- */
  function popHTML(cfg, P) {
    switch (cfg.pop) {
      case 'sweep':
        return '<div style="position:absolute;left:-30%;top:0;width:50%;height:100%;transform:skewX(-20deg);' +
          'background:linear-gradient(90deg,transparent,' + hexA('#ffffff', .12) + ',transparent);left:18%;pointer-events:none;"></div>';
      case 'spark':
        var sp = function (x, y, s) {
          return '<div style="position:absolute;left:' + x + 'px;top:' + y + 'px;width:' + s + 'px;height:' + s + 'px;transform:translate(-50%,-50%);">' +
            '<div style="position:absolute;left:50%;top:0;bottom:0;width:4px;transform:translateX(-50%);background:linear-gradient(' + hexA(P.a, 0) + ',' + P.a + ',' + hexA(P.a, 0) + ');"></div>' +
            '<div style="position:absolute;top:50%;left:0;right:0;height:4px;transform:translateY(-50%);background:linear-gradient(90deg,' + hexA(P.a, 0) + ',' + P.a + ',' + hexA(P.a, 0) + ');"></div></div>';
        };
        return sp(170, 240, 90) + sp(930, 360, 130) + sp(250, 1180, 70) + sp(880, 1240, 100);
      case 'pulse':
        return '<div style="position:absolute;left:50%;top:760px;transform:translate(-50%,-50%);width:720px;height:720px;border-radius:50%;' +
          'border:3px solid ' + hexA(P.a, .22) + ';box-shadow:0 0 80px ' + hexA(P.a, .18) + ';pointer-events:none;"></div>';
      case 'chevron':
        return '<div style="position:absolute;left:50%;bottom:' + (CAP_BOTTOM + 250) + 'px;transform:translateX(-50%);display:flex;flex-direction:column;align-items:center;gap:6px;">' +
          ['.4', '.7', '1'].map(function (o) {
            return '<div style="width:64px;height:64px;border-right:9px solid ' + hexA(P.a, parseFloat(o)) + ';border-bottom:9px solid ' + hexA(P.a, parseFloat(o)) + ';transform:rotate(45deg);"></div>';
          }).join('') + '</div>';
      default:
        return '';
    }
  }

  /* ---------- lower-third BADGE ------------------------------------ */
  function badgeHTML(cfg, P) {
    var b = cfg.badge;
    if (!b || b === 'none') return '';
    if (b === 'chip') {
      return '<div style="display:inline-flex;align-items:center;gap:14px;padding:14px 26px 14px 16px;border-radius:100px;' +
        'background:' + hexA(P.a, .14) + ';border:2px solid ' + hexA(P.a, .65) + ';">' + glyph(P, 30) +
        '<span style="white-space:nowrap;font:800 34px/1 ' + FONTS.grotesk.stack + ';letter-spacing:1px;color:#fff;text-transform:uppercase;">DefyCard</span></div>';
    }
    if (b === 'stat') {
      return '<div style="display:inline-flex;align-items:center;gap:12px;padding:14px 30px;border-radius:100px;background:' + P.a + ';' +
        'box-shadow:0 12px 34px ' + hexA(P.a, .45) + ';"><span style="white-space:nowrap;font:800 42px/1 ' + FONTS.grotesk.stack + ';letter-spacing:.5px;color:#0a0a0b;">' +
        (cfg.badgeText || '2% BACK') + '</span></div>';
    }
    if (b === 'step') {
      var cur = cfg.step || 1, tot = cfg.steps || 3, dots = '';
      for (var i = 1; i <= tot; i++) {
        dots += '<span style="width:' + (i === cur ? 40 : 16) + 'px;height:16px;border-radius:100px;background:' + (i <= cur ? P.a : hexA('#ffffff', .3)) + ';"></span>';
      }
      return '<div style="display:inline-flex;align-items:center;gap:18px;padding:12px 26px;border-radius:100px;background:rgba(10,10,11,.6);' +
        'border:1px solid rgba(255,255,255,.16);"><span style="font:700 32px/1 ' + MONO + ';color:#fff;">' + cur + ' / ' + tot + '</span>' +
        '<span style="display:inline-flex;gap:8px;align-items:center;">' + dots + '</span></div>';
    }
    return '';
  }

  /* ---------- CAPTION (karaoke) ------------------------------------ */
  function wordSpan(w, act, cfg, P, ol) {
    var base = 'display:inline-block;white-space:nowrap;';
    switch (cfg.capStyle) {
      case 'whiteGold':
        return '<span style="' + base + 'color:' + (act ? P.a : '#fff') + ';text-shadow:' + ol + ';' + (act ? 'transform:scale(1.05);' : '') + '">' + w + '</span>';
      case 'glowWord':
        return '<span style="' + base + 'color:#fff;text-shadow:' + ol + (act ? ',0 0 26px ' + P.a + ',0 0 50px ' + hexA(P.a, .7) : '') + ';' + (act ? 'transform:scale(1.05);' : '') + '">' + w + '</span>';
      case 'box':
        return act
          ? '<span style="' + base + 'color:#0a0a0b;background:' + P.a + ';padding:.02em .2em;border-radius:12px;box-shadow:0 10px 26px ' + hexA(P.a, .5) + ';">' + w + '</span>'
          : '<span style="' + base + 'color:#fff;text-shadow:' + ol + ';">' + w + '</span>';
      case 'underline':
        return '<span style="' + base + 'color:' + (act ? P.a : '#fff') + ';text-shadow:' + ol + ';' + (act ? 'border-bottom:10px solid ' + P.a + ';padding-bottom:.04em;' : '') + '">' + w + '</span>';
      case 'allAccentBase':
        return '<span style="' + base + 'color:' + (act ? '#fff' : P.a) + ';text-shadow:' + ol + ';' + (act ? 'transform:scale(1.05);' : '') + '">' + w + '</span>';
      case 'outlineFill':
        return act
          ? '<span style="' + base + 'color:' + P.a + ';text-shadow:0 6px 16px rgba(0,0,0,.55);">' + w + '</span>'
          : '<span style="' + base + 'color:transparent;-webkit-text-stroke:3px #fff;text-stroke:3px #fff;">' + w + '</span>';
      case 'gradient':
        return act
          ? '<span style="' + base + 'background:linear-gradient(120deg,' + P.a + ',' + P.a2 + ');-webkit-background-clip:text;background-clip:text;color:transparent;filter:drop-shadow(0 6px 14px rgba(0,0,0,.5));transform:scale(1.05);">' + w + '</span>'
          : '<span style="' + base + 'color:#fff;text-shadow:' + ol + ';">' + w + '</span>';
      case 'plateBox':
        return '<span style="' + base + 'color:' + (act ? '#0a0a0b' : '#fff') + ';background:' + (act ? P.a : 'rgba(10,10,11,.62)') + ';padding:.04em .22em;border-radius:10px;' + (act ? 'box-shadow:0 10px 26px ' + hexA(P.a, .45) + ';' : '') + '">' + w + '</span>';
      case 'shadowPop':
        return '<span style="' + base + 'color:' + (act ? P.a : '#fff') + ';text-shadow:' + ol + ';' + (act ? 'transform:scale(1.08) translateY(-3px);text-shadow:' + ol + ',0 0 30px ' + hexA(P.a, .7) + ';' : '') + '">' + w + '</span>';
      case 'duo':
        return '<span style="' + base + 'color:' + (act ? P.a : '#fff') + ';text-shadow:' + ol + ';' + (act ? 'border-bottom:10px solid ' + P.a + ';' : '') + '">' + w + '</span>';
      default:
        return '<span style="' + base + 'color:#fff;text-shadow:' + ol + ';">' + w + '</span>';
    }
  }

  function captionHTML(cfg, P, hook, activeIdx) {
    var fm = FONTS[cfg.font] || FONTS.anton;
    var size = Math.round((cfg.capSize || fm.size) * (cfg.capScale || 1));
    var tt = cfg.upper === false ? 'none' : 'uppercase';
    var words = String(hook).split(/\s+/).filter(Boolean);
    if (activeIdx == null) activeIdx = pickActive(words);
    var ol = outline(cfg.outline || 6, '#000');
    var spans = words.map(function (w, i) { return wordSpan(w, i === activeIdx, cfg, P, ol); }).join('');
    var gap = Math.round(size * 0.12);
    var inner = '<div style="display:flex;flex-wrap:wrap;justify-content:center;align-items:center;gap:' + gap + 'px ' + Math.round(size * 0.28) + 'px;' +
      'max-width:920px;font-family:' + fm.stack + ';font-weight:' + fm.weight + ';font-size:' + size + 'px;line-height:' + fm.lh + ';' +
      'letter-spacing:' + fm.ls + ';text-transform:' + tt + ';text-align:center;">' + spans + '</div>';
    if (cfg.plate) {
      inner = '<div style="padding:24px 40px;border-radius:28px;background:rgba(8,8,10,.5);backdrop-filter:blur(6px);-webkit-backdrop-filter:blur(6px);">' + inner + '</div>';
    }
    return inner;
  }

  function captionZone(cfg, P, hook, activeIdx) {
    var badge = badgeHTML(cfg, P);
    return '<div style="position:absolute;left:48px;right:48px;bottom:' + CAP_BOTTOM + 'px;display:flex;flex-direction:column;align-items:center;gap:30px;">' +
      (badge ? '<div>' + badge + '</div>' : '') + captionHTML(cfg, P, hook, activeIdx) + '</div>';
  }

  /* ---------- end CTA ---------------------------------------------- */
  function ctaZone(cfg, P, cta) {
    var fm = FONTS.anton;
    return '<div style="position:absolute;left:48px;right:48px;bottom:' + (CAP_BOTTOM + 30) + 'px;display:flex;flex-direction:column;align-items:center;gap:40px;">' +
      '<div style="font:700 36px/1 ' + MONO + ';letter-spacing:8px;color:' + P.a + ';text-shadow:0 4px 14px rgba(0,0,0,.6);">TAP THE LINK</div>' +
      '<div style="display:inline-flex;align-items:center;gap:22px;padding:30px 50px;border-radius:18px;background:#0a0a0b;' +
      'border-bottom:8px solid ' + P.a + ';box-shadow:0 26px 70px rgba(0,0,0,.6),0 0 70px ' + hexA(P.a, .25) + ';">' +
      glyph(P, 60) +
      '<span style="font-family:' + fm.stack + ';font-weight:400;font-size:100px;line-height:0.9;color:#fff;letter-spacing:-1px;">' + (cta || 'defycard.com') + '</span></div></div>';
  }

  /* ---------- FTC banner (always-on, very bottom) ------------------ */
  function ftcHTML(P) {
    return '<div style="position:absolute;left:0;right:0;bottom:0;height:96px;display:flex;align-items:center;justify-content:center;gap:14px;' +
      'background:linear-gradient(180deg,rgba(0,0,0,0),rgba(0,0,0,.84) 42%);">' +
      '<span style="width:10px;height:10px;border-radius:50%;background:' + P.a + ';flex:none;"></span>' +
      '<span style="font:600 25px/1.2 ' + MONO + ';letter-spacing:.3px;color:rgba(255,255,255,.92);text-align:center;">' +
      'Affiliate links \u2014 we may earn commission. Not financial advice.</span></div>';
  }

  function guides() {
    return '<div style="position:absolute;inset:60px;border:2px dashed rgba(0,229,255,.8);pointer-events:none;"></div>' +
      '<div style="position:absolute;left:50%;top:0;bottom:0;width:1px;background:rgba(255,0,170,.5);"></div>' +
      '<div style="position:absolute;left:0;right:0;bottom:' + CAP_BOTTOM + 'px;height:1px;background:rgba(255,200,0,.6);"></div>' +
      '<div style="position:absolute;left:64px;top:64px;font:700 20px/1 ' + MONO + ';color:#00e5ff;">SAFE 60px</div>' +
      '<div style="position:absolute;left:64px;bottom:' + (CAP_BOTTOM + 8) + 'px;font:700 20px/1 ' + MONO + ';color:#ffc800;">CAPTION BASELINE</div>';
  }

  function demoBroll() {
    return '<div style="position:absolute;inset:0;background:#0d0d11;background-image:repeating-linear-gradient(125deg,#1a1a22 0 36px,#131318 36px 72px);"></div>' +
      '<div style="position:absolute;inset:0;background:linear-gradient(180deg,rgba(0,0,0,.25),rgba(0,0,0,.5));"></div>' +
      '<div style="position:absolute;left:50%;top:46%;transform:translate(-50%,-50%);font:700 30px/1 ' + MONO + ';letter-spacing:6px;color:rgba(255,255,255,.22);">B-ROLL FOOTAGE</div>';
  }

  var FONT_IMPORT = "<style>@import url('https://fonts.googleapis.com/css2?family=Anton&family=Archivo+Black&family=Bebas+Neue&family=Oswald:wght@500;700&family=Zilla+Slab:wght@700&family=Space+Grotesk:wght@400;700&family=Space+Mono:wght@400;700&display=swap');</style>";

  /* =================================================================
     50 STYLE KITS
     def(index, name, palette, font, capStyle, badge, motion, frame, pop, opts)
     ================================================================= */
  var STYLES = [], BY = {};
  function def(index, name, pal, font, capStyle, badge, motion, frame, pop, opts) {
    opts = opts || {};
    var cfg = {
      index: index, name: name, palette: pal, font: font, capStyle: capStyle,
      badge: badge, motion: motion, frame: frame, pop: pop,
      plate: !!opts.plate, badgeText: opts.badgeText, step: opts.step, steps: opts.steps,
      capScale: opts.capScale, capSize: opts.capSize, upper: opts.upper,
      defaultHook: opts.hook || 'SPEND CRYPTO ANYWHERE', defaultStat: opts.stat || '2% BACK'
    };
    STYLES.push(cfg); BY[index] = cfg;
  }

  def(1,  'House Gold',        'gold',    'anton',   'whiteGold',   'none', 'none',       'bottomrule', 'none',    { hook: 'SPEND CRYPTO ANYWHERE' });
  def(2,  'Cyan Tap Ring',     'cyan',    'anton',   'glowWord',    'step', 'tap-ring',   'none',       'pulse',   { hook: 'JUST TAP TO PAY' });
  def(3,  'Violet Box',        'violet',  'oswald',  'box',         'stat', 'none',       'sideline',   'none',    { hook: 'GET 2% BACK IN CRYPTO' });
  def(4,  'Coin Burst',        'gold',    'bebas',   'whiteGold',   'none', 'coin-burst', 'none',       'spark',   { hook: 'CRYPTO IN YOUR POCKET' });
  def(5,  'Lime Underline',    'lime',    'oswald',  'underline',   'chip', 'none',       'topbar',     'none',    { hook: 'YOUR CRYPTO, ANYWHERE' });
  def(6,  'Magenta Edge',      'magenta', 'anton',   'glowWord',    'none', 'neon-edge',  'brackets',   'none',    { hook: 'THE FUTURE OF SPENDING' });
  def(7,  'Ice Clean',         'ice',     'grotesk', 'whiteGold',   'stat', 'none',       'bottomrule', 'none',    { upper: false, hook: 'Crypto, made simple' });
  def(8,  'Emerald Cashback',  'emerald', 'archivo', 'box',         'stat', 'check',      'none',       'none',    { hook: 'EARN WHILE YOU SPEND' });
  def(9,  'Orange Sweep',      'orange',  'bebas',   'whiteGold',   'none', 'none',       'none',       'sweep',   { hook: 'INSTANT. ANYWHERE.' });
  def(10, 'Holo Gradient',     'holo',    'anton',   'gradient',    'chip', 'none',       'brackets',   'none',    { hook: 'DEFY THE BANK' });
  def(11, 'Mono Outline',      'mono',    'archivo', 'outlineFill', 'none', 'none',       'topbar',     'none',    { hook: 'A REAL CRYPTO CARD' });
  def(12, 'Gold Ticker',       'gold',    'oswald',  'whiteGold',   'none', 'ticker',     'bottomrule', 'none',    { hook: 'WATCH IT. SPEND IT.' });
  def(13, 'Violet Swipe',      'violet',  'anton',   'glowWord',    'none', 'swipe',      'none',       'chevron', { hook: 'PICK YOUR CRYPTO' });
  def(14, 'Cyan Plate',        'cyan',    'grotesk', 'plateBox',    'step', 'none',       'none',       'none',    { upper: false, hook: 'Tap. Pay. Done.' });
  def(15, 'Gold Slab',         'gold',    'zilla',   'whiteGold',   'stat', 'none',       'sideline',   'none',    { hook: 'SPEND LIKE GOLD' });
  def(16, 'Lime Box Burst',    'lime',    'bebas',   'box',         'none', 'coin-burst', 'none',       'spark',   { hook: 'CHARGE UP YOUR WALLET' });
  def(17, 'Emerald Underline', 'emerald', 'oswald',  'underline',   'chip', 'none',       'topbar',     'none',    { hook: 'EVERY SWIPE PAYS BACK' });
  def(18, 'Magenta Pop',       'magenta', 'archivo', 'shadowPop',   'none', 'none',       'none',       'sweep',   { hook: 'CRYPTO MEETS REAL LIFE' });
  def(19, 'Ice Tap Ring',      'ice',     'anton',   'glowWord',    'none', 'tap-ring',   'brackets',   'pulse',   { hook: 'CONTACTLESS CRYPTO' });
  def(20, 'Gold Duo',          'gold',    'anton',   'duo',         'none', 'none',       'bottomrule', 'none',    { hook: 'DEFY. SPEND. REPEAT.' });
  def(21, 'Violet Ticker',     'violet',  'oswald',  'whiteGold',   'stat', 'ticker',     'none',       'none',    { hook: 'SPEND THE GAINS' });
  def(22, 'Holo Box',          'holo',    'bebas',   'box',         'chip', 'none',       'none',       'spark',   { hook: 'NEXT-GEN CRYPTO CARD' });
  def(23, 'Cyan Check',        'cyan',    'archivo', 'glowWord',    'none', 'check',      'brackets',   'none',    { hook: 'PAYMENT APPROVED' });
  def(24, 'Orange Coins',      'orange',  'anton',   'whiteGold',   'none', 'coin-burst', 'none',       'none',    { hook: 'BTC TO BURGERS' });
  def(25, 'Mono Manifesto',    'mono',    'archivo', 'whiteGold',   'none', 'none',       'sideline',   'none',    { hook: 'SPEND CRYPTO ANYWHERE' });
  def(26, 'Gold Stat Box',     'gold',    'oswald',  'box',         'stat', 'none',       'topbar',     'none',    { hook: 'CASHBACK ON EVERY TAP' });
  def(27, 'Lime Edge Sweep',   'lime',    'bebas',   'glowWord',    'none', 'neon-edge',  'none',       'sweep',   { hook: 'POWERED BY CRYPTO' });
  def(28, 'Violet Plate',      'violet',  'grotesk', 'plateBox',    'none', 'none',       'bottomrule', 'none',    { upper: false, hook: 'Your phone is your card' });
  def(29, 'Emerald Ring',      'emerald', 'anton',   'glowWord',    'step', 'tap-ring',   'none',       'pulse',   { hook: 'ONE TAP. REAL WORLD.' });
  def(30, 'Ice Slab',          'ice',     'zilla',   'whiteGold',   'chip', 'none',       'brackets',   'none',    { hook: 'COOL, CALM, CRYPTO' });
  def(31, 'Gold Swipe',        'gold',    'anton',   'whiteGold',   'none', 'swipe',      'none',       'chevron', { hook: 'MEET DEFYCARD' });
  def(32, 'Magenta Gradient',  'magenta', 'anton',   'gradient',    'none', 'neon-edge',  'brackets',   'none',    { hook: 'THE CARD THAT GETS IT' });
  def(33, 'Cyan Ticker',       'cyan',    'oswald',  'whiteGold',   'none', 'ticker',     'bottomrule', 'none',    { hook: 'YOUR CRYPTO, LIVE' });
  def(34, 'Holo Pop',          'holo',    'archivo', 'shadowPop',   'chip', 'none',       'none',       'spark',   { hook: 'SLEEK. SECURE. CRYPTO.' });
  def(35, 'Emerald Coins',     'emerald', 'bebas',   'whiteGold',   'none', 'coin-burst', 'none',       'none',    { hook: 'CRYPTO IN YOUR POCKET' });
  def(36, 'Gold Brackets',     'gold',    'anton',   'glowWord',    'stat', 'none',       'brackets',   'none',    { hook: 'TAP INTO GOLD' });
  def(37, 'Orange Underline',  'orange',  'oswald',  'underline',   'chip', 'none',       'topbar',     'none',    { hook: 'MONEY THAT MOVES' });
  def(38, 'Mono Box',          'mono',    'bebas',   'box',         'none', 'none',       'sideline',   'none',    { hook: 'CRYPTO, MADE SIMPLE' });
  def(39, 'Violet Check',      'violet',  'anton',   'glowWord',    'none', 'check',      'none',       'none',    { hook: 'APPROVED IN SECONDS' });
  def(40, 'Ice Plate',         'ice',     'grotesk', 'plateBox',    'stat', 'none',       'bottomrule', 'none',    { upper: false, hook: 'Borderless spending' });
  def(41, 'Gold Neon Edge',    'gold',    'anton',   'whiteGold',   'none', 'neon-edge',  'none',       'none',    { hook: 'OUTLINE THE FUTURE' });
  def(42, 'Lime Tap Ring',     'lime',    'oswald',  'glowWord',    'step', 'tap-ring',   'none',       'pulse',   { hook: 'JUST TAP TO PAY' });
  def(43, 'Magenta Slab',      'magenta', 'zilla',   'whiteGold',   'chip', 'none',       'brackets',   'none',    { hook: 'CRYPTO IN ORBIT' });
  def(44, 'Cyan Swipe',        'cyan',    'anton',   'whiteGold',   'none', 'swipe',      'none',       'chevron', { hook: 'TAP AT ANY TERMINAL' });
  def(45, 'Emerald Coin Pop',  'emerald', 'anton',   'glowWord',    'none', 'coin-burst', 'none',       'spark',   { hook: 'RAIN OR SHINE, SPEND CRYPTO' });
  def(46, 'Gold Duo Bold',     'gold',    'bebas',   'duo',         'none', 'none',       'topbar',     'none',    { hook: 'DROP IN APPLE PAY' });
  def(47, 'Violet Outline',    'violet',  'archivo', 'outlineFill', 'none', 'none',       'sideline',   'none',    { hook: 'WORKS WHERE YOU SHOP' });
  def(48, 'Holo Ticker',       'holo',    'oswald',  'whiteGold',   'stat', 'ticker',     'none',       'none',    { hook: 'ACCEPTED WORLDWIDE' });
  def(49, 'Orange Pop',        'orange',  'archivo', 'shadowPop',   'none', 'none',       'none',       'sweep',   { hook: 'CASH BACK, INSTANTLY' });
  def(50, 'Gold Finale',       'gold',    'anton',   'gradient',    'chip', 'check',      'brackets',   'spark',   { hook: 'WHY DEFYCARD' });

  /* =================================================================
     compose
     ================================================================= */
  function renderStyle(spec, opts) {
    opts = opts || {};
    var idx = (typeof spec === 'number') ? spec : (spec && spec.index) || 1;
    if (!BY[idx]) idx = 1;
    var cfg = BY[idx];
    var P = PAL[cfg.palette];
    var hook = (spec && spec.hook != null) ? spec.hook : cfg.defaultHook;
    var stat = (spec && typeof spec.stat !== 'undefined') ? spec.stat : cfg.defaultStat;
    var phase = (spec && spec.phase) || 'caption';
    var cta = (spec && spec.cta) || 'defycard.com';
    var showFTC = !(spec && spec.showFTC === false);

    // resolve active karaoke word
    var words = String(hook).split(/\s+/).filter(Boolean);
    var activeIdx = null;
    if (spec && typeof spec.activeIndex === 'number') activeIdx = spec.activeIndex;
    else if (spec && spec.word) { var wi = words.indexOf(spec.word); activeIdx = wi >= 0 ? wi : null; }

    // per-style stat override into badge text
    var cfgR = cfg;
    if (cfg.badge === 'stat' && stat) cfgR = Object.assign({}, cfg, { badgeText: stat });

    var layers = '';
    if (opts.demoBroll) layers += demoBroll();
    layers += frameHTML(cfgR, P);
    layers += motionHTML(cfgR, P);
    layers += popHTML(cfgR, P);
    layers += brandChip(P);
    if (phase === 'cta') layers += ctaZone(cfgR, P, cta);
    else layers += captionZone(cfgR, P, esc(hook), activeIdx);
    if (showFTC) layers += ftcHTML(P);
    if (opts.guides) layers += guides();

    return FONT_IMPORT +
      '<div class="defycard-overlay" data-style="' + idx + '" style="position:relative;width:1080px;height:1920px;overflow:hidden;' +
      (opts.demoBroll ? '' : 'background:transparent;') + 'font-family:' + FONTS.grotesk.stack + ';color:#fff;">' + layers + '</div>';
  }

  /* public token set + render handle per style */
  window.VIDEO_STYLES = STYLES.map(function (c) {
    var P = PAL[c.palette];
    return {
      index: c.index, name: c.name, palette: c.palette, paletteName: P.name, accent: P.a,
      font: c.font, capStyle: c.capStyle, badge: c.badge, motion: c.motion, frame: c.frame, pop: c.pop,
      defaultHook: c.defaultHook, defaultStat: c.defaultStat,
      render: function (overrides, o) {
        var s = { index: c.index };
        if (overrides) for (var k in overrides) s[k] = overrides[k];
        return renderStyle(s, o || {});
      }
    };
  });
  window.renderStyle = renderStyle;
})();
