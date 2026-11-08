const $ = s => document.querySelector(s);
const api = (fn, ...a) => window.pywebview.api[fn](...a);
// [format id sent to python, what you see, plain-words hint, more = folded away]
const AUDIO = [
  ["mp3", "mp3", "plays on everything"],
  ["original", "best quality", "the site's own audio, untouched"],
  ["wav", "wav", "for editing apps"],
  ["m4a", "m4a", "best for iPhone and iTunes", true],
  ["flac", "flac", "for flac music libraries", true],
  ["opus", "opus", "youtube's own format", true],
];
const AUDIO_NOTE = {
  mp3: "Converted to mp3 so it plays anywhere. 320 sounds the best.",
  original: "Nothing is converted, so nothing is lost. The file may end in .opus or .m4a; phones and VLC play it fine.",
  wav: "Uncompressed, for editing apps. Huge files and no cover art.",
  m4a: "Plays almost everywhere, and it's the format iPhone and iTunes use natively. Sounds a bit better than an mp3 of the same size.",
  flac: "Lossless, but it can't add back quality the source never had. Same sound as best quality, much bigger file.",
  opus: "The format YouTube itself uses. Usually the same as best quality.",
};
// rough file size per format from the video length (kilobits per second)
const AUDIO_KBPS = { wav: 1411, flac: 800, m4a: 256, opus: 160 };
let audioMore = false;
let clipOn = false;
let subsFor = null;    // which link the subtitle language list was built for
// two groups of four: the white styles, then the yellow ones (each yellow mirrors a white one above it)
const STYLES = [["clean", "clean"], ["boxed", "boxed"], ["big", "big"], ["editorial", "editorial"],
  ["yellow", "yellow"], ["cinema", "cinema"], ["cinema_edge", "cinema edge"], ["editorial_yellow", "editorial yellow"]];
let stylesBuilt = false, styleScrolled = false;

/* subtitle preview at true size
   ffmpeg's subtitle renderer works on a canvas 288 units high that's stretched to the video, so 1 unit =
   video height / 288. Its font size is measured differently from a web page's, so each font has a factor,
   measured by burning real text with ffmpeg and comparing letter heights in pixels. */
const FONT_K = { "Jost": 0.692, "Instrument Serif": 0.777, "TeX Gyre Heros": 0.877 };
const FONT_CSS = { "Jost": "Jost, 'Segoe UI', sans-serif", "Instrument Serif": "'Instrument Serif', Georgia, serif",
                   "TeX Gyre Heros": "'TeX Gyre Heros', Arial, sans-serif" };
const BOLD_SPACING = "-0.015em";  // the browser's fake bold is ~4% wider than ffmpeg's; this evens it out
const FONT_SPACING = { "TeX Gyre Heros": "0.011em" };  // ffmpeg sets this font ~3% wider than the browser
let SUBSTYLES = {};  // the exact numbers ffmpeg burns with, sent by grabF at start
const assColor = c => {  // '&HAABBGGRR' (alpha first, transparent = FF) -> css rgba
  const h = String(c || "").replace(/^&H/i, "").padStart(8, "0");
  const n = i => parseInt(h.slice(i, i + 2), 16);
  return `rgba(${n(6)},${n(4)},${n(2)},${(1 - n(0) / 255).toFixed(3)})`;
};
// unit: pixels per ffmpeg unit (null = fixed swatch size); scale: the size slider;
// outline false = just the letters, no dark edge or shadow (boxed keeps its box) - same rule as grabF's burn
function paintSub(el, name, unit, scale = 1, outline = true) {
  const p = SUBSTYLES[name];
  if (!p || !el) return;
  const u = unit || 21 / (p.size * (FONT_K[p.font] || 0.7));  // swatches: every style at the same 21px
  const bare = !outline && p.border !== 3;
  const fs = p.size * scale * u * (FONT_K[p.font] || 0.7), edge = bare ? 0 : p.outline * scale * u, sh = bare ? 0 : p.shadow * scale * u;
  Object.assign(el.style, {
    fontFamily: FONT_CSS[p.font] || "sans-serif",
    fontStyle: p.italic ? "italic" : "normal", fontWeight: p.bold ? "700" : "400",
    fontSize: `${fs}px`, color: assColor(p.color), letterSpacing: p.bold ? BOLD_SPACING : (FONT_SPACING[p.font] || "normal"),
    background: "none", padding: "0", webkitTextStroke: "0", textShadow: "none",
  });
  if (p.border === 3) {  // boxed: a solid box behind each line, padded by the edge width on every side
    Object.assign(el.style, { background: assColor(p.edge), padding: `${edge}px` });
  } else {
    // the edge as a ring of copies around each letter: round corners like ffmpeg's (a css text-stroke
    // has sharp mitred corners that spike on letters like 'w' and 'v')
    const ring = [];
    if (edge > 0) {
      const n = Math.max(12, Math.ceil(edge * 8));
      for (let i = 0; i < n; i++) {
        const a = i / n * 2 * Math.PI;
        ring.push(`${(Math.cos(a) * edge).toFixed(2)}px ${(Math.sin(a) * edge).toFixed(2)}px 0 ${assColor(p.edge)}`);
      }
    }
    if (sh > 0) ring.push(`${sh}px ${sh}px 0 ${assColor(p.back)}`);
    el.style.textShadow = ring.join(",") || "none";
  }
}
function drawPreview() {
  const box = $("#subPreview"), line = box.querySelector(".spv-line"), p = SUBSTYLES[S.sub_style || "clean"];
  if (!p || !box.clientHeight) return;
  const H = box.clientHeight, W = box.clientWidth, u = H / 288;
  // ffmpeg: margins 10 units left/right (on a 384-wide canvas), MarginV from the bottom edge
  Object.assign(line.style, { left: `${W * 10 / 384}px`, right: `${W * 10 / 384}px`, bottom: `${p.margin * u}px` });
  paintSub($("#spvText"), S.sub_style || "clean", u, (S.sub_size || 100) / 100, S.sub_outline !== false);
}
new ResizeObserver(() => { if ($("#subsBox").classList.contains("burn")) drawPreview(); }).observe($("#subPreview"));
const BURN_NOTE = "Burned in for good, so they can't be turned off. The file takes a bit more space than a normal download.";


// "1:30", "1:02:30", "90" or "90s" -> seconds; anything else -> null
function parseTime(t) {
  t = String(t || "").trim().replace(/s$/i, "");
  if (!t) return null;
  if (!/^\d+(:\d{1,2}){0,2}(\.\d+)?$/.test(t)) return null;
  return t.split(":").reduce((acc, p) => acc * 60 + parseFloat(p), 0);
}
// the part you asked for, or null with a reason
function clipRange() {
  if (!clipOn || !info?.duration) return { range: null };
  const a = parseTime($("#clipFrom").value || "0"), b = parseTime($("#clipTo").value || dur(info.duration));
  if (a == null || b == null) return { range: null, error: "Use minutes:seconds, like 1:30." };
  if (b <= a) return { range: null, error: "The end has to come after the start." };
  if (b > info.duration + 1) return { range: null, error: `The video is only ${dur(info.duration)} long.` };
  return { range: [a, Math.min(b, info.duration)] };
}
const KBPS = [320, 256, 192, 128];
let S = {}, info = null, url = "", playlist = false, probing = 0, showAll = false;

// one decimal when it isn't a whole number: 7.9 mb, 80 mb, 1.2 gb
const oneDp = v => { const r = Math.round(v * 10) / 10; return Number.isInteger(r) ? String(r) : r.toFixed(1); };
const mb = n => !n ? "" : n >= 1073741824 ? oneDp(n / 1073741824) + " gb" : n >= 1048576 ? oneDp(n / 1048576) + " mb" : Math.max(1, Math.round(n / 1024)) + " kb";
const dur = s => { if (!s) return ""; s = Math.round(s); const h = s / 3600 | 0, m = (s % 3600) / 60 | 0, x = String(s % 60).padStart(2, "0"); return h ? `${h}:${String(m).padStart(2, "0")}:${x}` : `${m}:${x}`; };
const esc = t => String(t ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const isUrl = t => /^https?:\/\/\S+\.\S+/i.test(t) && !/\s/.test(t);
// every value that came from a website goes through esc() before it touches innerHTML
const row = (on, attr, attrVal, name, tag, val) =>
  `<button class="row ${on ? "on" : ""}"${attr ? ` ${attr}="${esc(attrVal)}"` : ""}><span class="tick">${on ? "&#10003;" : ""}</span><span>${esc(name)}</span>${tag ? `<span class="tag">${esc(tag)}</span>` : ""}<span class="lead-dots"></span><span class="val">${esc(val || "")}</span></button>`;
const safeImg = u => /^https:\/\/[^\s"'<>]+$/i.test(u || "") ? u : "";

function save(patch) { Object.assign(S, patch); api("save", patch); }

// --- probing ---
// playlists: 'one' = just the video in the link, 'pick' = the videos you tick, 'all' = everything
let listMode = "one";
const picked = new Set();
let heroFor = null;  // which link the picture and title at the top were drawn for

// a language code as a readable name, made on this PC (nothing is looked up online)
let LANGS = null;
function langName(code) {
  try { LANGS = LANGS || new Intl.DisplayNames(["en"], { type: "language" }); } catch (e) { return code; }
  try {
    const n = LANGS.of(code.replace(/_/g, "-"));
    return n && n.toLowerCase() !== code.toLowerCase() ? n : code;
  } catch (e) { return code; }  // not a real code - show it as it came
}

async function probe(u, list = false) {
  u = u.trim();
  if (!isUrl(u)) return;
  if (u !== url) { listMode = "one"; picked.clear(); }  // a new link starts fresh
  url = u; playlist = list; showAll = false;
  const n = ++probing;
  $("#field").classList.add("loading");
  if (!list) showEmpty(true);
  let r;
  try { r = await api("probe", u, list); }
  catch (e) { r = { ok: false, error: "Something went wrong reading this link. Try again." }; }
  if (n !== probing) return;
  $("#field").classList.remove("loading");
  if (!r.ok) { showEmpty(false, r.error, "", r.fix); return; }
  info = r.info;
  if (info.kind === "playlist" && listMode === "one") listMode = "all";  // a playlist-only link
  if (info.kind === "audio") S.mode = "audio";
  render();
  if (!list) requestAnimationFrame(() => $("#main").scrollTop = 0);
}

// errors the user can fix in one click: the button opens the right Windows settings page (nothing else)
const FIXES = { clock: "open date & time settings" };
const fixBtn = fix => FIXES[fix] ? ` <button class="fix" data-fix="${esc(fix)}">${FIXES[fix]}</button>` : "";
function showEmpty(loading, error, note, fix) {
  info = null;
  $("#app").classList.remove("ready");
  $("#hero").innerHTML = loading
    ? `<div class="lead skel"><div class="pic"></div><div><div class="kicker">&nbsp;</div><div class="title it waiting">Reading the link&hellip;</div></div></div>`
    : `<div class="empty"><h1>Paste a link<i>.</i></h1>
       <p>Videos, songs and photo posts from YouTube, TikTok, Instagram, Pinterest, X, SoundCloud and about a thousand other sites. Paste one above and grabF reads it straight away.</p>
       ${error ? `<div class="err">${esc(error)}${fixBtn(fix)}</div>` : ""}${note ? `<div class="hint">${esc(note)}</div>` : ""}</div>`;
}

// --- rendering ---
function render() {
  $("#app").classList.add("ready");
  const photos = info.kind === "images", isList = info.kind === "playlist";
  const qs = info.qualities, top = qs[0];
  const unit = info.videos ? "file" : "photo";
  const count = photos ? `${info.count} ${unit}${info.count === 1 ? "" : "s"}` : "";
  const kicker = [info.site, info.by, isList ? `${info.count} videos` : photos ? count : dur(info.duration)].filter(Boolean);
  let dek = "";
  if (photos) dek = info.videos ? `${info.count - info.videos} photos and ${info.videos} videos, at full resolution.` : `Saved at the original full resolution.`;
  else if (isList) dek = `A playlist. Each video is saved at the quality you pick, or the best below it.`;
  else if (top) dek = `${qs.length} size${qs.length === 1 ? "" : "s"} on offer. The largest is ${top.label}${top.size ? `, about ${mb(top.size)}` : ""}.`;
  else if (info.kind === "audio") dek = info.audio?.kbps ? `Audio only. The source is ${info.audio.codec} at ${info.audio.kbps} kbps.` : `Audio only.`;
  // added after, so a video opened with your login still says its sizes
  if (info.login_auto) dek += ` Opened with your ${info.login_with} login, on this PC only.`;
  if (heroFor !== info) {  // only when the link changes - clicking options never makes the picture flicker
    heroFor = info;
    const thumb = safeImg(info.thumb);
    $("#hero").innerHTML = `<div class="lead"><div class="pic">${thumb ? `<img referrerpolicy="no-referrer" alt="" src="${esc(thumb)}">` : ""}</div>
      <div><div class="kicker">${kicker.map(esc).join("  ·  ")}</div><div class="title">${esc(info.title)}</div><p class="dek">${esc(dek)}</p></div></div>`;
    const img = $("#hero img");
    if (img) { img.addEventListener("load", () => img.classList.add("on")); img.addEventListener("error", () => img.remove()); }
  }

  const mode = photos ? "images" : info.kind === "audio" ? "audio" : S.mode;
  $("#tabs").style.display = photos || info.kind === "audio" ? "none" : "";
  document.querySelectorAll("#tabs button").forEach(b => b.classList.toggle("on", b.dataset.mode === mode));
  $("#videoOpts").style.display = mode === "video" ? "" : "none";
  $("#audioOpts").style.display = mode === "audio" ? "" : "none";
  $("#photoOpts").style.display = photos ? "" : "none";

  // video: the index of sizes. Every row shows what you'd really get in the chosen file mode,
  // as worked out by the download engine itself (Python asks yt-dlp's own format selector).
  const C = !!S.compat;
  const reach = q => !C || q.c_ok !== false;        // can this file mode actually deliver that resolution?
  const lab = q => (C && q.c_label) || q.label;
  const sz = q => C ? q.c_size : q.size;
  const avail = qs.filter(reach), topR = avail[0];
  if (S.quality !== "best" && !qs.some(q => q.h === S.quality)) S.quality = "best";
  // your choice is kept as-is; if this mode can't reach it, the nearest lower one is what you get
  const eff = S.quality === "best" ? topR : (avail.find(q => q.h <= S.quality) || avail[avail.length - 1]);
  const selH = eff?.h;
  const opts = qs.length ? qs : [{ h: "best", label: "best available", size: null, c_ok: true }];
  const shown = showAll || opts.length <= 5 ? opts : opts.slice(0, 4);
  const hidden = opts.slice(shown.length);
  if (hidden.some(q => q.h === selH)) { showAll = true; return render(); }
  const offRow = q => `<button class="row off" data-unlock="${esc(q.h)}" title="switch to best quality to get ${esc(q.label)}">`
    + `<span class="tick"></span><span>${esc(q.label)}</span><span class="tag">best quality only</span>`
    + `<span class="lead-dots"></span><span class="val">&mdash;</span></button>`;
  $("#qRows").innerHTML = shown.map(q => !reach(q) ? offRow(q) : row(
      q.h === selH || !qs.length, "data-q", q === topR ? "best" : q.h, lab(q),
      [q.tag, q === topR && qs.length ? "best" : ""].filter(Boolean).join(", "), mb(sz(q)))).join("")
    + (hidden.length ? `<button class="row more" id="moreQ"><span class="tick"></span><span>${esc(hidden.map(lab).join(", "))}</span><span class="lead-dots"></span><span class="val">more</span></button>` : "");
  const off = qs.filter(q => !reach(q));
  $("#qNote").textContent = !C ? ""
    : info.compat_real === false ? "This site has no H.264 version, so this file may not play everywhere."
    : off.length ? `${off.map(q => q.label).join(" and ")} only exist in newer formats that don't play everywhere. Pick one to switch to best quality.`
    : "";
  $("#compatCap").textContent = off.length && topR ? ` · up to ${topR.h}p` : "";
  document.querySelectorAll("[data-compat]").forEach(c => c.classList.toggle("on", +c.dataset.compat === +!!S.compat));
  const anySubs = info.subs?.length || info.autosubs?.length || info.autotrans?.length;
  $("#subsBox").style.display = anySubs ? "" : "none";
  if (subsFor !== info) {  // rebuild the language list only for a new link - otherwise your choice would reset
    subsFor = info;
    // some sites (TED, ...) only send codes like "pt-br" - turn those into "Brazilian Portuguese", A-Z
    const group = (label, list) => {
      if (!list?.length) return "";
      const named = list.map(s => ({ ...s, label: s.label === s.id ? langName(s.id) : s.label }))
        .sort((a, b) => a.label.localeCompare(b.label));
      // a plain greyed line as the heading - Windows draws real <optgroup> headings bold whatever the styles say
      return `<option class="grp" disabled>${esc(label)}</option>` + named.map(s => `<option value="${esc(s.id)}">${esc(s.label)}</option>`).join("");
    };
    $("#subs").innerHTML = `<option value="">none</option>`
      + group("from the uploader", info.subs)
      + group(`auto-generated by ${info.site_label || "the site"}`, info.autosubs)
      + group(`auto-translated by ${info.site_label || "the site"}`, info.autotrans);
  }
  const lang = $("#subs").value;
  const isAuto = lang.startsWith("auto:");
  $("#subsNote").textContent = !isAuto ? ""
    : (info.autotrans || []).some(s => s.id === lang)
      ? `Made automatically by ${info.site_label || "the site"}: speech turned into text, then machine-translated. Expect mistakes.`
      : `Made automatically by ${info.site_label || "the site"} from the speech, so expect some mistakes.`;
  const canBurn = !isList && !playlist;  // burning is per video, so not for whole playlists
  $("#burnOpt").style.display = canBurn ? "" : "none";
  $("#burnOpt").nextElementSibling.style.display = canBurn ? "" : "none";
  const sm = S.subs_mode === "burn" && !canBurn ? "embed" : (S.subs_mode || "embed");
  $("#subsBox").classList.toggle("lang", !!lang);
  $("#subsBox").classList.toggle("burn", !!lang && sm === "burn");
  document.querySelectorAll("[data-sm]").forEach(b => b.classList.toggle("on", b.dataset.sm === sm));
  if (lang && sm === "burn") {
    const bg = safeImg(info.thumb);
    // the strip is built once; after that a click only moves the tick, so nothing shifts or scrolls back
    if (!stylesBuilt) {
      stylesBuilt = true;
      $("#styleRows").innerHTML = STYLES.map(([id, name]) =>
        `<button class="sty" data-style="${id}"><span class="sw"><span>Aa</span></span>`
        + `<span class="nm"><i class="tk">&#10003;</i>${name}</span></button>`).join("");
    }
    // styles are set from code, not style="" attributes - grabF's security policy blocks inline styles in markup.
    // repainted each time so the swatches follow the outline switch
    document.querySelectorAll("#styleRows .sty").forEach(el => paintSub(el.querySelector(".sw span"), el.dataset.style, null, 1, S.sub_outline !== false));
    const boxed = SUBSTYLES[S.sub_style || "clean"]?.border === 3;
    $("#olBox").classList.toggle("na", boxed);  // kept in place (no jump), just not shown
    document.querySelectorAll("[data-ol]").forEach(b => b.classList.toggle("on", (b.dataset.ol === "1") === (S.sub_outline !== false)));
    document.querySelectorAll("#styleRows .sty").forEach(el => el.classList.toggle("on", el.dataset.style === (S.sub_style || "clean")));
    if (!styleScrolled) {  // first time it's shown: open on the group holding your chosen style
      styleScrolled = true;
      requestAnimationFrame(() => {
        const i = Math.max(0, STYLES.findIndex(s => s[0] === (S.sub_style || "clean")));
        $("#styleRows").scrollLeft = Math.floor(i / 4) * $("#styleRows").clientWidth;
        styleArrows();
      });
    }
    if (bg) $("#subPreview").style.backgroundImage = `url("${bg}")`;
    $("#subSize").value = S.sub_size || 100;
    sizeFill();
    drawPreview();
    $("#burnNote").textContent = BURN_NOTE;
  }

  // audio
  const secs = info.duration || 0;
  const aSize = f => f === "original" ? info.audio?.size || (info.audio?.kbps && secs ? info.audio.kbps * 125 * secs : 0)
    : f === "mp3" ? +S.kbps * 125 * secs : (AUDIO_KBPS[f] || 0) * 125 * secs;
  if (AUDIO.some(a => a[3] && a[0] === S.afmt)) audioMore = true;
  const shownA = AUDIO.filter(a => !a[3] || audioMore), foldedA = AUDIO.filter(a => a[3] && !audioMore);
  $("#aRows").innerHTML = shownA.map(([f, name, hint]) => row(S.afmt === f, "data-a", f, name, hint, mb(aSize(f)))).join("")
    + (foldedA.length ? `<button class="row more" id="moreA"><span class="tick"></span><span>${esc(foldedA.map(a => a[1]).join(", "))}</span><span class="lead-dots"></span><span class="val">more</span></button>` : "");
  $("#kbpsLine").style.display = S.afmt === "mp3" ? "" : "none";
  $("#kbpsLine").innerHTML = `<span class="lbl">bitrate</span>` + KBPS.map((k, i) =>
    `${i ? `<span class="sep">/</span>` : ""}<button class="choice ${+S.kbps === k ? "on" : ""}" data-k="${k}">${k}${k === 320 ? ` <span class="it">best</span>` : ""}</button>`).join("");
  $("#aNote").textContent = AUDIO_NOTE[S.afmt] || "";

  // photos
  $("#pRows").innerHTML = row(true, "", "", count, "original size", info.videos ? `incl. ${info.videos} video${info.videos > 1 ? "s" : ""}` : "");

  // playlist
  $("#listLine").style.display = !photos && (info.inList || isList) ? "" : "none";
  $("#oneOpt").style.display = info.inList ? "" : "none";  // a playlist-only link has no 'this video'
  document.querySelectorAll("[data-list]").forEach(c => c.classList.toggle("on", c.dataset.list === listMode));
  $("#listCount").textContent = isList ? `${info.count} videos` : "";
  const picking = isList && listMode === "pick";
  $("#pickBox").classList.toggle("on", picking);
  if (picking) {
    const items = info.items || [];
    $("#pickCount").textContent = `${picked.size} of ${items.length} chosen`;
    $("#pickCount").classList.remove("bad");
    const keep = $("#pickRows").scrollTop;  // ticking a video mustn't jump the list back to the top
    requestAnimationFrame(() => $("#pickRows").scrollTop = keep);
    $("#pickRows").innerHTML = items.map(it => {
      const on = picked.has(it.i);
      return `<button class="row ${on ? "on" : ""}" data-pick="${esc(it.i)}"><span class="tick">${on ? "&#10003;" : ""}</span>`
        + `<span class="num">${esc(String(it.i).padStart(2, "0"))}</span><span class="t">${esc(it.title)}</span>`
        + `<span class="lead-dots"></span><span class="val">${esc(dur(it.dur))}</span></button>`;
    }).join("");
  }

  // just a part of it
  const canClip = !photos && !isList && !playlist && info.duration > 2;
  $("#clipLine").style.display = canClip ? "" : "none";
  if (!canClip) clipOn = false;
  $("#clipLine").classList.toggle("on", clipOn);
  document.querySelectorAll("[data-clip]").forEach(c => c.classList.toggle("on", +c.dataset.clip === +clipOn));
  if (!$("#clipTo").value && info.duration) $("#clipTo").placeholder = dur(info.duration);
  const cr = clipRange();
  $("#clipNote").textContent = cr.error || (clipOn && cr.range ? "Cut exactly at those times. Takes a little longer than a full download." : "");
  $("#clipNote").classList.toggle("bad", !!cr.error);
  ["#clipFrom", "#clipTo"].forEach(s => $(s).classList.toggle("bad", !!cr.error));
  const part = cr.range ? (cr.range[1] - cr.range[0]) / info.duration : 1;

  // summary next to the button
  let sum;
  if (photos) sum = `${count} at full size`;
  else if (mode === "audio") {
    const sizeNote = mb(aSize(S.afmt) * part) ? `, about ${mb(aSize(S.afmt) * part)}` : "";
    sum = (S.afmt === "mp3" ? `mp3 at ${S.kbps} kbps` : S.afmt === "original" ? "best quality audio" : `${S.afmt} audio`) + sizeNote;
  }
  else if (lang && sm === "burn") sum = `${eff ? lab(eff) : "best"}, mp4 with ${(STYLES.find(s => s[0] === S.sub_style) || STYLES[0])[1]} subtitles burned in`
    + ((+S.sub_size || 100) !== 100 ? ` at ${S.sub_size}% size` : "")
    + (S.sub_outline === false && SUBSTYLES[S.sub_style || "clean"]?.border !== 3 ? ", no outline" : "");
  else sum = `${eff ? lab(eff) : "best"}${eff && sz(eff) ? `, about ${mb(sz(eff) * part)}` : ""}, ${C ? "plays everywhere" : "best quality file"}`
    + (mode === "video" && lang ? `, ${isAuto ? "auto-captions" : "subtitles"} ${sm === "file" ? "as a .srt file" : "inside"}` : "");
  if (isList) sum += picking ? `, ${picked.size} of ${info.count} videos` : `, ${info.count} videos`;
  if (cr.range) sum += `, ${dur(cr.range[0])}–${dur(cr.range[1])} only`;
  $("#goNote").textContent = sum;
}

// --- jobs ---
const jobs = {};
let jobSeq = 0;
function startDownload(again) {
  if (!info && !again) return;
  const cr = again ? { range: again.req.clip } : clipRange();
  if (!again && cr.error) { $("#clipFrom").focus(); return; }  // the note under the times says what's wrong
  const picking = !again && info.kind === "playlist" && listMode === "pick";
  if (picking && !picked.size) {
    $("#pickCount").textContent = "Tick at least one video first.";
    $("#pickCount").classList.add("bad");
    return;
  }
  const id = "j" + Date.now().toString(36) + (jobSeq++);
  const job = again || {
    title: info.title, label: $("#goNote").textContent,
    req: { url, mode: info.kind === "images" ? "images" : info.kind === "audio" ? "audio" : S.mode,
           count: picking ? picked.size : info.count, items: picking ? [...picked] : null,
           quality: S.quality, compat: !!S.compat, afmt: S.afmt, kbps: +S.kbps,
           subs: S.mode === "video" ? $("#subs").value : "", playlist: playlist || info.kind === "playlist",
           subs_mode: (S.subs_mode === "burn" && (playlist || info.kind === "playlist")) ? "embed" : (S.subs_mode || "embed"),
           sub_style: S.sub_style || "clean", sub_size: +S.sub_size || 100, sub_outline: S.sub_outline !== false,
           login: info.login === true, login_with: info.login_with || "", clip: cr.range || null, title: info.title },
  };
  jobs[id] = job;
  $("#jobs").insertAdjacentHTML("afterbegin", `<div class="job" id="${id}">
      <div class="nm">${esc(job.title)}</div>
      <div class="act"><button data-cancel="${id}">cancel</button></div>
      <div class="st">starting&hellip;  ·  ${esc(job.label)}</div>
      <div class="tr busy"><i></i></div></div>`);
  $("#queue").classList.add("has");
  api("download", id, job.req);
}

const stopTimer = el => { if (el._timer) { clearInterval(el._timer); el._timer = 0; } };
window.onEvent = (type, d) => {
  if (type === "engines") { if (d.state === "restart") updState("restart"); return; }
  if (type === "appupdate") {
    const b = $("#verBtn");
    b.textContent = `grabF ${d.version} is out`;
    b.title = "opens the download page in your browser";
    b.classList.add("fresh");
    b.onclick = () => api("open_release");
    return;
  }
  const el = document.getElementById(d.id);
  if (!el) return;
  const st = el.querySelector(".st"), tr = el.querySelector(".tr"), bar = tr?.querySelector("i");
  if (type !== "progress") stopTimer(el);
  if (type === "progress") {
    const parts = [];
    if (d.of) parts.push(`${d.item} of ${d.of}`);
    parts.push(d.stage);
    if (d.pct != null) parts.push(Math.floor(d.pct) + "%");
    if (d.speed) parts.push(mb(d.speed) + "/s");
    if (d.eta) parts.push(dur(d.eta) + " left");
    if (d.of && d.title && d.stage !== "photos") el.querySelector(".nm").textContent = d.title;
    st.textContent = parts.join("  ·  ");
    // a step with no % (cutting a part): count the seconds, so it's clearly working, not stuck
    stopTimer(el);
    if (d.timer) {
      const base = st.textContent, t0 = Date.now();
      el._timer = setInterval(() => { st.textContent = `${base}  ·  ${dur(Math.max(1, (Date.now() - t0) / 1000))}`; }, 1000);
    }
    if (d.pct == null) tr.classList.add("busy"); else { tr.classList.remove("busy"); bar.style.width = d.pct + "%"; }
  } else if (type === "done") {
    el.classList.add("done");
    const ext = d.files.length === 1 ? d.name.split(".").pop() : "";
    el.querySelector(".nm").innerHTML = `${esc(jobs[d.id]?.title || d.name)}${ext ? `<span class="it">${esc(ext)}</span>` : ""}`;
    st.innerHTML = `<span class="mark">&#10003;</span>  ${d.files.length === 1 ? "saved" : esc(d.name)}  ·  ${mb(d.size)}`;
    const f = d.files[0] || "";
    el.querySelector(".act").innerHTML = (d.files.length === 1 && d.openable ? `<button data-open="${esc(f)}">open</button>` : "") + `<button data-reveal="${esc(f)}">show</button>`;
    if (d.blocked) st.innerHTML += `  ·  <span class="mark">${esc(d.blocked)} unsafe file${d.blocked > 1 ? "s" : ""} blocked</span>`;
    if (d.skipped) st.innerHTML += `  ·  ${esc(d.skipped)} skipped (deleted, private or blocked)`;
  } else if (type === "failed") {
    el.classList.add("fail");
    st.innerHTML = `<span class="mark">&#10007;</span>  ${esc(d.error)}`;
    el.querySelector(".act").innerHTML = (FIXES[d.fix] ? `<button data-fix="${esc(d.fix)}">fix the clock</button>` : "")
      + `<button data-retry="${esc(d.id)}">retry</button>`;
  }
};

// --- wiring ---
$("#url").addEventListener("keydown", e => { if (e.key === "Enter") probe(e.target.value); });
$("#url").addEventListener("paste", () => setTimeout(() => probe($("#url").value), 0));
// you pressed paste, so it's your call: read whatever link is on the clipboard
$("#pasteBtn").onclick = async e => {
  e.preventDefault();
  const c = await api("clipboard").catch(() => ({}));
  if (c.url) { $("#url").value = c.url; probe(c.url); }
};
// The buttons sit inside the bar you drag the window by. Without this, pressing one also starts a
// window drag, the window shifts under the mouse mid-click, and the click can get lost.
document.querySelectorAll(".bar button").forEach(b => b.addEventListener("mousedown", e => e.stopPropagation()));
$("#minBtn").onclick = () => api("minimize");
$("#contactBtn").onclick = () => api("open_link", "contact");
$("#donateBtn").onclick = () => api("open_link", "donate");
let sizing = false;  // a click that lands while the window is still resizing is ignored, not half-applied
async function toggleBig() {
  if (sizing) return;
  sizing = true;
  try {
    const big = await api("toggle_size");
    $("#app").classList.toggle("big", big);
    $("#bigBtn").title = big ? "back to normal size" : "make bigger";
  } finally { sizing = false; }
}
$("#bigBtn").onclick = toggleBig;
// double-clicking the top bar does the same. It acts on the second press, like Windows' own title bars,
// not on the release ('dblclick' fires only after the button comes back up, which feels a beat late).
// stopPropagation: that press must not also start a window drag, or the resized window could get nudged.
$(".bar").addEventListener("mousedown", e => {
  if (e.detail === 2 && e.button === 0 && !e.target.closest("button")) {
    e.preventDefault();
    e.stopPropagation();
    toggleBig();
  }
});
$("#closeBtn").onclick = () => api("close");
// drag a link onto the window to read it. Dropped files are ignored, and a drop can never
// navigate the window away from grabF (preventDefault on every drag event).
window.addEventListener("dragover", e => { e.preventDefault(); document.body.classList.add("dropping"); });
window.addEventListener("dragleave", e => { if (!e.relatedTarget) document.body.classList.remove("dropping"); });
window.addEventListener("drop", e => {
  e.preventDefault();
  document.body.classList.remove("dropping");
  const t = (e.dataTransfer.getData("text/uri-list") || e.dataTransfer.getData("text/plain") || "")
    .split(/\r?\n/).map(s => s.trim()).find(s => s && !s.startsWith("#"));
  if (t && isUrl(t)) { $("#url").value = t; probe(t); }
});

// resizing from the edges: Python moves the real window, this just reports how far you've dragged
document.querySelectorAll(".rz").forEach(z => {
  z.addEventListener("pointerdown", e => {
    e.preventDefault();
    try { z.setPointerCapture(e.pointerId); } catch { /* keep resizing even if capture isn't available */ }
    api("resize_start");
    $("#app").classList.remove("big");
    const x0 = e.screenX, y0 = e.screenY, k = window.devicePixelRatio || 1;
    let frame = 0;
    const move = ev => {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        api("resize_to", z.dataset.edge, (ev.screenX - x0) * k, (ev.screenY - y0) * k);
      });
    };
    const up = () => { z.removeEventListener("pointermove", move); z.removeEventListener("pointerup", up); };
    z.addEventListener("pointermove", move);
    z.addEventListener("pointerup", up);
  });
});

// just a part
document.querySelectorAll("[data-clip]").forEach(c => c.onclick = () => {
  clipOn = c.dataset.clip === "1";
  render();
  if (clipOn) $("#clipFrom").focus();
});
["#clipFrom", "#clipTo"].forEach(s => $(s).addEventListener("input", () => render()));
$("#tabs").onclick = e => { const m = e.target.closest("button")?.dataset.mode; if (m) { save({ mode: m }); render(); } };
$("#qRows").onclick = e => {
  const b = e.target.closest("button"); if (!b) return;
  if (b.id === "moreQ") { showAll = true; return render(); }
  if (b.dataset.q) { save({ quality: b.dataset.q === "best" ? "best" : +b.dataset.q }); render(); }
  // a resolution 'plays everywhere' can't reach: you clicked it, so switch to the file mode that delivers it
  if (b.dataset.unlock) { save({ compat: false, quality: +b.dataset.unlock }); render(); }
};
document.querySelectorAll("[data-compat]").forEach(c => c.onclick = () => { save({ compat: c.dataset.compat === "1" }); render(); });
$("#aRows").onclick = e => {
  if (e.target.closest("#moreA")) { audioMore = true; return render(); }
  const b = e.target.closest("[data-a]"); if (b) { save({ afmt: b.dataset.a }); render(); }
};
$("#kbpsLine").onclick = e => { const b = e.target.closest("[data-k]"); if (b) { save({ kbps: +b.dataset.k }); render(); } };
document.querySelectorAll("[data-list]").forEach(c => c.onclick = () => {
  if (!info) return;
  const mode = c.dataset.list, isList = info.kind === "playlist";
  listMode = mode;
  if (mode === "one") { if (isList) probe(url, false); else render(); return; }  // back to just this video
  if (isList) render();          // the list is already here
  else probe(url, true);         // read the playlist's list of videos first
});
$("#pickRows").onclick = e => {
  const b = e.target.closest("[data-pick]"); if (!b) return;
  const i = +b.dataset.pick;
  picked.has(i) ? picked.delete(i) : picked.add(i);
  render();
};
// subtitles
$("#subs").addEventListener("change", () => render());
document.querySelectorAll("[data-sm]").forEach(b => b.onclick = () => {
  save({ subs_mode: b.dataset.sm });
  render();
});
$("#styleRows").onclick = e => { const s = e.target.closest("[data-style]"); if (s) { save({ sub_style: s.dataset.style }); render(); } };
// the strip scrolls like any list: two-finger swipe on a trackpad, shift + wheel, or the arrows
// (one arrow click = one group of four). Scrolling never changes your chosen style.
function styleArrows() {
  const r = $("#styleRows");
  $("#styPrev").disabled = r.scrollLeft <= 2;
  $("#styNext").disabled = r.scrollLeft + r.clientWidth >= r.scrollWidth - 2;
}
$("#styleRows").addEventListener("scroll", styleArrows, { passive: true });
new ResizeObserver(styleArrows).observe($("#styleRows"));  // also when the strip first appears or the window resizes
$("#styPrev").onclick = () => $("#styleRows").scrollBy({ left: -$("#styleRows").clientWidth, behavior: "smooth" });
$("#styNext").onclick = () => $("#styleRows").scrollBy({ left: $("#styleRows").clientWidth, behavior: "smooth" });
// size: the preview follows the slider live; saved when you let go
// how far along the size bar is (0..1) - the CSS draws the black part of the rule up to there
function sizeFill() {
  const s = $("#subSize");
  s.style.setProperty("--f", ((s.value - s.min) / (s.max - s.min)).toFixed(4));
}
$("#subSize").addEventListener("input", e => {
  sizeFill();
  S.sub_size = +e.target.value;
  $("#subSizeVal").textContent = `${S.sub_size}%`;
  drawPreview();
});
$("#subSize").addEventListener("change", e => { save({ sub_size: +e.target.value }); render(); });
document.querySelectorAll("[data-ol]").forEach(b => b.onclick = () => { save({ sub_outline: b.dataset.ol === "1" }); render(); });
$("#subSizeReset").onclick = () => { save({ sub_size: 100 }); $("#subSize").value = 100; $("#subSizeVal").textContent = "100%"; render(); };
$("#pickAll").onclick = () => { (info?.items || []).forEach(it => picked.add(it.i)); render(); };
$("#pickNone").onclick = () => { picked.clear(); render(); };
$("#go").onclick = () => startDownload();
$("#clearBtn").onclick = () => {
  document.querySelectorAll(".job.done,.job.fail").forEach(e => e.remove());
  if (!$("#jobs").children.length) $("#queue").classList.remove("has");
};
$("#jobs").onclick = e => {
  const b = e.target.closest("button"); if (!b) return;
  if (b.dataset.cancel) { api("cancel", b.dataset.cancel); b.textContent = "stopping"; }
  if (b.dataset.open) api("open_file", b.dataset.open);
  if (b.dataset.reveal) api("reveal", b.dataset.reveal);
  if (b.dataset.fix) api("open_fix", b.dataset.fix);
  if (b.dataset.retry) { const old = jobs[b.dataset.retry]; document.getElementById(b.dataset.retry).remove(); startDownload(old); }
};

// --- earlier downloads (kept on this PC) ---
const when = t => {
  const d = new Date(t * 1000), days = Math.floor((Date.now() - d) / 864e5);
  return days < 1 ? "today" : days < 2 ? "yesterday" : days < 7 ? `${days} days ago`
    : d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: d.getFullYear() === new Date().getFullYear() ? undefined : "numeric" });
};
async function loadPast() {
  const list = (await api("history")).filter(h => !jobs[h.id]);  // this session's downloads are already listed above
  $("#pastBox").classList.toggle("has", list.length > 0);
  $("#past").innerHTML = list.map(h => {
    const f = h.files[0] || "", ext = h.files.length === 1 ? f.split(".").pop() : "";
    return `<div class="job ${h.gone ? "gone" : "done"}">
      <div class="nm">${esc(h.title || h.name)}${ext ? `<span class="it">${esc(ext)}</span>` : ""}</div>
      <div class="act">${h.gone ? "" : (h.files.length === 1 ? `<button data-open="${esc(f)}">open</button>` : "")
        + `<button data-reveal="${esc(f)}">show</button>`}<button data-forget="${esc(h.id)}" title="remove from this list">&times;</button></div>
      <div class="st">${esc(when(h.when))}${h.gone ? "  ·  moved or deleted" : `  ·  ${h.files.length > 1 ? esc(h.name) + "  ·  " : ""}${mb(h.size)}`}</div></div>`;
  }).join("");
}
$("#past").onclick = async e => {
  const b = e.target.closest("button"); if (!b) return;
  if (b.dataset.open) api("open_file", b.dataset.open);
  if (b.dataset.reveal) api("reveal", b.dataset.reveal);
  if (b.dataset.forget) { await api("forget", b.dataset.forget); loadPast(); }
};
$("#hero").addEventListener("click", e => { const b = e.target.closest("[data-fix]"); if (b) api("open_fix", b.dataset.fix); });
$("#clearPastBtn").onclick = async () => { await api("clear_history"); loadPast(); };
$("#outBtn").onclick = async () => { const p = await api("pick_folder"); if (p) { S.out = p; showOut(); } };
$("#openBtn").onclick = () => api("open_folder");
$("#cookies").onchange = e => save({ cookies: e.target.value });
// update -> updating... -> 'up to date' (then back to 'update'), 'update failed', or a real restart button
let updBusy = false, updTimer = 0;
function updState(state) {
  const b = $("#updBtn");
  clearTimeout(updTimer);
  b.classList.toggle("fresh", state === "restart");
  b.dataset.state = state;
  b.textContent = { idle: "update", busy: "updating…", current: "up to date", failed: "update failed",
                    restart: "restart to finish update", wait: "finish downloads first" }[state];
  b.title = state === "restart" ? "newer download engines are installed; restart grabF to use them"
    : "update the download engines when a site stops working";
  if (state === "current" || state === "failed") updTimer = setTimeout(() => updState("idle"), 4000);
  if (state === "wait") updTimer = setTimeout(() => updState("restart"), 3000);
}
$("#updBtn").onclick = async () => {
  if (updBusy) return;
  if ($("#updBtn").dataset.state === "restart") {
    if (!(await api("restart").catch(() => false))) updState("wait");
    return;
  }
  updBusy = true;
  updState("busy");
  updState(await api("update_engine").catch(() => "failed"));
  updBusy = false;
};
function showOut() { const p = S.out.replace(/\\/g, "/").split("/"); $("#outBtn").textContent = p.slice(-2).join("/").toLowerCase(); $("#outBtn").title = S.out; }

window.addEventListener("pywebviewready", async () => {
  const r = await api("init");
  S = r.settings;
  SUBSTYLES = r.sub_styles || {};
  $("#subSizeVal").textContent = `${S.sub_size || 100}%`;
  $("#verBtn").textContent = `v${r.version}`;
  $("#ffwarn").classList.toggle("on", !r.ffmpeg);
  // only browsers that exist on this PC; chrome-family ones lock their logins, so say so
  const list = r.browsers || [];
  $("#cookies").innerHTML = `<option value="">no browser</option>` + list.map(b =>
    `<option value="${esc(b.id)}">${esc(b.id)}${b.locked ? " (locked)" : ""}</option>`).join("");
  $("#cookies").value = list.some(b => b.id === S.cookies) ? S.cookies : "";
  showOut(); showEmpty(false);
  loadPast();
  $("#url").focus();
});
