/* Плеер готовых аудиокниг (шаг 8): экран плеера, мини-плеер в библиотеке, добавление аудиокниг.
   Звук: в браузере — элемент <audio> (части книги — куски сохранённого файла, Blob.slice); в приложении — системный
   плеер Android (Native.Ab: работает в фоне, уведомление, гарнитура). Работает в любом режиме, и в автономном.
   Позиция — секунды от начала книги (apos в карточке), progress — доля прослушанного.
   app.js подключает модуль через Audiobook.init(ctx). */
(function () {
  "use strict";
  const A = window.AudioMeta;
  let ctx = null;
  const $ = (id) => document.getElementById(id);
  const RATES = [0.75, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2];
  const SAVE_EVERY = 10000;

  // ---------- состояние ----------
  let cur = null;          // { id, meta, title, authors, cover, parts, chapters, starts, duration, temp, blob }
  let at = 0;              // позиция, секунды от начала книги
  let playing = false, loading = false;
  let savedAt = 0, lastSaved = -1;
  let dragging = false;    // тянут ползунок — не двигаем его по времени
  let sleep = null;        // браузер: { until } | { chapterEnd: индекс главы }; приложение — sleepLeft из состояния
  let nativeSleepLeft = 0; // приложение: сколько осталось (с), -1 — до конца главы
  const engine = () => (window.Native ? nativeEngine : browserEngine);

  const rate = () => { const r = Number(ctx.store.get("abRate", 1)); return RATES.includes(r) ? r : 1; };

  // ---------- книга ----------
  function load(meta, extra) {
    const info = meta.audio || {};
    const parts = info.parts || [];
    const starts = [];
    let t = 0;
    for (const p of parts) { starts.push(t); t += p.duration || 0; }
    return Object.assign({
      id: meta.id, meta, title: meta.title, authors: meta.authors || [], cover: meta.cover,
      parts, chapters: (info.chapters || []).slice().sort((a, b) => a.at - b.at), starts, duration: info.duration || t,
    }, extra || {});
  }
  const partOf = (t) => { let p = 0; for (let i = 0; i < cur.starts.length; i++) if (t >= cur.starts[i] - 0.001) p = i; return p; };
  const chapterOf = (t) => { let c = 0; for (let i = 0; i < cur.chapters.length; i++) if (t >= cur.chapters[i].at - 0.25) c = i; return c; };
  const chapterEnd = (c) => (c + 1 < cur.chapters.length ? cur.chapters[c + 1].at : cur.duration);
  const clampAt = (t) => Math.max(0, Math.min(cur.duration ? cur.duration - 0.5 : t, t));

  // ---------- звук в браузере ----------
  const browserEngine = (() => {
    const audio = new Audio();
    audio.preload = "auto";
    try { audio.preservesPitch = true; audio.mozPreservesPitch = true; audio.webkitPreservesPitch = true; } catch (_) {}
    let part = -1, url = null, wantPlay = false, pendingSeek = null;
    function setPart(p) {
      if (p === part && url) return false;
      if (url) URL.revokeObjectURL(url);
      const pr = cur.parts[p];
      url = URL.createObjectURL(cur.blob.slice(pr.off, pr.off + pr.size, pr.mime || A.mimeOf(pr.name)));
      part = p;
      audio.src = url;
      return true;
    }
    function place(t) {
      const p = partOf(t);
      const local = Math.max(0, t - cur.starts[p]);
      const fresh = setPart(p);
      if (fresh || audio.readyState < 1) pendingSeek = local;
      else audio.currentTime = local;
    }
    audio.addEventListener("loadedmetadata", () => {
      if (pendingSeek !== null) { try { audio.currentTime = pendingSeek; } catch (_) {} pendingSeek = null; }
      audio.playbackRate = rate();
      if (wantPlay) audio.play().catch(onPlayError);
    });
    audio.addEventListener("timeupdate", () => {
      if (!cur || part < 0 || pendingSeek !== null) return;
      tick(cur.starts[part] + audio.currentTime);
    });
    audio.addEventListener("playing", () => { loading = false; playing = true; render(); });
    audio.addEventListener("waiting", () => { loading = true; render(); });
    audio.addEventListener("pause", () => { if (!wantPlay) { playing = false; loading = false; render(); } });
    audio.addEventListener("ended", () => {
      if (!cur) return;
      if (part + 1 < cur.parts.length) { place(cur.starts[part + 1]); if (wantPlay) audio.play().catch(onPlayError); }
      else { wantPlay = false; ended(); }
    });
    audio.addEventListener("error", () => {
      if (!cur || !audio.src) return;
      wantPlay = false; playing = false; loading = false; render();
      const p = cur.parts[part];
      ctx.toast(`Не получилось воспроизвести «${p ? baseName(p.name) : cur.title}»: формат не поддерживается этим браузером или файл повреждён`, 6000);
    });
    function onPlayError(e) {
      if (e && e.name === "AbortError") return;
      wantPlay = false; playing = false; loading = false; render();
      if (e && e.name === "NotAllowedError") ctx.toast("Браузер не дал начать воспроизведение — нажмите ▶ ещё раз", 4000);
    }
    return {
      async open() { part = -1; if (url) URL.revokeObjectURL(url); url = null; audio.removeAttribute("src"); },
      play(t) {
        wantPlay = true; loading = true; render();
        // экран блокировки / системные кнопки браузера: название и обложка
        try {
          if ("mediaSession" in navigator && window.MediaMetadata)
            navigator.mediaSession.metadata = new MediaMetadata({ title: cur.title, artist: cur.authors.join(", "), artwork: cur.cover ? [{ src: cur.cover }] : [] });
        } catch (_) {}
        place(t);
        audio.playbackRate = rate();
        if (audio.readyState >= 1 && pendingSeek === null) audio.play().catch(onPlayError);
      },
      pause() { wantPlay = false; audio.pause(); playing = false; loading = false; render(); },
      seek(t) { place(t); },
      setRate(r) { audio.playbackRate = r; },
      sleep() {},             // браузер: таймер ведёт контроллер (tick)
      stop() { wantPlay = false; audio.pause(); audio.removeAttribute("src"); if (url) URL.revokeObjectURL(url); url = null; part = -1; },
      get native() { return false; },
    };
  })();

  // ---------- звук в приложении (Android) ----------
  const nativeEngine = (() => {
    let poll = 0;
    const N = () => window.Native.Ab;
    function startPoll() {
      clearInterval(poll);
      poll = setInterval(async () => {
        if (document.hidden || !cur) return;
        try { applyState(await N().state()); } catch (_) {}
      }, 500);
    }
    function applyState(st) {
      if (!st || !cur || st.bookId !== cur.id) return;
      const wasPlaying = playing;
      playing = !!st.playing; loading = !!st.loading;
      nativeSleepLeft = st.sleepLeft || 0;
      if (typeof st.at === "number" && !dragging) tick(st.at, true);
      if (st.error) { ctx.toast("Аудиокнига: " + st.error, 6000); }
      if (st.ended && wasPlaying) ended(true);
      if (wasPlaying && !playing) save(true);
      render();
    }
    return {
      async open(book) {
        await N().open({ id: book.id, title: book.title, authors: book.authors,
          parts: book.parts.map((p) => ({ off: p.off, size: p.size, duration: p.duration, mime: p.mime })),
          chapters: book.chapters.map((c) => ({ title: c.title, at: c.at })), at });
        startPoll();
      },
      play(t) { loading = true; render(); N().play(t, rate()).catch((e) => ctx.toast("Аудиокнига: " + e.message, 5000)); },
      pause() { N().pause().catch(() => {}); playing = false; render(); },
      seek(t) { N().seek(t).catch(() => {}); },
      setRate(r) { N().setRate(r).catch(() => {}); },
      sleep(minutes, chEnd) { N().sleep(minutes, chEnd).catch(() => {}); },
      stop() { N().stop().catch(() => {}); clearInterval(poll); },
      applyState,
      get native() { return true; },
    };
  })();

  // ---------- ход воспроизведения ----------
  function tick(t, fromNative) {
    if (!cur) return;
    at = t;
    if (!fromNative && playing && sleep) {
      if (sleep.until && Date.now() >= sleep.until) return sleepFired();
      if (sleep.chapterEnd !== undefined && at >= chapterEnd(sleep.chapterEnd) - 0.3) return sleepFired();
    }
    if (Date.now() - savedAt > SAVE_EVERY) save();
    renderTime();
  }
  function sleepFired() {
    sleep = null;
    pause();
    ctx.toast("Таймер сна: пауза");
    render();
  }
  function ended(fromNative) {
    playing = false; loading = false;
    at = cur.duration;
    save(true);
    render();
    if (!fromNative || !document.hidden) ctx.toast("Аудиокнига прослушана до конца");
  }

  async function save(force) {
    if (!cur || cur.temp) return;
    if (!force && Math.abs(at - lastSaved) < 1) return;
    savedAt = Date.now();
    lastSaved = at;
    const progress = cur.duration ? Math.min(1, at / cur.duration) : 0;
    cur.meta.apos = at; cur.meta.progress = progress;
    try { await ctx.Library.update(cur.id, { apos: at, progress, openedAt: Date.now() }); } catch (_) {}
    ctx.onProgress(cur.id, progress);
  }

  // ---------- управление ----------
  function play() {
    if (!cur) return;
    ctx.pauseTts();
    if (cur.duration && at >= cur.duration - 1) at = 0;     // дослушали — сначала
    engine().play(at);
    playing = true;
    render();
  }
  function pause() {
    if (!cur) return;
    engine().pause();
    playing = false; loading = false;
    save(true);
    render();
  }
  const toggle = () => (playing || loading ? pause() : play());
  function seekTo(t) {
    if (!cur) return;
    at = clampAt(t);
    engine().seek(at);
    renderTime();
    save(true);
  }
  const skip = (d) => seekTo(at + d);
  function goChapter(i) {
    if (!cur || !cur.chapters.length) return;
    i = Math.max(0, Math.min(cur.chapters.length - 1, i));
    seekTo(cur.chapters[i].at);
    if (sleep && sleep.chapterEnd !== undefined) sleep.chapterEnd = i;
  }
  function prevChapter() {
    const c = chapterOf(at);
    // в начале главы (первые 3 с) — на предыдущую, иначе — к началу этой
    goChapter(at - cur.chapters[c].at > 3 ? c : c - 1);
  }
  const nextChapter = () => goChapter(chapterOf(at) + 1);
  function setRate(r) {
    ctx.store.set("abRate", r);
    engine().setRate(r);
    render();
  }

  async function sleepDialog() {
    const now = engine().native
      ? (nativeSleepLeft === -1 ? "пауза в конце главы" : nativeSleepLeft > 0 ? `пауза через ${Math.ceil(nativeSleepLeft / 60)} мин` : "")
      : sleep ? (sleep.until ? `пауза через ${Math.max(0, Math.ceil((sleep.until - Date.now()) / 60000))} мин` : "пауза в конце главы") : "";
    const opts = [15, 30, 45, 60, 90].map((m) => ({ label: m + " минут", value: m }));
    const v = await ctx.ask("Таймер сна", `<p>Воспроизведение встанет на паузу через выбранное время.</p>` + (now ? `<p><b>Сейчас:</b> ${now}.</p>` : ""),
      [...opts, { label: "До конца главы", value: "chapter" }, ...(now ? [{ label: "Выключить таймер", value: "off" }] : [])]);
    if (v === null || v === undefined || !cur) return;
    if (v === "off") {
      sleep = null; nativeSleepLeft = 0;
      engine().sleep(0, false);
      ctx.toast("Таймер сна выключен");
    } else if (v === "chapter") {
      sleep = { chapterEnd: chapterOf(at) };
      nativeSleepLeft = -1;
      engine().sleep(0, true);
      ctx.toast("Таймер сна: пауза в конце главы");
    } else {
      sleep = { until: Date.now() + v * 60000 };
      nativeSleepLeft = v * 60;
      engine().sleep(v, false);
      ctx.toast(`Таймер сна: пауза через ${v} мин`);
    }
    render();
  }

  // ---------- открыть / закрыть ----------
  // meta — карточка из библиотеки; temp — { blob } для прослушивания без добавления (браузер)
  async function open(meta, temp) {
    if (!meta.audio || !(meta.audio.parts || []).length) throw new Error("в карточке нет сведений об аудиокниге — удалите её и добавьте заново");
    const same = cur && cur.id === meta.id;
    if (!same) {
      if (cur) { save(true); engine().stop(); }
      sleep = null;
      const blob = temp ? temp.blob : window.Native ? null : await ctx.Library.blob(meta.id);
      if (!window.Native && !blob) throw new Error("файл аудиокниги не найден в хранилище");
      cur = load(meta, { temp: !!temp, blob });
      at = Math.min(meta.apos || 0, cur.duration);
      playing = false; loading = false;
      await engine().open(cur);
      if (!temp) ctx.store.set("lastAudio", meta.id);
    }
    show(true);
    render();
    renderChapters();
    if (!temp) ctx.Library.update(meta.id, { openedAt: Date.now() }).catch(() => {});
    if (!same && at > 5) ctx.toast(`Продолжаем с ${A.fmtTime(at)}`, 2000);
  }
  // при запуске: последняя аудиокнига — в мини-плеер (без воспроизведения)
  async function restore(meta) {
    if (!meta || !meta.audio || cur) return;
    try {
      const blob = window.Native ? null : await ctx.Library.blob(meta.id);
      if (!window.Native && !blob) return;
      cur = load(meta, { blob });
      at = Math.min(meta.apos || 0, cur.duration);
      if (window.Native) {
        // приложение могло играть в фоне, пока страницу перезагружали — подхватываем
        const st = await window.Native.Ab.state().catch(() => null);
        await engine().open(cur);
        if (st && st.bookId === cur.id) nativeEngine.applyState(st);
      } else await engine().open(cur);
      render();
    } catch (_) { cur = null; }
  }
  // книгу удалили из библиотеки
  function forget(id) {
    if (!cur || cur.id !== id) return;
    engine().stop();
    cur = null; playing = false;
    show(false);
    ctx.store.set("lastAudio", null);
    render();
  }
  function reset(id) {
    if (!cur || cur.id !== id) return;
    at = 0; lastSaved = -1;
    engine().seek(0);
    render();
  }
  function rename(id, title, authors) {
    if (!cur || cur.id !== id) return;
    cur.title = title; cur.authors = authors;
    render();
  }

  // ---------- экран ----------
  let el = {};
  function show(on) {
    el.view.hidden = !on;
    document.documentElement.classList.toggle("ab-open", on);
    if (!on) el.chapters.hidden = true;
    renderMini();
  }
  const isOpen = () => el.view && !el.view.hidden;
  function close() {
    if (!el.chapters.hidden) { el.chapters.hidden = true; return; }
    show(false);
  }

  const baseName = (n) => String(n).split("/").pop().replace(/\.[^.]+$/, "");
  function coverHtml() {
    if (cur.cover) return `<img src="${cur.cover}" alt="">`;
    return `<div class="ab-ph"><svg><use href="#i-voice"/></svg></div>`;
  }
  function render() {
    if (!el.view) return;
    renderMini();
    if (!cur) return;
    el.cover.innerHTML = coverHtml();
    el.title.textContent = cur.title;
    el.author.textContent = cur.authors.join(", ") || "Автор не указан";
    el.play.classList.toggle("playing", playing);
    el.play.classList.toggle("loading", loading && playing);
    el.play.title = playing ? "Пауза (пробел)" : "Слушать (пробел)";
    el.rate.value = String(rate());
    const sl = engine().native ? nativeSleepLeft : sleep ? (sleep.until ? 1 : -1) : 0;
    el.sleep.classList.toggle("on", !!sl);
    el.prevCh.disabled = el.nextCh.disabled = cur.chapters.length < 2;
    renderTime();
  }
  function renderTime() {
    if (!cur || !el.view) return;
    const c = chapterOf(at), ch = cur.chapters[c] || { title: cur.title, at: 0 };
    const cs = ch.at, ce = chapterEnd(c), len = Math.max(1, ce - cs);
    el.chapter.textContent = ch.title || `Глава ${c + 1}`;
    el.chNum.textContent = cur.chapters.length > 1 ? `Глава ${c + 1} из ${cur.chapters.length}` : "";
    if (!dragging) el.seek.value = String(Math.round(((at - cs) / len) * 1000));
    el.pos.textContent = A.fmtTime(at - cs);
    el.left.textContent = "−" + A.fmtTime(Math.max(0, ce - at));
    const pct = cur.duration ? Math.min(100, Math.floor((at / cur.duration) * 100)) : 0;
    const left = Math.max(0, cur.duration - at) / rate();
    el.total.textContent = `${pct}% · осталось ${A.fmtDuration(left)}` + (rate() !== 1 ? ` при ${String(rate()).replace(".", ",")}×` : "");
    const cl = el.chList.querySelector(".current");
    const now = el.chList.querySelector(`[data-c="${c}"]`);
    if (cl !== now) { if (cl) cl.classList.remove("current"); if (now) now.classList.add("current"); }
    renderMiniTime();
  }
  function renderChapters() {
    if (!cur) return;
    el.chList.innerHTML = cur.chapters.map((c, i) =>
      `<button class="ab-ch" data-c="${i}"><span class="ab-ch-t">${ctx.esc(c.title || `Глава ${i + 1}`)}</span><span class="ab-ch-time">${A.fmtTime(c.at)}</span></button>`).join("");
  }
  function openChapters() {
    if (!cur) return;
    el.chapters.hidden = false;
    renderTime();
    const c = el.chList.querySelector(".current");
    if (c) c.scrollIntoView({ block: "center" });
  }

  // мини-плеер внизу библиотеки
  function renderMini() {
    if (!el.mini) return;
    el.mini.hidden = !cur || isOpen();
    if (!cur) return;
    el.miniCover.innerHTML = cur.cover ? `<img src="${cur.cover}" alt="">` : `<div class="ab-ph"><svg><use href="#i-voice"/></svg></div>`;
    el.miniTitle.textContent = cur.title;
    el.miniPlay.classList.toggle("playing", playing);
    el.miniPlay.title = playing ? "Пауза" : "Слушать";
    renderMiniTime();
  }
  function renderMiniTime() {
    if (!el.mini || el.mini.hidden || !cur) return;
    const c = cur.chapters[chapterOf(at)];
    const pct = cur.duration ? Math.floor((at / cur.duration) * 100) : 0;
    el.miniSub.textContent = `${c && cur.chapters.length > 1 ? c.title + " · " : ""}${pct}%`;
  }

  function bind() {
    el = {
      view: $("ab"), cover: $("abCover"), title: $("abTitle"), author: $("abAuthor"), chapter: $("abChapter"), chNum: $("abChNum"),
      seek: $("abSeek"), pos: $("abPos"), left: $("abLeft"), total: $("abTotal"), play: $("abPlay"),
      back15: $("abBack15"), fwd30: $("abFwd30"), prevCh: $("abPrevCh"), nextCh: $("abNextCh"),
      rate: $("abRate"), sleep: $("abSleep"), chBtn: $("abChBtn"), chapters: $("abChapters"), chList: $("abChList"),
      chClose: $("abChClose"), back: $("abBack"), lib: $("abLib"),
      mini: $("abMini"), miniOpen: $("abMiniOpen"), miniCover: $("abMiniCover"), miniTitle: $("abMiniTitle"), miniSub: $("abMiniSub"), miniPlay: $("abMiniPlay"),
    };
    el.rate.innerHTML = RATES.map((r) => `<option value="${r}">${String(r).replace(".", ",")}×</option>`).join("");
    el.play.onclick = toggle;
    el.back15.onclick = () => skip(-15);
    el.fwd30.onclick = () => skip(30);
    el.prevCh.onclick = prevChapter;
    el.nextCh.onclick = nextChapter;
    el.rate.onchange = () => setRate(Number(el.rate.value));
    el.sleep.onclick = sleepDialog;
    el.chBtn.onclick = openChapters;
    el.chapter.onclick = openChapters;
    el.chClose.onclick = () => { el.chapters.hidden = true; };
    el.chList.onclick = (e) => {
      const b = e.target.closest("[data-c]");
      if (!b) return;
      goChapter(Number(b.dataset.c));
      el.chapters.hidden = true;
      if (!playing) play();
    };
    el.back.onclick = () => show(false);
    el.lib.onclick = () => ctx.openLibrary();
    el.seek.addEventListener("input", () => {
      dragging = true;
      const c = chapterOf(at), cs = cur.chapters[c] ? cur.chapters[c].at : 0, ce = chapterEnd(c);
      const t = cs + (Number(el.seek.value) / 1000) * (ce - cs);
      el.pos.textContent = A.fmtTime(t - cs);
      el.left.textContent = "−" + A.fmtTime(Math.max(0, ce - t));
    });
    el.seek.addEventListener("change", () => {
      const c = chapterOf(at), cs = cur.chapters[c] ? cur.chapters[c].at : 0, ce = chapterEnd(c);
      dragging = false;
      seekTo(Math.min(ce - 0.5, cs + (Number(el.seek.value) / 1000) * (ce - cs)));
    });
    el.miniOpen.onclick = () => { if (cur) { ctx.closePanels(); show(true); render(); renderChapters(); } };
    el.miniPlay.onclick = toggle;
    document.addEventListener("visibilitychange", () => { if (document.hidden) save(true); });
    window.addEventListener("beforeunload", () => save(true));
  }

  // кнопки гарнитуры / экрана блокировки в браузере: аудиокнига, если её экран открыт или она играет
  function mediaAction(name) {
    if (!cur || !(isOpen() || playing)) return false;
    ({ play, pause, stop: pause, previoustrack: () => skip(-15), nexttrack: () => skip(30), seekbackward: () => skip(-15), seekforward: () => skip(30) }[name] || (() => {}))();
    return true;
  }

  // клавиши на экране плеера
  function key(e) {
    if (!isOpen()) return false;
    if (e.code === "Space") { e.preventDefault(); toggle(); return true; }
    if (e.code === "ArrowLeft") { e.preventDefault(); skip(-15); return true; }
    if (e.code === "ArrowRight") { e.preventDefault(); skip(30); return true; }
    if (e.code === "Escape") { e.preventDefault(); close(); return true; }
    return e.code === "KeyT" || e.code === "KeyP";   // клавиши текстовой книги здесь не работают
  }

  // ---------- добавление ----------
  // разделить выбранные файлы: аудиокниги и всё остальное (ZIP — по содержимому)
  async function split(files) {
    const audio = [], rest = [];
    for (const f of files) ((await A.isAudioBook(f.name, A.blobReader(f))) ? audio : rest).push(f);
    return { audio, rest };
  }
  async function readCover(book) {
    if (!book.cover) return null;
    const url = URL.createObjectURL(new Blob([book.cover.data], { type: book.cover.mime }));
    try { return await ctx.thumbnail(url); } finally { URL.revokeObjectURL(url); }
  }
  const progressToast = (what) => (f) => ctx.toast(`${what}… ${Math.floor(f * 100)}%`, 8000);

  // браузер: файлы аудио/ZIP → книги (одна или отдельные), спросить про хранение, открыть последнюю
  async function importBrowser(files, openIt = true) {
    let groups;
    const singles = files.filter((f) => A.isAudioName(f.name));
    if (singles.length > 1 && (await ctx.askAssemble(singles.length, true)) === "one")
      groups = [singles, ...files.filter((f) => !singles.includes(f)).map((f) => [f])];
    else groups = files.map((f) => [f]);
    let last = null;
    for (const g of groups) {
      try {
        let blob = g[0], name = g[0].name;
        if (g.length > 1 || (A.ext(name) === "zip" && (await A.zipNeedsRepack(A.blobReader(blob))))) {
          ctx.toast("Собираю аудиокнигу…", 8000);
          const parts = await A.collectParts(g);
          name = g.length > 1 ? A.packName(parts.map((p) => p.name)) : name;
          blob = await A.packZip(parts, progressToast("Собираю аудиокнигу"));
        }
        ctx.toast(`Читаю «${name}»…`, 8000);
        const book = await A.readBook(A.blobReader(blob), name);
        const cover = await readCover(book);
        const est = await ctx.Library.estimate();
        const free = est && est.quota ? est.quota - est.usage : 0;
        const v = await ctx.ask("Аудиокнига", `<p><b>${ctx.esc(book.title)}</b> · ${A.fmtDuration(book.duration)} · ${ctx.fmtSize(blob.size)}</p>
          <p class="set-hint">Хранилище браузера ограничено${free ? ` (свободно примерно ${ctx.fmtSize(free)})` : ""}. Можно слушать, не сохраняя книгу:
          тогда после перезагрузки страницы файл придётся выбрать снова.</p>`,
          [{ label: "Слушать без добавления", value: "temp" }, { label: "Добавить в библиотеку", value: "add", primary: true }]);
        if (!v) continue;
        if (v === "temp") {
          const meta = { id: "temp:" + name + ":" + blob.size, title: book.title, authors: book.authors, cover, format: "audio", audio: window.AudioCard.audioInfo(book), apos: 0 };
          last = { meta, temp: { blob } };
        } else {
          if (free && blob.size > free) throw new Error("не хватает места в хранилище браузера");
          const meta = await ctx.Library.addAudio(blob, name, book, cover);
          last = { meta };
        }
      } catch (e) {
        console.error(e);
        ctx.toast(`«${g[0].name}»: ${e.message}`, 5000);
        await new Promise((r) => setTimeout(r, 1200));
      }
    }
    await ctx.refreshLibrary();
    if (openIt && last) { ctx.closePanels(); await open(last.meta, last.temp); ctx.hideToast(); }
    return !!last;
  }

  // приложение: файлы уже в библиотеке (C# скопировал их) → разобрать; несколько — собрать в одну книгу
  async function importNative(picked) {
    const L = ctx.Library;
    let items = picked;
    if (picked.length > 1 && picked.filter((p) => A.isAudioName(p.name)).length > 1 && (await ctx.askAssemble(picked.length, true)) === "one") {
      ctx.toast("Собираю аудиокнигу…", 30000);
      try {
        const packed = await L.packAudio(picked.map((p) => p.id), A.packName(picked.map((p) => p.name)));
        for (const p of picked) if (p.isNew && p.id !== packed.id) await L.remove(p.id).catch(() => {});
        items = [Object.assign(packed, { isNew: true })];
      } catch (e) { ctx.toast("Не получилось собрать аудиокнигу: " + e.message, 5000); return null; }
    }
    let lastId = null;
    for (const p0 of items) {
      let p = p0;
      try {
        let r = await L.reader(p.id);
        if (A.ext(p.name) === "zip" && (await A.zipNeedsRepack(r))) {
          ctx.toast("Перекладываю архив без сжатия…", 30000);
          const packed = await L.packAudio([p.id], p.name);
          if (p.isNew && packed.id !== p.id) await L.remove(p.id).catch(() => {});
          p = Object.assign(packed, { isNew: true });
          r = await L.reader(p.id);
        }
        const meta0 = await L.get(p.id);
        if (p.isNew || !meta0 || !meta0.audio) {
          ctx.toast(`Читаю «${p.name}»…`, 8000);
          const book = await A.readBook(r, p.name);
          await L.update(p.id, { title: book.title, authors: book.authors, format: "audio", formatLabel: window.AudioCard.audioLabel(book),
            cover: await readCover(book), audio: window.AudioCard.audioInfo(book), apos: 0 });
        }
        lastId = p.id;
      } catch (e) {
        console.error(e);
        if (p.isNew) await L.remove(p.id).catch(() => {});
        ctx.toast(`«${p.name}»: ${e.message}`, 5000);
        await new Promise((res) => setTimeout(res, 1200));
      }
    }
    await ctx.refreshLibrary();
    return lastId;
  }

  window.Audiobook = {
    init(c) { ctx = c; bind(); },
    open, restore, forget, reset, mediaAction, rename, close, isOpen, key, play, pause, toggle,
    split, importBrowser, importNative,
    get current() { return cur ? cur.id : null; },
    get playing() { return playing; },
    onNativeState: (st) => nativeEngine.applyState(st),
    // для проверок
    _state: () => ({ at, playing, loading, sleep, part: cur ? partOf(at) : -1, chapter: cur ? chapterOf(at) : -1 }),
  };
})();
