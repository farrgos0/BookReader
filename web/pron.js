/* Личные правила произношения (BookReader 1.2.0).
   Хранятся на этом устройстве (в браузере — localStorage, в приложении — ещё и в его настройках, чтобы
   их видела нативная озвучка) и уходят на сервер с каждым запросом озвучки — только те, что действуют
   для открытой книги. Сервер их не хранит. Формат правила: { id, w, c, s, books }:
     w — слово как в книге, c — учитывать регистр, s — как звучит, books — null (во всех книгах) или список id книг. */
(function () {
  "use strict";
  const KEY = "fb2r:pron";
  const VOWELS = "аеёиоуыэюяАЕЁИОУЫЭЮЯ";

  function load() {
    try {
      const d = JSON.parse(localStorage.getItem(KEY) || "null");
      if (d && Array.isArray(d.rules)) return { rules: d.rules, opts: d.opts || {} };
    } catch (_) {}
    return { rules: [], opts: {} };
  }
  let data = load();
  const listeners = [];
  // приложение: если хранилище страницы очистилось, а в настройках приложения правила есть — берём их оттуда
  if (window.Native && !data.rules.length) {
    window.Native.getPref("pron").then((j) => {
      if (!j || data.rules.length) return;
      try {
        const d = JSON.parse(j);
        if (d && Array.isArray(d.rules)) { data = { rules: d.rules, opts: d.opts || {} }; localStorage.setItem(KEY, j); }
      } catch (_) {}
    }).catch(() => {});
  }

  async function save(next) {
    data = { rules: next.rules.filter((r) => r.w && r.s && !checkSay(r.s)), opts: next.opts || {} };
    const json = JSON.stringify(data);
    try { localStorage.setItem(KEY, json); } catch (_) {}
    // в приложении правила читает нативная озвучка — дожидаемся записи, прежде чем она возьмёт озвучку заново
    try { if (window.Native) await window.Native.setPref("pron", json); } catch (_) {}
    listeners.forEach((f) => { try { f(data); } catch (_) {} });
  }

  // та же проверка, что на сервере (ru_rules.check_say)
  function checkSay(s) {
    s = (s || "").trim();
    if (!s) return "пусто";
    if (/\d/.test(s)) return "цифры не принимаются — напишите словами";
    if (/[A-Za-z]/.test(s)) return "латиница не принимается — напишите русскими буквами";
    if (!/^[А-Яа-яЁё+\- ]+$/.test(s)) return "можно только русские буквы, «ё», «+», пробел и дефис";
    if (s.includes("++")) return "два «+» подряд";
    if (new RegExp(`\\+(?![${VOWELS}])`).test(s)) return "«+» ставится перед ударной гласной";
    if (!/[А-Яа-яЁё]/.test(s)) return "нет букв";
    return "";
  }

  /** Что отправить серверу для книги id: { rules, opts } (пустые поля не отправляем). */
  function payload(bookId) {
    const out = {};
    const rules = data.rules.filter((r) => !r.books || (bookId && r.books.includes(bookId)))
      .map((r) => ({ w: r.w, c: !!r.c, s: r.s }));
    if (rules.length) out.rules = rules;
    if (data.opts && data.opts.date_order && data.opts.date_order !== "dmy") out.opts = { date_order: data.opts.date_order };
    return out;
  }
  /** Отпечаток набора для книги — меняется, когда меняются действующие для неё правила (кэш озвучки). */
  function stamp(bookId) { return JSON.stringify(payload(bookId)); }

  const uid = () => Math.random().toString(36).slice(2, 10);

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
      </ul></details>`;

    const html = `<div class="pr">
      <label class="set-field"><span>Даты вида 01.02.2001</span>
        <select id="prDate"><option value="dmy">ДД.ММ.ГГГГ — первое февраля</option><option value="mdy">ММ.ДД.ГГГГ — второе января</option></select>
      </label>
      <div class="pr-list" id="prList"></div>
      <button class="set-btn" id="prAdd">+ Добавить слово</button>
      ${help}
      <div class="pr-io"><button class="set-btn" id="prExport">Экспорт</button><button class="set-btn" id="prImport">Импорт</button>
        <input type="file" id="prFile" accept=".json,application/json" hidden></div>
    </div>`;
    const buttons = [{ label: "Отмена", value: null }, { label: "Сохранить", primary: true, value: "save" }];
    const pr = ctx.ask("Произношение", html, buttons);
    const body = ctx.askEl.body;
    const saveBtn = ctx.askEl.actions.querySelector('[data-i="1"]');
    const list = body.querySelector("#prList");
    // кнопки (▶, ✕, флажок) не забирают фокус у поля ввода: на телефоне иначе прячется клавиатура,
    // окно сдвигается — и касание попадает не туда (например, в выбор даты)
    body.addEventListener("pointerdown", (e) => {
      const a = document.activeElement;
      if (a && a.matches && a.matches(".pr-w, .pr-s") && e.target.closest("button, .pr-case")) e.preventDefault();
    });
    body.querySelector("#prDate").value = work.opts.date_order || "dmy";
    body.querySelector("#prDate").onchange = (e) => { work.opts.date_order = e.target.value; };

    function scopeLabel(r) { return !r.books ? "Во всех книгах" : `В выбранных: ${r.books.length}`; }

    function row(r) {
      const d = document.createElement("div");
      d.className = "pr-rule";
      d.innerHTML = `<div class="pr-line">
          <input class="pr-w" placeholder="слово" value="${esc(r.w)}" maxlength="60" aria-label="Слово как в книге">
          <label class="pr-case" title="Учитывать регистр"><input type="checkbox"${r.c ? " checked" : ""}><span>Аа</span></label>
          <span class="pr-eq">=</span>
          <input class="pr-s" placeholder="как звучит, + перед ударной" value="${esc(r.s)}" maxlength="200" aria-label="Как звучит">
          <button class="icon-btn pr-play" title="Прослушать">▶</button>
          <button class="icon-btn pr-del" title="Удалить правило">✕</button>
        </div>
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
        const e = s.value.trim() || w.value.trim() ? checkSay(s.value) : "";
        err.textContent = e ? "Как звучит: " + e : (s.value.trim() && !w.value.trim() ? "Укажите слово" : "");
        err.hidden = !err.textContent;
        s.classList.toggle("bad", !!e);
        refresh();
      };
      w.oninput = () => { r.w = w.value.trim(); validate(); };
      s.oninput = () => { r.s = s.value.replace(/\s+/g, " ").trim(); validate(); };
      d.querySelector(".pr-case input").onchange = (e) => { r.c = e.target.checked; };
      d.querySelector(".pr-del").onclick = () => { work.rules = work.rules.filter((x) => x !== r); d.remove(); refresh(); };
      const sum = d.querySelector(".pr-scope summary"), books = d.querySelector(".pr-books");
      d.querySelectorAll(`input[name="sc-${r.id}"]`).forEach((x) => (x.onchange = () => {
        r.books = x.value === "all" ? null : (r.books || []);
        books.hidden = !r.books;
        sum.textContent = scopeLabel(r);
      }));
      books.querySelectorAll("input[type=checkbox]").forEach((x) => (x.onchange = () => {
        const set = new Set(r.books || []);
        if (x.checked) set.add(x.value); else set.delete(x.value);
        r.books = [...set];
        sum.textContent = scopeLabel(r);
      }));
      d.querySelector(".pr-play").onclick = async () => {
        if (!r.w || !r.s || checkSay(r.s)) { validate(); return; }
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
    function refresh() {
      const bad = work.rules.some((r) => (r.w || r.s) && (!r.w || checkSay(r.s)));
      saveBtn.disabled = bad;
      if (!work.rules.length && !list.querySelector(".pr-empty")) {
        list.innerHTML = '<div class="pr-empty">Личных правил пока нет. Добавьте слово, которое звучит неправильно.</div>';
      }
    }
    function render() {
      list.innerHTML = "";
      work.rules.forEach((r) => list.appendChild(row(r)));
      refresh();
    }
    body.querySelector("#prAdd").onclick = () => {
      const r = { id: uid(), w: "", c: false, s: "", books: null };
      work.rules.push(r);
      const empty = list.querySelector(".pr-empty");
      if (empty) empty.remove();
      const d = row(r);
      list.appendChild(d);
      d.querySelector(".pr-w").focus();
    };
    body.querySelector("#prExport").onclick = () => {
      const blob = new Blob([JSON.stringify({ bookreader: "pronunciation", version: 1, ...work }, null, 2)], { type: "application/json" });
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
        const add = (d.rules || []).filter((r) => r && r.w && r.s && !checkSay(r.s))
          .map((r) => ({ id: uid(), w: String(r.w), c: !!r.c, s: String(r.s), books: Array.isArray(r.books) ? r.books.map(String) : null }));
        const known = new Set(work.rules.map((r) => r.w + "\n" + (r.c ? 1 : 0)));
        add.forEach((r) => { if (!known.has(r.w + "\n" + (r.c ? 1 : 0))) work.rules.push(r); });
        if (d.opts && d.opts.date_order) work.opts.date_order = d.opts.date_order;
        body.querySelector("#prDate").value = work.opts.date_order || "dmy";
        render();
        ctx.toast(`Добавлено правил: ${add.length}`, 3000);
      } catch (e) { ctx.toast("Файл не прочитан: " + e.message, 4000); }
      file.value = "";
    };
    render();
    const v = await pr;
    if (v === "save") { await save(work); return true; }
    return false;
  }

  window.Pron = { get: () => data, save, payload, stamp, checkSay, dialog, onChange: (f) => listeners.push(f) };
})();
