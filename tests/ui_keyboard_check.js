// Проверка: касание кнопок в окнах при открытой экранной клавиатуре (телефон).
// Клавиатура имитируется: фокус в поле — видимая область ниже, фокус ушёл — сразу прежняя (как на телефоне).
// Запуск: из папки web поднять сервер (python -m http.server 8765), затем
//   node tests/ui_keyboard_check.js [ширина высота] [адрес]      (нужен Playwright: npm i playwright)
// По умолчанию 390×800 и http://127.0.0.1:8765/. Шаг 9.3 аудита интерфейса.
const { chromium } = require('playwright');
(async () => {
  const W = +(process.argv[2] || 390), H = +(process.argv[3] || 800), KB = Math.round(H * 0.42);
  const b = await chromium.launch();
  const ctx = await b.newContext({ viewport: { width: W, height: H }, hasTouch: true, isMobile: true, deviceScaleFactor: 2 });
  const p = await ctx.newPage();
  const ok = (c, m) => { console.log((c ? 'ok   ' : 'FAIL ') + m); if (!c) process.exitCode = 1; };
  // «клавиатура»: фокус в текстовом поле — видимая область ниже на KB (окно прижато к верху и укорачивается),
  // фокус ушёл — сразу прежняя. Синхронно, как на телефоне: раскладка меняется до того, как палец отпущен.
  await p.addInitScript((KB) => {
    const st = document.createElement('style');
    st.textContent = 'html.kb #ask .modal-card, html.kb .prp-card { max-height: calc(100dvh - 32px - ' + KB + 'px) !important; } html.kb .prp-card { height: calc(100dvh - ' + KB + 'px) !important; }';
    document.addEventListener('DOMContentLoaded', () => document.head.appendChild(st));
    const text = (t) => t && t.matches && t.matches('input:not([type=checkbox]):not([type=radio]), textarea');
    document.addEventListener('focusin', (e) => { if (text(e.target)) document.documentElement.classList.add('kb'); });
    document.addEventListener('focusout', (e) => { if (text(e.target)) document.documentElement.classList.remove('kb'); });
  }, KB);
  await p.goto(process.argv[4] || 'http://127.0.0.1:8765/');
  await p.evaluate(() => localStorage.setItem('fb2r:pron', JSON.stringify({ v: 2, folders: [], rules: [{ id: '1', f: null, w: 'а', s: 'плюс', t: 1 }], opts: {} })));
  await p.reload(); await p.waitForTimeout(300);
  await p.evaluate(() => document.getElementById('btnPron').click()); await p.waitForTimeout(300);
  for (const [btn, name] of [['.pr-cancel', 'Отмена'], ['.pr-save', 'Сохранить']]) {
    await p.locator('.prp-rule .prp-open').first().tap(); await p.waitForTimeout(200);
    await p.locator('.pr-s').tap(); await p.waitForTimeout(400);
    if (name === 'Сохранить') { await p.locator('.pr-s').fill('пл+юс'); await p.waitForTimeout(100); }
    const box = await p.locator(btn).boundingBox();
    await p.touchscreen.tap(box.x + box.width / 2, box.y + box.height / 2); await p.waitForTimeout(500);
    const st = await p.evaluate(() => ({ open: !document.getElementById('ask').hidden,
      some: !!document.querySelector('#ask input[name=prsc][value=some]:checked'), s: JSON.parse(localStorage.getItem('fb2r:pron')).rules[0].s }));
    if (name === 'Отмена') ok(!st.open && !st.some, `${W}×${H}: «Отмена» при открытой клавиатуре — окно закрыто, книги не тронуты`);
    else ok(!st.open && st.s === 'пл+юс', `${W}×${H}: «Сохранить» при открытой клавиатуре — сохранено`);
    if (st.open) { await p.evaluate(() => document.getElementById('askClose').click()); await p.waitForTimeout(200); const l = p.locator('.pr-u-leave'); if (await l.isVisible()) await l.tap(); await p.waitForTimeout(200); }
  }
  // касание «Аа» при открытой клавиатуре: переключается флажок, а не то, что окажется под пальцем
  await p.locator('.prp-rule .prp-open').first().tap(); await p.waitForTimeout(200);
  await p.locator('.pr-s').tap(); await p.waitForTimeout(400);
  const cb = await p.locator('.pr-case input').boundingBox();
  await p.touchscreen.tap(cb.x + cb.width / 2, cb.y + cb.height / 2); await p.waitForTimeout(400);
  ok(await p.locator('.pr-case input').isChecked(), `${W}×${H}: «Аа» при открытой клавиатуре переключается`);
  ok(await p.evaluate(() => document.activeElement && document.activeElement.classList.contains('pr-s')), `${W}×${H}: после «Аа» клавиатура остаётся`);
  await p.locator('#askHead').tap(); await p.waitForTimeout(300);
  ok(await p.evaluate(() => !document.activeElement || !document.activeElement.matches('input')), `${W}×${H}: касание мимо поля прячет клавиатуру`);
  await b.close();
})();
