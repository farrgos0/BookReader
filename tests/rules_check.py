"""Проверка личных правил на настоящей модели Silero: «е» в правиле остаётся «е», «ё» остаётся «ё».

    python\\python.exe tests\\rules_check.py --out     (отчёт — reports\\rules-report.txt)

Для каждого слова — два правила без «+»: «слово → то же слово» и «слово → то же с «ё» вместо первой «е»»,
и две фразы: слово в середине и в начале предложения. В синтез должна уйти ровно та буква, что в правиле,
и у слова должно быть ударение.
Свои слова: --words "Королев,Селен,елка".
"""
import contextlib
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# слова, в которых Silero часто сама ставит «ё»
WORDS = ("Королев Королева Селен Ковалев Горбачев Хрущев Пугачева Лебедев Федор Семен Артем Алена Петр Матрена "
         "Фекла Тема Сема Лева Ерема Чернышев Соловьев Воробьев все ее елка мед лед черт пчелы звезды береза слезы "
         "легкий желтый тяжелый веселый клен шел нес вез жены щеки тетя осел небо узнаем передохнут падеж ежик "
         "темный зеленый еще").split()


def main(argv):
    out_path = None
    if "--out" in argv:
        i = argv.index("--out")
        has = i + 1 < len(argv) and not argv[i + 1].startswith("-")
        out_path = argv[i + 1] if has else "rules-report.txt"
        argv = argv[:i] + argv[i + (2 if has else 1):]
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import report_path
        out_path = report_path.resolve(out_path)
    words = WORDS
    if "--words" in argv:
        i = argv.index("--words"); words = [w.strip() for w in argv[i + 1].split(",") if w.strip()]
    with contextlib.redirect_stdout(io.StringIO()):
        import server
        import ru_rules
        import ru_yo
        model = server.load_silero()
    acc = ru_yo.find_accentor(model)
    lines, bad = [], 0
    if acc is None:
        lines.append("В модели нет accentor — проверка невозможна.")
    n = 0
    for w in words if acc else []:
        # 1) «е» в правиле — без «ё»;  2) обратно: «ё» в правиле вместо первой «е» — с «ё»
        variants = [(w, False)]
        if "е" in w.lower():
            i = w.lower().index("е")
            variants.append((w[:i] + ("Ё" if w[i].isupper() else "ё") + w[i + 1:], True))
        for say, want_yo in variants:
            pron = ru_rules.Pron([{"w": w, "c": True, "s": say}])
            for phrase in (f"Об этом сказал {w}.", f"{w} был рядом."):
                cleaned = server.clean_for_speech(phrase, pron=pron)
                res = ru_yo.prepare(cleaned, acc)
                tok = next((t for t in res.split() if t.replace("+", "").strip(".,").lower().replace("ё", "е") == w.lower()), "")
                ok = (("ё" in tok.lower()) == want_yo) and "+" in tok
                bad += not ok
                n += 1
                lines.append(f"{'OK  ' if ok else 'ОШИБКА'}  {w} → {say:<12} {phrase:<30} → {res}")
    lines.append(f"\nПроверено фраз: {n}, ошибок: {bad}")
    text = "\n".join(lines)
    if out_path:
        with open(out_path, "w", encoding="utf-8-sig") as f:
            f.write(text + "\n")
        print(f"report: {os.path.abspath(out_path)}")
    else:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        print(text)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
