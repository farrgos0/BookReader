/* Личные правила произношения (BookReader 1.2.0, «Произношение» 2.0 — данные в pron-model.js).
   Хранятся на этом устройстве (в браузере — localStorage, в приложении — ещё и в его настройках, чтобы
   их видела нативная озвучка) и уходят на сервер с каждым запросом озвучки — только те, что действуют
   для открытой книги. Сервер их не хранит. Формат правила: { id, f, w, c, s, books, t }:
     w — слово как в книге, c — учитывать регистр, s — как звучит, books — null (во всех книгах) или список id книг;
     f — папка (null — верхний уровень), t — время создания. */
(function () {
  "use strict";
  const KEY = "fb2r:pron";
  const M = window.PronModel;

  function load() {
    try {
      const j = localStorage.getItem(KEY);
      if (j) return M.migrate(JSON.parse(j));
    } catch (_) {}
    return M.empty();
  }
  let data = load();
  const listeners = [];
  // приложение: если хранилище страницы очистилось, а в настройках приложения правила есть — берём их оттуда
  if (window.Native && !data.rules.length && !data.folders.length) {
    window.Native.getPref("pron").then((j) => {
      if (!j || data.rules.length || data.folders.length) return;
      try {
        data = M.migrate(JSON.parse(j));
        localStorage.setItem(KEY, JSON.stringify(data));
      } catch (_) {}
    }).catch(() => {});
  }

  const complete = M.complete;
  const checkSay = M.checkSay;

  /** Сохранить данные (v2). Принимает и { rules, opts } без папок — папки тогда остаются прежними.
      Неполные правила не сохраняются (Произношение 2.0). */
  async function save(next) {
    const src = next && next.v === 2 ? next : { v: 2, folders: data.folders, rules: (next && next.rules) || [], opts: (next && next.opts) || {} };
    data = M.migrate(src);
    const json = JSON.stringify(data);
    try { localStorage.setItem(KEY, json); } catch (_) {}
    // в приложении правила читает нативная озвучка — дожидаемся записи, прежде чем она возьмёт озвучку заново
    try { if (window.Native) await window.Native.setPref("pron", json); } catch (_) {}
    listeners.forEach((f) => { try { f(data); } catch (_) {} });
  }

  /** Что отправить серверу для книги id: { rules, opts } (пустые поля не отправляем). */
  function payload(bookId) {
    const out = {};
    const rules = M.forBook(data, bookId);
    if (rules.length) out.rules = rules;
    if (data.opts && data.opts.date_order && data.opts.date_order !== "dmy") out.opts = { date_order: data.opts.date_order };
    return out;
  }
  /** Отпечаток набора для книги — меняется, когда меняются действующие для неё правила (кэш озвучки). */
  function stamp(bookId) { return JSON.stringify(payload(bookId)); }

  const uid = M.uid;

  // ---------- окно «Произношение» ----------
  // ctx: { ask, askEl, esc, toast, library: () => Promise<[{id,title}]>, catalog: () => [{id,title}], voice: () => id,
  //        speak: async (text, payload) => Blob }
  async function dialog(ctx) {
    const { esc } = ctx;
    const work = { rules: data.rules.map((r) => ({ ...r, books: r.books ? [...r.books] : null })), opts: { ...data.opts } };
    let lib = [];
    try { lib = await ctx.library(); } catch (_) {}
    const cat = (ctx.catalog() || []).filter((c) => !lib.some((b) => b.id === c.id));
    const titleOf = (id) => (lib.find((b) => b.id === id) || cat.find((c) => c.id === id) || { title: "книга не найдена" }).title;

    const help = `<details class="pr-help"><summary>Как записывать</summary>
      <ul>
        <li><b>+</b> перед ударной гласной: «игр+ок», «Да+эрон». Без «+» ударение поставит озвучка — проверьте кнопкой ▶.</li>
        <li>«е» и «ё» звучат как написано: «Королев» — через «е», «Королёв» — через «ё».</li>
        <li>Только русские буквы. Числа и латиницу — словами: «HP» → «эйч-п+и», «3» → «три».</li>
        <li><b>Аа</b> — только слово с заглавной: имя «Роза», но не цветок.</li>
        <li>Каждая форма слова — отдельное правило: «Тёма», «Тёму».</li>
        <li>Изменения сохраняются сами. <b>Не полностью заполненное правило</b> (нет слова, нет «как звучит» или
          в нём ошибка) не сохраняется.</li>
      </ul></details>`;

    const html = `<div class="pr">
      <div class="pr-unsaved" id="prUnsaved" hidden><b class="pr-u-title"></b>
        <span></span>
        <div class="pr-u-btns"><button class="set-btn pr-u-stay">Вернуться</button><button class="set-btn primary pr-u-leave">Выйти</button></div></div>
      <button class="set-btn" id="prAdd">+ Добавить слово</button>
      <div class="pr-list" id="prList"></div>
      ${help}
      <div class="pr-io"><button class="set-btn" id="prExport">Экспорт</button><button class="set-btn" id="prImport">Импорт</button>
        <input type="file" id="prFile" accept=".json,application/json" hidden></div>
    </div>`;

    // что реально звучит: только полностью заполненные правила — по ним решаем, менять ли озвучку после окна
    const effective = () => JSON.stringify([work.rules.filter(complete).map((r) => [r.w, !!r.c, r.s, r.books || null]), work.opts || {}]);
    const before = effective();
    let timer = 0;
    const flush = () => { clearTimeout(timer); timer = 0; return save(work); };
    const changed = () => { clearTimeout(timer); timer = setTimeout(flush, 400); };   // автосохранение

    // при выходе (✕, фон, Esc, «Назад» на телефоне): есть недозаполненные — предупредить
    let asking = null;
    const beforeClose = async () => {
      await flush();
      const bad = work.rules.filter((r) => (r.w || r.s) && !complete(r));
      if (!bad.length) return true;
      if (asking) return false;
      return new Promise((resolve) => {
        const bar = body.querySelector("#prUnsaved");
        bar.querySelector(".pr-u-title").textContent = bad.length === 1 ? "Одно правило заполнено не полностью."
          : `Не полностью заполнено правил: ${bad.length}.`;
        bar.querySelector("span").textContent = bad.length === 1
          ? "Оно не сохранится, если выйти сейчас."
          : "Они не сохранятся, если выйти сейчас.";
        bar.hidden = false;
        bar.scrollIntoView({ block: "nearest" });
        asking = (v) => { asking = null; bar.hidden = true; resolve(v); };
        bar.querySelector(".pr-u-stay").onclick = () => {
          asking(false);
          const first = list.querySelector(".pr-rule.incomplete");
          if (first) { first.scrollIntoView({ block: "center" }); (first.querySelector(".pr-w").value ? first.querySelector(".pr-s") : first.querySelector(".pr-w")).focus(); }
        };
        bar.querySelector(".pr-u-leave").onclick = () => asking(true);
      });
    };
    const pr = ctx.ask("Произношение", html, [], { beforeClose });
    const body = ctx.askEl.body;
    const list = body.querySelector("#prList");
    // окно прижато к верху: когда клавиатура появляется или прячется, оно не «прыгает» под пальцем
    const modal = ctx.askEl.box;
    modal.classList.add("pr-open");
    pr.finally(() => modal.classList.remove("pr-open"));
    // кнопки (▶, ✕, флажок) не забирают фокус у поля ввода: на телефоне иначе прячется клавиатура,
    // окно сдвигается — и касание попадает не туда
    body.addEventListener("pointerdown", (e) => {
      const a = document.activeElement;
      if (a && a.matches && a.matches(".pr-w, .pr-s") && e.target.closest("button, .pr-case")) e.preventDefault();
    });

    function scopeLabel(r) { return !r.books ? "Во всех книгах" : `В выбранных: ${r.books.length}`; }

    function row(r) {
      const d = document.createElement("div");
      d.className = "pr-rule";
      d.innerHTML = `<div class="pr-line">
          <input class="pr-w" placeholder="слово" value="${esc(r.w)}" maxlength="60" aria-label="Слово как в книге">
          <label class="pr-case" title="Учитывать регистр"><input type="checkbox"${r.c ? " checked" : ""}><span>Аа</span></label>
          <span class="pr-eq">=</span>
          <input class="pr-s" placeholder="как звучит, + перед ударной" value="${esc(r.s)}" maxlength="200" aria-label="Как звучит">
          <button class="icon-btn pr-play" title="Прослушать" aria-label="Прослушать">▶</button>
          <button class="icon-btn pr-del" title="Удалить правило" aria-label="Удалить правило">✕</button>
        </div>
        <div class="pr-confirm" hidden><span></span>
          <button class="set-btn pr-no">Отмена</button><button class="set-btn danger pr-yes">Удалить</button></div>
        <div class="pr-err" hidden></div>
        <details class="pr-scope"><summary>${esc(scopeLabel(r))}</summary>
          <label class="pr-opt"><input type="radio" name="sc-${r.id}" value="all"${r.books ? "" : " checked"}> Во всех книгах, включая новые</label>
          <label class="pr-opt"><input type="radio" name="sc-${r.id}" value="some"${r.books ? " checked" : ""}> Только в выбранных:</label>
          <div class="pr-books"${r.books ? "" : " hidden"}>
            ${lib.length || cat.length ? "" : '<div class="pr-none">В библиотеке пока нет книг</div>'}
            ${lib.map((b) => `<label class="pr-book"><input type="checkbox" value="${esc(b.id)}"${r.books && r.books.includes(b.id) ? " checked" : ""}> ${esc(b.title)}</label>`).join("")}
            ${cat.length ? '<div class="pr-sub">Каталог сервера</div>' + cat.map((b) => `<label class="pr-book"><input type="checkbox" value="${esc(b.id)}"${r.books && r.books.includes(b.id) ? " checked" : ""}> ${esc(b.title)}</label>`).join("") : ""}
            ${(r.books || []).filter((id) => !lib.some((b) => b.id === id) && !cat.some((c) => c.id === id))
              .map((id) => `<label class="pr-book"><input type="checkbox" value="${esc(id)}" checked> ${esc(titleOf(id))}</label>`).join("")}
          </div>
        </details>`;
      const w = d.querySelector(".pr-w"), s = d.querySelector(".pr-s"), err = d.querySelector(".pr-err");
      const validate = () => {
        const any = !!(w.value.trim() || s.value.trim());
        let msg = "";
        if (any && !w.value.trim()) msg = "Не указано слово";
        else if (any && !s.value.trim()) msg = "Не указано, как звучит";
        else if (any && checkSay(s.value)) msg = "Как звучит: " + checkSay(s.value);
        err.textContent = msg ? msg + " — правило не используется в озвучке" : "";
        err.hidden = !msg;
        s.classList.toggle("bad", any && !!s.value.trim() && !!checkSay(s.value));
        d.classList.toggle("incomplete", !!msg);
        empty();
      };
      w.oninput = () => { r.w = w.value.trim(); validate(); changed(); };
      s.oninput = () => { r.s = s.value.replace(/\s+/g, " ").trim(); validate(); changed(); };
      d.querySelector(".pr-case input").onchange = (e) => { r.c = e.target.checked; changed(); };
      const drop = () => { work.rules = work.rules.filter((x) => x !== r); d.remove(); empty(); changed(); };
      const confirm = d.querySelector(".pr-confirm");
      d.querySelector(".pr-del").onclick = () => {
        if (!r.w && !r.s) return drop();          // пустое, только что добавленное — без вопроса
        confirm.querySelector("span").textContent = `Удалить правило «${r.w || r.s}»?`;
        confirm.hidden = false;
      };
      confirm.querySelector(".pr-no").onclick = () => { confirm.hidden = true; };
      confirm.querySelector(".pr-yes").onclick = drop;
      const sum = d.querySelector(".pr-scope summary"), books = d.querySelector(".pr-books");
      d.querySelectorAll(`input[name="sc-${r.id}"]`).forEach((x) => (x.onchange = () => {
        r.books = x.value === "all" ? null : (r.books || []);
        books.hidden = !r.books;
        sum.textContent = scopeLabel(r);
        changed();
      }));
      books.querySelectorAll("input[type=checkbox]").forEach((x) => (x.onchange = () => {
        const set = new Set(r.books || []);
        if (x.checked) set.add(x.value); else set.delete(x.value);
        r.books = [...set];
        sum.textContent = scopeLabel(r);
        changed();
      }));
      d.querySelector(".pr-play").onclick = async () => {
        if (!complete(r)) { validate(); return; }
        try {
          const blob = await ctx.speak(r.w, { rules: [{ w: r.w, c: !!r.c, s: r.s }] });
          const url = URL.createObjectURL(blob);
          const a = new Audio(url);
          a.onended = () => URL.revokeObjectURL(url);
          await a.play();
        } catch (e) { ctx.toast("Не получилось прослушать: " + (e.message || e), 4000); }
      };
      validate();
      return d;
    }
    function empty() {
      const has = work.rules.length > 0;
      const msg = list.querySelector(".pr-empty");
      if (!has && !msg) list.innerHTML = '<div class="pr-empty">Личных правил пока нет. Добавьте слово, которое звучит неправильно.</div>';
      if (has && msg) msg.remove();
    }
    function render() {
      list.innerHTML = "";
      work.rules.forEach((r) => list.appendChild(row(r)));
      empty();
    }
    body.querySelector("#prAdd").onclick = () => {
      const r = { id: uid(), w: "", c: false, s: "", books: null };
      work.rules.unshift(r);                      // новые — сверху, ниже — старее
      const d = row(r);
      list.prepend(d);
      empty();
      d.querySelector(".pr-w").focus();
    };
    body.querySelector("#prExport").onclick = () => {
      const out = { bookreader: "pronunciation", version: 1, rules: work.rules.filter((r) => r.w || r.s), opts: work.opts };
      const blob = new Blob([JSON.stringify(out, null, 2)], { type: "application/json" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "bookreader-произношение.json";
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 2000);
    };
    const file = body.querySelector("#prFile");
    body.querySelector("#prImport").onclick = () => file.click();
    file.onchange = async () => {
      const f = file.files[0];
      if (!f) return;
      try {
        const d = JSON.parse(await f.text());
        const add = (d.rules || []).filter((r) => r && (r.w || r.s))
          .map((r) => ({ id: uid(), w: String(r.w || ""), c: !!r.c, s: String(r.s || ""), books: Array.isArray(r.books) ? r.books.map(String) : null }));
        const known = new Set(work.rules.map((r) => r.w + "\n" + (r.c ? 1 : 0)));
        const fresh = add.filter((r) => !known.has(r.w + "\n" + (r.c ? 1 : 0)));
        work.rules = fresh.concat(work.rules);    // добавленные — сверху
        if (d.opts && d.opts.date_order) work.opts.date_order = d.opts.date_order;
        render();
        changed();
        ctx.toast(`Добавлено правил: ${fresh.length}`, 3000);
      } catch (e) { ctx.toast("Файл не прочитан: " + e.message, 4000); }
      file.value = "";
    };
    render();
    await pr;
    await flush();
    return effective() !== before;   // true — звучание изменилось, полученную озвучку надо получить заново
  }

  window.Pron = { get: () => data, save, payload, stamp, checkSay, dialog, onChange: (f) => listeners.push(f) };
})();
