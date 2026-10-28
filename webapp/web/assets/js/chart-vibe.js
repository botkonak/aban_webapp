/* آداپتور رسم آبان روی VibeChart — لایهٔ خودکار استراتژی، ابزارهای ۹۱گانه و ترسیم دستی پایدار */
(function () {
  "use strict";

  var TF_RES = {
    "5m": "5", "15m": "15", "30m": "30", "1h": "60",
    "4h": "240", "6h": "360", "12h": "720", "1d": "1D", "1w": "1W"
  };
  var RES_TF = {
    "5": "5m", "15": "15m", "30": "30m", "60": "1h",
    "240": "4h", "360": "6h", "720": "12h", "1D": "1d", "1W": "1w"
  };
  var SUPPORTED = ["5", "15", "30", "60", "240", "360", "720", "1D", "1W"];
  var SYS = "sys-";
  var LS_PREFIX = "aban.chart.drawings.v1.";

  function nf(v) {
    var n = Number(v);
    return Number.isFinite(n) ? n : null;
  }

  function fmtPrice(v, digits) {
    var n = Number(v);
    if (!Number.isFinite(n)) return "-";
    var d = Math.max(0, Math.min(10, Number.isFinite(digits) ? digits : (n >= 1000 ? 2 : (n >= 1 ? 4 : 8))));
    var s = Math.abs(n).toFixed(d);
    if (d > 0) s = s.replace(/0+$/, "").replace(/\.$/, "");
    return (n < 0 ? "-" : "") + s;
  }

  /* توکن اندیکاتور موتور تحلیل → تعریف اندیکاتور بومی VibeChart */
  function tokenSpec(token) {
    var parts = String(token || "").trim().split(":");
    var key = (parts[0] || "").toLowerCase();
    var nums = parts.slice(1).map(function (x) { return Number(x); });
    var base = key;
    if (/^macd/i.test(key)) base = "macd";
    else if (/^rsi/i.test(key)) base = "rsi";
    else if (/^stoch/i.test(key)) base = "stoch";
    else if (/^ichimoku/i.test(key)) base = "ichimoku";
    else if (/^volume/i.test(key)) base = "volume";
    if (base === "ema") return { id: "Moving Average Exponential", inputs: { length: nums[0] || 20 } };
    if (base === "sma") return { id: "Moving Average", inputs: { length: nums[0] || 20 } };
    if (base === "rsi") return { id: "Relative Strength Index", inputs: { length: nums[0] || 14 } };
    if (base === "macd") return { id: "MACD", inputs: { fast: nums[0] || 12, slow: nums[1] || 26, signal: nums[2] || 9 } };
    if (base === "stoch") return { id: "Stochastic", inputs: { k: nums[0] || 14, kSmooth: 1, d: nums[1] || 3 } };
    if (base === "cci") return { id: "Commodity Channel Index", inputs: { length: nums[0] || 20 } };
    if (base === "adx") return { id: "Average Directional Index", inputs: { adxSmoothing: nums[0] || 14, diLength: nums[0] || 14 } };
    if (base === "boll") return { id: "Bollinger Bands", inputs: { length: nums[0] || 20, mult: nums[1] || 2 } };
    if (base === "sar") return { id: "Parabolic SAR", inputs: { start: nums[0] || 0.02, increment: nums[0] || 0.02, maximum: nums[1] || 0.2 } };
    if (base === "vwap") return { id: "Volume Weighted Average Price", inputs: { anchor: "Session", source: "hlc3" } };
    if (base === "ichimoku") return { id: "Ichimoku Cloud", inputs: {} };
    return null;
  }

  function specKey(spec) {
    return spec.id + "|" + JSON.stringify(spec.inputs || {});
  }

  function specsFromLayers(layers) {
    var seen = {}, out = [];
    (layers || []).forEach(function (layer) {
      if (!layer || !layer.visible) return;
      var tokens = (layer.signal && layer.signal.overlay && layer.signal.overlay.indicators) || [];
      tokens.forEach(function (token) {
        var spec = tokenSpec(token);
        if (!spec) return;
        var k = specKey(spec);
        if (seen[k]) return;
        seen[k] = 1;
        out.push(spec);
      });
    });
    return out;
  }

  function decodingDigits(candles) {
    var last = 0;
    (candles || []).forEach(function (c) {
      var p = Math.abs(Number(c[4]) || 0);
      if (p > 0) last = p;
    });
    if (last >= 1000) return 2;
    if (last >= 100) return 3;
    if (last >= 1) return 4;
    if (last >= 0.01) return 6;
    return 8;
  }

  function createDatafeed(candles, meta) {
    var bars = (candles || []).map(function (c) {
      var ts = Number(c[0]);
      return {
        /* بک‌اند زمان را میلی‌ثانیه می‌دهد؛ داده‌ی ثانیه‌ای هم پذیرفته می‌شود */
        time: ts > 1e12 ? ts : ts * 1000,
        open: Number(c[1]), high: Number(c[2]), low: Number(c[3]),
        close: Number(c[4]), volume: Number(c[5]) || 0
      };
    }).filter(function (b) { return Number.isFinite(b.time) && Number.isFinite(b.close); })
      .sort(function (a, b) { return a.time - b.time; });

    return {
      onReady: function (cb) {
        setTimeout(function () {
          cb({ supported_resolutions: SUPPORTED.slice(), supports_marks: false, supports_timescale_marks: false, supports_time: false });
        }, 0);
      },
      resolveSymbol: function (name, onResolve) {
        var digits = decodingDigits(candles);
        onResolve({
          name: meta.symbol,
          ticker: meta.symbol,
          description: meta.symbol + " / USDT",
          type: "crypto",
          session: "24x7",
          timezone: "Asia/Tehran",
          exchange: meta.exchange || "",
          listed_exchange: meta.exchange || "",
          pricescale: Math.pow(10, digits),
          minmov: 1,
          has_intraday: true,
          has_daily: true,
          has_weekly_and_monthly: true,
          supported_resolutions: SUPPORTED.slice(),
          volume_precision: 2,
          data_status: "endofday",
          logo_urls: meta.logoUrl ? [meta.logoUrl] : undefined
        });
      },
      getBars: function (info, resolution, period, onResult) {
        var to = Number(period && period.to) || 0;
        var upto = to > 0 ? bars.filter(function (b) { return b.time / 1000 <= to + 1; }) : bars.slice();
        var count = Number(period && period.countBack) || 0;
        var rows = count > 0 ? upto.slice(-count) : upto;
        onResult(rows, rows.length ? undefined : { noData: true });
      },
      subscribeBars: function () {},
      unsubscribeBars: function () {}
    };
  }

  function installGuards(chart, host, settings, onBlocked) {
    var cleanups = [];
    var wrap = function (name, replacement) {
      var original = chart[name];
      chart[name] = replacement(original);
      cleanups.push(function () { chart[name] = original; });
    };
    wrap("setSymbol", function (original) {
      return function () {
        if (onBlocked) onBlocked("symbol");
        return Promise.resolve();
      };
    });
    wrap("setResolution", function (original) {
      return function () {
        if (onBlocked) onBlocked("interval");
        return Promise.resolve();
      };
    });
    wrap("setChartType", function (original) {
      return function (type) {
        if (type === chart.chartType) return original.call(chart, type);
        if (onBlocked) onBlocked("type");
      };
    });
    wrap("removeAllIndicators", function () {
      return function () {
        chart.getIndicators().forEach(function (inst) {
          if (String(inst.id || "").indexOf(SYS) !== 0) chart.removeIndicator(inst);
        });
      };
    });
    wrap("removeIndicator", function (original) {
      return function (inst) {
        var id = typeof inst === "string" ? inst : (inst && inst.id);
        if (String(id || "").indexOf(SYS) === 0) return;
        return original.call(chart, inst);
      };
    });
    if (typeof chart._onLegendAction === "function") {
      var legend = chart._onLegendAction.bind(chart);
      chart._onLegendAction = function (action, source, ev) {
        var id = source && source.id ? String(source.id) : "";
        if (String(id).indexOf(SYS) === 0) return;
        if (source === chart.model.volume && !settings.showVolumePanel) return;
        if (source === chart.model.mainSeries && action === "symbol") {
          if (onBlocked) onBlocked("symbol");
          return;
        }
        return legend(action, source, ev);
      };
      cleanups.push(function () { chart._onLegendAction = legend; });
    }

    var keyGuard = function (e) {
      if (!host.contains(e.target) && !host.contains(document.activeElement)) return;
      var mod = e.ctrlKey || e.metaKey;
      if (mod && (String(e.key).toLowerCase() === "k" || e.key === ",")) {
        e.preventDefault();
        e.stopPropagation();
        if (onBlocked) onBlocked(e.key === "," ? "settings" : "symbol");
      }
    };
    document.addEventListener("keydown", keyGuard, true);
    cleanups.push(function () { document.removeEventListener("keydown", keyGuard, true); });

    var closeDialogs = function () {
      var root = chart.root;
      if (!root) return;
      root.querySelectorAll(".vc-dialog-backdrop").forEach(function (backdrop) {
        if (backdrop.querySelector(".vc-sym-dialog, .vc-settings-dialog")) {
          var btn = backdrop.querySelector(".vc-dialog-close");
          if (btn) btn.click();
          else backdrop.remove();
          if (onBlocked) onBlocked("dialog");
        }
      });
      root.querySelectorAll(".vc-menu").forEach(function (menu) {
        menu.querySelectorAll(".vc-menu-item").forEach(function (item) {
          var text = (item.textContent || "").trim();
          if (/Change symbol/i.test(text) || /Change interval/i.test(text) || /Change chart type/i.test(text) || /Remove indicators/i.test(text)) {
            item.remove();
          }
          if (settings.showVolumePanel === false && /Volume/i.test(text)) item.remove();
        });
      });
    };
    var observer = new MutationObserver(closeDialogs);
    observer.observe(chart.root, { childList: true, subtree: true });
    cleanups.push(function () { observer.disconnect(); });

    /* شاخص‌های پایهٔ استراتژی همیشه روشن و قابل حذف‌نشدنی می‌مانند */
    var enforce = window.setInterval(function () {
      chart.getIndicators().forEach(function (inst) {
        if (String(inst.id || "").indexOf(SYS) === 0 && !inst.visible) inst.visible = true;
      });
      if (chart.model && chart.model.options) {
        if (chart.model.options.volume.visible !== !!settings.showVolumePanel) {
          chart.model.options.volume.visible = !!settings.showVolumePanel;
          chart.model.volume.visible = !!settings.showVolumePanel;
          chart.requestRender("full");
        }
      }
    }, 900);
    cleanups.push(function () { window.clearInterval(enforce); });

    return function () { cleanups.forEach(function (fn) { try { fn(); } catch (e) {} }); };
  }

  function makeOverlay(chart, host, settings, getLayers) {
    var pane = null;
    var canvas = null;
    var ctx = null;
    var raf = 0;
    var lastW = 0, lastH = 0;
    var tagBoxes = [];
    var TAG_BG = "#000000", TAG_FG = "#ffffff";
    var dpr = Math.max(1, Math.min(3, window.devicePixelRatio || 1));

    function themeInk() { return settings.theme === "dark" ? "#f4f7fc" : "#000000"; }

    function ensure() {
      var main = host.querySelector('.vc-pane[data-pane="main"]');
      if (!main) return false;
      if (pane !== main || !canvas || canvas.parentNode !== main) {
        pane = main;
        canvas = document.createElement("canvas");
        canvas.className = "aban-vibe-overlay";
        canvas.setAttribute("aria-hidden", "true");
        canvas.style.position = "absolute";
        canvas.style.left = "0";
        canvas.style.top = "0";
        canvas.style.pointerEvents = "none";
        /* بین بوم اصلی و بوم ترسیم دستی تا هر دو دیده شوند */
        var top = main.querySelector(".vc-canvas-top");
        if (top && top.parentNode === main) main.insertBefore(canvas, top);
        else main.appendChild(canvas);
        ctx = canvas.getContext("2d");
      }
      return true;
    }

    function resizeCanvas() {
      if (!pane || !canvas) return false;
      var w = Math.max(1, pane.clientWidth);
      var h = Math.max(1, pane.clientHeight);
      if (w === lastW && h === lastH && canvas.width === Math.round(w * dpr)) return true;
      lastW = w; lastH = h;
      canvas.style.width = w + "px";
      canvas.style.height = h + "px";
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
      return true;
    }

    function roundRectPath(c, x, y, w, h, r) {
      var rr = Math.min(r, w / 2, h / 2);
      c.beginPath();
      c.moveTo(x + rr, y);
      c.lineTo(x + w - rr, y);
      c.quadraticCurveTo(x + w, y, x + w, y + rr);
      c.lineTo(x + w, y + h - rr);
      c.quadraticCurveTo(x + w, y + h, x + w - rr, y + h);
      c.lineTo(x + rr, y + h);
      c.quadraticCurveTo(x, y + h, x, y + h - rr);
      c.lineTo(x, y + rr);
      c.quadraticCurveTo(x, y, x + rr, y);
      c.closePath();
    }

    /* برچسب‌ها روی هم می‌افتند؛ جای عمودی آزاد نزدیک‌ترین را انتخاب می‌کنیم */
    function freeTagBox(cx, cy, w, h) {
      var left = Math.min(Math.max(cx - w / 2, 2), Math.max(2, lastW - w - 2));
      var top = Math.min(Math.max(cy - h / 2, 4), Math.max(4, lastH - h - 4));
      var offsets = [0, 21, -21, 42, -42, 63, -63, 84, -84, 105, -105, 126, -126, 147, -147, 168, -168, 210, -210];
      var best = null, bestScore = Infinity;
      for (var i = 0; i < offsets.length; i++) {
        var y = Math.min(Math.max(top + offsets[i], 4), Math.max(4, lastH - h - 4));
        var box = { l: left, t: y, r: left + w, b: y + h };
        var score = 0;
        for (var j = 0; j < tagBoxes.length; j++) {
          var o = tagBoxes[j];
          if (box.l < o.r + 3 && box.r + 3 > o.l && box.t < o.b + 3 && box.b + 3 > o.t) score++;
        }
        if (score < bestScore) { best = box; bestScore = score; if (!score) break; }
      }
      tagBoxes.push(best);
      return best;
    }

    /* برچسب چرخیده هم جعبه‌ی محاطی خود را رزرو می‌کند تا با بقیه تلاقی نکند */
    function rotatedTagBox(cx, cy, w, ang) {
      var cos = Math.abs(Math.cos(ang)), sin = Math.abs(Math.sin(ang));
      var box = freeTagBox(cx, cy, cos * w + sin * 19, sin * w + cos * 19);
      return { cx: (box.l + box.r) / 2, cy: (box.t + box.b) / 2 };
    }

    function paintTag(c, text, cx, cy, color, bold) {
      if (!text) return;
      var pad = 6, h = 19;
      c.font = (bold ? "700 " : "600 ") + "11px Vazirmatn, Tahoma, sans-serif";
      var w = c.measureText(text).width + pad * 2;
      var box = freeTagBox(cx, cy, w, h);
      cx = (box.l + box.r) / 2;
      cy = (box.t + box.b) / 2;
      c.save();
      roundRectPath(c, cx - w / 2, cy - h / 2, w, h, 6);
      c.fillStyle = TAG_BG;
      c.fill();
      if (color) {
        c.strokeStyle = color;
        c.lineWidth = 1.4;
        c.stroke();
      }
      c.fillStyle = TAG_FG;
      c.textAlign = "center";
      c.textBaseline = "middle";
      c.fillText(text, cx, cy + 0.5);
      c.restore();
    }

    function paintPointer(c, x, y, up, color, label) {
      var reach = 46, head = 13, wing = 8;
      var dirn = up ? 1 : -1;
      var tipY = y + dirn * 6;
      var tailY = tipY + dirn * reach;
      c.save();
      c.strokeStyle = themeInk();
      c.fillStyle = themeInk();
      c.lineWidth = 2;
      c.setLineDash([4, 3]);
      c.beginPath();
      c.moveTo(x, tailY);
      c.lineTo(x, tipY + dirn * head);
      c.stroke();
      c.setLineDash([]);
      c.beginPath();
      c.moveTo(x, tipY);
      c.lineTo(x - wing, tipY - dirn * head);
      c.lineTo(x + wing, tipY - dirn * head);
      c.closePath();
      c.fill();
      c.beginPath();
      c.arc(x, y, 2.6, 0, Math.PI * 2);
      c.fill();
      c.restore();
      paintTag(c, label, x, tailY + dirn * 11, color, true);
    }

    function candleAt(signal, bars) {
      if (!bars || !bars.length || !signal) return null;
      var t = Number(signal.candle_ts) || 0;
      var idx = -1, best = Infinity;
      if (t > 0) {
        for (var i = 0; i < bars.length; i++) {
          var d = Math.abs(Number(bars[i].time) - t);
          if (d < best) { best = d; idx = i; }
        }
        if (best > 86400 * 3) idx = -1;
      }
      if (idx < 0) {
        var ci = Number(signal.candle_index);
        if (Number.isFinite(ci) && ci >= 0 && ci < bars.length) idx = ci;
      }
      if (idx < 0) return null;
      var c = bars[idx];
      return { time: c.time, high: c.high, low: c.low, close: c.close };
    }

    function draw() {
      raf = 0;
      if (!ensure() || !resizeCanvas()) return;
      var c = ctx;
      var W = lastW, H = lastH;
      c.setTransform(dpr, 0, 0, dpr, 0, 0);
      c.clearRect(0, 0, W, H);
      tagBoxes = [];
      c.direction = "rtl";
      c.textAlign = "center";
      c.textBaseline = "middle";

      var ts = chart.timeScale();
      var ps = chart.priceScale("main", "right");
      var bars = chart.bars() || [];
      var ink = themeInk();
      var xOf = function (t) {
        var v = ts.timeToCoordinate(Number(t));
        return v == null || !Number.isFinite(v) ? null : v;
      };
      var yOf = function (p) {
        var v = ps.priceToCoordinate(Number(p));
        return v == null || !Number.isFinite(v) ? null : v;
      };

      var layers = getLayers() || [];
      var layer = null;
      for (var li = 0; li < layers.length; li++) {
        if (layers[li] && layers[li].visible) { layer = layers[li]; break; }
      }
      if (!layer) return;
      var sig = layer.signal || {};
      var ov = sig.overlay || {};
      var color = layer.color || "#4b8ef7";
      var zones = ov.zones || [], lines = ov.lines || [], points = ov.points || [];
      /* عنوان استراتژی در نوار لایه دیده می‌شود؛ تکرار آن روی هر خط فقط شلوغی می‌سازد */
      var shownTitles = {};
      var titleOnce = function (text) {
        var t = String(text || "").trim();
        if (!t || t === String(sig.strategy || "").trim()) return false;
        if (shownTitles[t]) return false;
        shownTitles[t] = 1;
        return true;
      };

      var drawRay = function (r, withSegment) {
        var pts = (r && r.points) || [];
        if (pts.length < 2) return;
        var x1 = xOf(pts[0].time), y1 = yOf(pts[0].value);
        var x2 = xOf(pts[pts.length - 1].time), y2 = yOf(pts[pts.length - 1].value);
        if (x1 == null || y1 == null || x2 == null || y2 == null) return;
        var dx = x2 - x1;
        if (Math.abs(dx) < 0.5) return;
        var slope = (y2 - y1) / dx;
        var mode = r.extend || "right";
        c.save();
        c.strokeStyle = ink;
        c.lineWidth = 1.7;
        c.setLineDash(r.dashed ? [6, 4] : []);
        var yAt = function (x) { return y1 + slope * (x - x1); };
        if (withSegment) { c.beginPath(); c.moveTo(x1, y1); c.lineTo(x2, y2); c.stroke(); }
        if (mode === "right" || mode === "both") {
          c.globalAlpha = 0.6;
          c.beginPath(); c.moveTo(x2, y2); c.lineTo(W, yAt(W)); c.stroke();
        }
        if (mode === "left" || mode === "both") {
          c.globalAlpha = 0.6;
          c.beginPath(); c.moveTo(x1, y1); c.lineTo(0, yAt(0)); c.stroke();
        }
        c.restore();
        if (r.title && titleOnce(r.title)) {
          var mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
          var ang = Math.atan2(y2 - y1, x2 - x1);
          if (ang > Math.PI / 2) ang -= Math.PI;
          if (ang < -Math.PI / 2) ang += Math.PI;
          c.save();
          c.font = "600 11px Vazirmatn, Tahoma, sans-serif";
          var tw = c.measureText(r.title).width + 12;
          var box = rotatedTagBox(mx, my - 12, tw, ang);
          c.translate(box.cx, box.cy);
          c.rotate(ang);
          roundRectPath(c, -tw / 2, -9.5, tw, 19, 6);
          c.fillStyle = TAG_BG;
          c.fill();
          c.strokeStyle = color;
          c.lineWidth = 1.4;
          c.stroke();
          c.fillStyle = TAG_FG;
          c.textAlign = "center";
          c.textBaseline = "middle";
          c.fillText(r.title, 0, 0.5);
          c.restore();
        }
      };

      zones.forEach(function (z) {
        var y1 = yOf(z.top), y2 = yOf(z.bottom);
        if (y1 == null || y2 == null) return;
        var x1 = xOf(z.from), x2 = xOf(z.to);
        if (x1 == null && x2 == null) return;
        if (x1 == null) x1 = 0;
        if (x2 == null) x2 = W;
        if (x2 < x1) { var tmp = x1; x1 = x2; x2 = tmp; }
        var top = Math.min(y1, y2), height = Math.abs(y2 - y1);
        var zc = z.color || color;
        c.save();
        c.globalAlpha = 0.14;
        c.fillStyle = zc;
        c.fillRect(x1, top, Math.max(1, x2 - x1), Math.max(1, height));
        c.globalAlpha = 0.8;
        c.strokeStyle = ink;
        c.lineWidth = 1.2;
        c.setLineDash([5, 3]);
        c.strokeRect(x1, top, Math.max(1, x2 - x1), Math.max(1, height));
        c.restore();
        if (z.title && titleOnce(z.title)) paintTag(c, z.title, (x1 + x2) / 2, top + 11, zc, false);
      });

      (ov.levels || []).forEach(function (level) {
        var y = yOf(level.price);
        if (y == null) return;
        c.save();
        c.strokeStyle = ink;
        c.globalAlpha = 0.72;
        c.lineWidth = 1.2;
        c.setLineDash([6, 3]);
        c.beginPath();
        c.moveTo(0, y);
        c.lineTo(W, y);
        c.stroke();
        c.restore();
        var label = (level.title || "") + (level.title ? " · " : "") + fmtPrice(level.price);
        paintTag(c, label, W - Math.min(92, Math.max(56, label.length * 3.4)), y, level.color || color, false);
      });

      lines.forEach(function (ln) {
        var pts = ln.points || [];
        if (pts.length < 2) return;
        var lc = ln.color || color;
        var x1 = xOf(pts[0].time), y1 = yOf(pts[0].value);
        var x2 = xOf(pts[pts.length - 1].time), y2 = yOf(pts[pts.length - 1].value);
        if (x1 == null || y1 == null || x2 == null || y2 == null) return;
        c.save();
        c.strokeStyle = ink;
        c.globalAlpha = 0.5;
        c.lineWidth = 1.6;
        c.setLineDash([]);
        var dx = x2 - x1, dy = y2 - y1;
        c.beginPath();
        c.moveTo(x1, y1);
        c.lineTo(x2, y2);
        c.stroke();
        if (Math.abs(dx) > 1) {
          var slope = dy / dx;
          c.globalAlpha = 0.3;
          c.setLineDash([3, 4]);
          c.beginPath();
          c.moveTo(x2, y2);
          c.lineTo(W, y2 + slope * (W - x2));
          c.stroke();
        }
        c.restore();
        if (!ln.title || !titleOnce(ln.title)) return;
        var ang = Math.atan2(y2 - y1, x2 - x1);
        if (ang > Math.PI / 2) ang -= Math.PI;
        if (ang < -Math.PI / 2) ang += Math.PI;
        var mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
        c.save();
        c.font = "600 11px Vazirmatn, Tahoma, sans-serif";
        var tw = c.measureText(ln.title).width + 12;
        var box = rotatedTagBox(mx, my - 11, tw, ang);
        c.translate(box.cx, box.cy);
        c.rotate(ang);
        roundRectPath(c, -tw / 2, -9.5, tw, 19, 6);
        c.fillStyle = TAG_BG;
        c.fill();
        c.strokeStyle = lc;
        c.lineWidth = 1.4;
        c.stroke();
        c.fillStyle = TAG_FG;
        c.fillText(ln.title, 0, 0.5);
        c.restore();
      });

      (ov.rays || []).forEach(function (r) { drawRay(r, true); });

      (ov.fib || []).forEach(function (f) {
        if (!f || !f.from || !f.to) return;
        var v1 = Number(f.from.value), v2 = Number(f.to.value);
        if (!Number.isFinite(v1) || !Number.isFinite(v2) || v1 === v2) return;
        var xa = xOf(f.from.time), xb = xOf(f.to.time);
        var left = xa == null ? 0 : xa, right = xb == null ? W : xb;
        if (right < left) { var t0 = left; left = right; right = t0; }
        var ya = yOf(v1), yb = yOf(v2);
        c.save();
        c.strokeStyle = ink;
        c.globalAlpha = 0.6;
        c.lineWidth = 1.2;
        c.setLineDash([2, 3]);
        if (ya != null && yb != null) { c.beginPath(); c.moveTo(left, ya); c.lineTo(right, yb); c.stroke(); }
        c.restore();
        (f.levels || []).forEach(function (lv) {
          var price = v2 + (v1 - v2) * (Number(lv.ratio) || 0);
          var y = yOf(price);
          if (y == null) return;
          var isGolden = Math.abs(Number(lv.ratio) - 0.618) < 1e-6;
          var lc = isGolden ? (lv.color || color) : ink;
          c.save();
          c.strokeStyle = lc;
          c.lineWidth = isGolden ? 1.8 : (Math.abs(Number(lv.ratio)) === 1 || Number(lv.ratio) === 0 ? 1.4 : 1);
          c.globalAlpha = isGolden ? 0.95 : 0.7;
          c.setLineDash(isGolden ? [] : [4, 3]);
          c.beginPath();
          c.moveTo(left, y);
          c.lineTo(W - 1, y);
          c.stroke();
          c.restore();
          var txt = (lv.label || "") + " • " + fmtPrice(price);
          paintTag(c, txt, W - 58, y, isGolden ? lc : null, isGolden);
        });
        if (f.title) paintTag(c, f.title, (left + right) / 2, Math.min(ya == null ? 20 : ya, yb == null ? 20 : yb) - 16, color, true);
      });

      (ov.labels || []).forEach(function (l) {
        if (!l || !l.text) return;
        var x = xOf(l.time), y = yOf(l.value);
        if (x == null || y == null) return;
        paintTag(c, String(l.text), x, y, l.color || color, false);
      });

      points.forEach(function (p, n) {
        var x = xOf(p.time), y = yOf(p.value);
        if (x == null || y == null) return;
        c.save();
        c.fillStyle = ink;
        c.strokeStyle = settings.theme === "dark" ? "#131722" : "#ffffff";
        c.lineWidth = 1.5;
        c.beginPath();
        c.arc(x, y, 3.6, 0, Math.PI * 2);
        c.fill();
        c.stroke();
        c.restore();
        if (p.label) paintTag(c, p.label, x, y - 15, color, false);
        else if (points.length > 2 && n === points.length - 1) paintTag(c, "کندل سیگنال", x, y - 15, color, false);
      });

      (ov.markers || []).forEach(function (m) {
        var x = xOf(m.time);
        var y = yOf(m.price || m.value);
        if (x == null) return;
        var candle = null;
        for (var bi = 0; bi < bars.length; bi++) {
          if (Number(bars[bi].time) === Number(m.time)) { candle = bars[bi]; break; }
        }
        var below = String(m.position || "belowBar") === "belowBar";
        var anchorY = y != null ? y : (candle ? (below ? candle.low : candle.high) : null);
        var cy = anchorY != null ? yOf(anchorY) : null;
        if (cy == null) return;
        var dirn = below ? 1 : -1;
        c.save();
        c.fillStyle = m.color || (below ? "#16b981" : "#ef4d5a");
        c.beginPath();
        c.moveTo(x, cy + dirn * 4);
        c.lineTo(x - 5, cy + dirn * 12);
        c.lineTo(x + 5, cy + dirn * 12);
        c.closePath();
        c.fill();
        c.restore();
        if (m.text) paintTag(c, m.text, x, cy + dirn * 24, m.color || color, false);
      });

      var anchor = candleAt(sig, bars);
      if (anchor) {
        var ax = xOf(anchor.time);
        if (ax != null) {
          var bullish = String(sig.direction || "").toUpperCase() === "LONG";
          var ay = yOf(bullish ? anchor.low : anchor.high);
          if (ay != null) {
            paintPointer(c, ax, ay, bullish, color, sig.strategy || "سیگنال");
          }
        }
      }
    }

    function schedule() {
      if (raf) return;
      raf = window.requestAnimationFrame(draw);
    }

    var subs = [];
    try {
      subs.push(chart.subscribe("dataLoaded", schedule));
      subs.push(chart.subscribe("visibleRangeChanged", schedule));
      subs.push(chart.subscribe("optionsChanged", schedule));
      subs.push(chart.subscribe("indicatorAdded", schedule));
      subs.push(chart.subscribe("indicatorRemoved", schedule));
      subs.push(chart.subscribe("chartTypeChanged", schedule));
    } catch (e) {}

    var ro = null;
    if (window.ResizeObserver) {
      ro = new ResizeObserver(function () { lastW = lastH = 0; schedule(); });
      ro.observe(host);
    }
    window.addEventListener("resize", schedule);
    var timer = window.setInterval(function () { ensure(); schedule(); }, 1200);

    schedule();

    return {
      refresh: schedule,
      destroy: function () {
        if (raf) window.cancelAnimationFrame(raf);
        subs.forEach(function (un) { try { if (typeof un === "function") un(); } catch (e) {} });
        if (ro) ro.disconnect();
        window.removeEventListener("resize", schedule);
        window.clearInterval(timer);
        if (canvas && canvas.parentNode) canvas.parentNode.removeChild(canvas);
        canvas = null;
        pane = null;
      }
    };
  }

  function mount(opts) {
    var host = opts.host;
    var settings = opts.settings || {};
    var candles = opts.candles || [];
    var layers = opts.layers || [];
    if (!host || !window.VibeChart || !window.VibeChart.Chart) {
      throw new Error("VibeChart بارگذاری نشد.");
    }
    host.innerHTML = "";
    host.classList.add("vibe-host");
    var meta = {
      symbol: String(opts.symbol || "").toUpperCase(),
      exchange: opts.exchange || "",
      logoUrl: opts.logoUrl || ""
    };
    var chart = new window.VibeChart.Chart({
      container: host,
      datafeed: createDatafeed(candles, meta),
      symbol: meta.symbol,
      interval: TF_RES[opts.tf] || "1D",
      chartType: opts.chartType === "line" ? "line" : "candles",
      autoSize: true,
      initialBars: Math.max(120, Math.min(900, candles.length)),
      theme: settings.theme === "light" ? "light" : "dark",
      layout: {
        fontFamily: "Vazirmatn, Tahoma, sans-serif",
        fontSize: 11,
        attributionLogo: false
      },
      localization: { locale: "en-US", dateFormat: "yyyy/MM/dd", timeFormat: "24h" },
      toolbar: {
        top: false, left: true, bottom: false,
        symbolSearch: false, intervals: false, chartTypes: false, indicators: true,
        undoRedo: false, settings: false, fullscreen: false, screenshot: false,
        replay: false, templates: false, compare: false, alert: false
      },
      volume: { visible: !!settings.showVolumePanel, overlay: true },
      legend: {
        visible: true, showSymbol: true, showSymbolDescription: false,
        showInterval: true, showOHLC: true, showBarChange: false, showVolume: !!settings.showVolumePanel,
        showExchange: false, showValues: true, showIndicatorTitles: true,
        showIndicatorArguments: false, showIndicatorValues: true, fontSize: 11
      },
      symbolSettings: {
        timezone: "Asia/Tehran",
        precision: decodingDigits(candles),
        countdownVisible: false
      },
      drawing: { magnet: "none" },
      rightPriceScale: { scaleMargins: { top: 0.08, bottom: 0.08 } }
    });

    /* قبل از نصب گاردها نگه داشته می‌شود تا تغییر نوع نمودار از دکمه‌های خود آبان مسدود نشود */
    var nativeSetChartType = chart.setChartType ? chart.setChartType.bind(chart) : null;
    /* حذف اندیکاتورهای سیستمی باید از تابع اصلی انجام شود؛ گارد کاربر را می‌بندد، نه همگام‌سازی داخلی را */
    var nativeRemoveIndicator = chart.removeIndicator ? chart.removeIndicator.bind(chart) : null;

    var blockedUntil = 0;
    var onBlocked = function (reason) {
      if (Date.now() < blockedUntil) return;
      blockedUntil = Date.now() + 900;
      if (typeof opts.onBlocked === "function") opts.onBlocked(reason);
    };
    var removeGuards = installGuards(chart, host, settings, onBlocked);

    var indicatorIds = [];
    var loadingDrawings = false;
    var saveTimer = 0;
    var storageKey = LS_PREFIX + meta.symbol + "|" + String(opts.tf || "");

    function storage() {
      try { return window.localStorage; } catch (e) { return null; }
    }

    function saveDrawings() {
      if (loadingDrawings) return;
      if (saveTimer) window.clearTimeout(saveTimer);
      saveTimer = window.setTimeout(function () {
        saveTimer = 0;
        var ls = storage();
        if (!ls) return;
        try { ls.setItem(storageKey, JSON.stringify(chart.drawings.serialize())); } catch (e) {}
      }, 350);
    }

    function loadDrawings() {
      var ls = storage();
      if (!ls) return;
      var raw = "";
      try { raw = ls.getItem(storageKey) || ""; } catch (e) {}
      if (!raw) return;
      try {
        var list = JSON.parse(raw);
        if (!Array.isArray(list) || !list.length) return;
        loadingDrawings = true;
        chart.drawings.load(list);
        loadingDrawings = false;
      } catch (e) { loadingDrawings = false; }
    }

    var chSub = chart.drawings.changed.subscribe(saveDrawings);

    function syncIndicators() {
      var desired = specsFromLayers(typeof opts.getLayers === "function" ? opts.getLayers() : layers);
      var wanted = {};
      desired.forEach(function (spec) {
        var key = SYS + String(spec.id).toLowerCase().replace(/[^a-z0-9]+/g, "-") + "-" + Object.keys(spec.inputs || {}).sort().map(function (k) {
          return k + spec.inputs[k];
        }).join("-");
        wanted[key] = spec;
      });
      chart.getIndicators().forEach(function (inst) {
        if (String(inst.id || "").indexOf(SYS) !== 0) return;
        if (!wanted[inst.id] && nativeRemoveIndicator) nativeRemoveIndicator(inst);
      });
      var existing = {};
      chart.getIndicators().forEach(function (inst) { existing[inst.id] = 1; });
      Object.keys(wanted).forEach(function (id) {
        if (existing[id]) return;
        try { chart.addIndicator(wanted[id].id, wanted[id].inputs || {}, { id: id }); } catch (e) {}
      });
      indicatorIds = Object.keys(wanted);
      overlay.refresh();
    }

    var overlay = makeOverlay(chart, host, settings, function () {
      return typeof opts.getLayers === "function" ? opts.getLayers() : layers;
    });

    var ready = false;
    var onData = chart.subscribe("dataLoaded", function () {
      if (ready) return;
      ready = true;
      loadDrawings();
      syncIndicators();
      if (typeof opts.onReady === "function") opts.onReady(chart);
    });

    var api = {
      chart: chart,
      host: host,
      get candles() { return chart.bars(); },
      get settings() { return settings; },
      setLayers: function (next) {
        layers = next || [];
        syncIndicators();
        overlay.refresh();
      },
      refreshOverlay: function () { overlay.refresh(); },
      setChartType: function (type) {
        var target = type === "line" ? "line" : "candles";
        if (chart.chartType !== target && nativeSetChartType) nativeSetChartType(target);
      },
      zoom: function (key) {
        var bars = chart.bars();
        if (!bars.length) return;
        if (!key || key === "all") { chart.timeScale().fitContent(); return; }
        var days = key === "1m" ? 30 : (key === "3m" ? 90 : (key === "6m" ? 180 : 365));
        var tf = RES_TF[chart.interval] || opts.tf || "1d";
        var minutes = { "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "6h": 360, "12h": 720, "1d": 1440, "1w": 10080 };
        var secs = (minutes[tf] || 1440) * 60;
        var count = Math.max(10, Math.ceil(days * 86400 / secs));
        var from = Math.max(0, bars.length - count);
        chart.timeScale().setVisibleLogicalRange(from, bars.length + 2);
      },
      focus: function (signal) {
        if (!signal) return;
        var bars = chart.bars();
        if (!bars.length) return;
        var t = Number(signal.candle_ts) || 0;
        var idx = -1, best = Infinity;
        if (t > 0) {
          for (var i = 0; i < bars.length; i++) {
            var d = Math.abs(Number(bars[i].time) - t);
            if (d < best) { best = d; idx = i; }
          }
          if (best > 86400 * 5) idx = -1;
        }
        if (idx < 0) {
          var ci = Number(signal.candle_index);
          if (Number.isFinite(ci) && ci >= 0 && ci < bars.length) idx = ci;
        }
        if (idx < 0) return;
        chart.timeScale().setVisibleLogicalRange(Math.max(0, idx - 55), idx + 12);
        overlay.refresh();
      },
      resize: function () {
        chart.resize();
        overlay.refresh();
      },
      undo: function () { chart.undo(); },
      redo: function () { chart.redo(); },
      canUndo: function () { return chart.drawings.canUndo; },
      canRedo: function () { return chart.drawings.canRedo; },
      destroy: function () {
        if (saveTimer) window.clearTimeout(saveTimer);
        try { docSave(); } catch (e) {}
        try { chSub(); } catch (e) {}
        try { onData(); } catch (e) {}
        try { overlay.destroy(); } catch (e) {}
        try { removeGuards(); } catch (e) {}
        try { chart.destroy(); } catch (e) {}
        host.innerHTML = "";
        host.classList.remove("vibe-host");
      }
    };

    function docSave() {
      var ls = storage();
      if (!ls) return;
      try { ls.setItem(storageKey, JSON.stringify(chart.drawings.serialize())); } catch (e) {}
    }

    return api;
  }

  window.AbanVibe = {
    mount: mount,
    TF_RES: TF_RES,
    RES_TF: RES_TF,
    tokenSpec: tokenSpec
  };
})();
