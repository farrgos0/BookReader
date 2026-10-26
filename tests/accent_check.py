"""Как Silero расставит ударения и «ё» во фразе — без озвучки.

    python\\python.exe tests\\accent_check.py "Королева вошла в зал." "у Селена" "Селен"
    python\\python.exe tests\\accent_check.py --file phrases.txt --out accent-report.txt

Показывает три шага модели Silero v5_ru:
  1) омографы по контексту (нейросеть смотрит на всё предложение: «королева» → корол+ева / корол+ёва);
  2) остальные слова по отдельности (словарь исключений и модель без контекста: «селен» → сел+ён);
  3) итог самой Silero и итог BookReader (ru_yo.py: имена без лишней «ё») — он и уходит в синтез.
"""
import contextlib
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def main(argv):
    out_path = None
    if "--out" in argv:
        i = argv.index("--out")
        out_path = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    phrases = []
    if "--file" in argv:
        i = argv.index("--file")
        with open(argv[i + 1], encoding="utf-8-sig") as f:
            phrases += [ln.strip() for ln in f if ln.strip()]
        argv = argv[:i] + argv[i + 2:]
    phrases += argv
    if not phrases:
        phrases = ["Королева вошла в зал.", "И тут Королева вошла в зал.", "Мы видели Королеву.",
                   "Селен сказал.", "Сказал Селен.", "У Селена был меч.", "Пришёл Петр с Семеном.",
                   "Всё было хорошо, но все ушли.", "Замок на двери. Замок на горе."]
    with contextlib.redirect_stdout(io.StringIO()):
        import server
        model = server.load_silero()
    import ru_yo
    acc = ru_yo.find_accentor(model)
    lines = []
    if acc is None:
        lines.append("В этой версии модели нет accentor — проверка невозможна. Атрибуты модели: " +
                     ", ".join(a for a in dir(model) if not a.startswith("_")))
    else:
        for p in phrases:
            homo = acc.homosolver(p, put_stress=True, put_yo=True) if hasattr(acc, "homosolver") else p
            full = acc(p, put_stress=True, put_stress_homo=True, put_yo=True, put_yo_homo=True)
            no_yo = acc(p, put_stress=True, put_stress_homo=True, put_yo=False, put_yo_homo=False)
            import ru_yo
            ours = ru_yo.prepare(p, acc)
            lines += [f"«{p}»", f"   по контексту:   {homo}", f"   Silero сама:    {full}",
                      f"   без авто-«ё»:   {no_yo}", f"   BookReader:     {ours}   ← это уходит в синтез", ""]
    text = "\n".join(lines)
    if out_path:
        with open(out_path, "w", encoding="utf-8-sig") as f:
            f.write(text + "\n")
        print(f"report: {os.path.abspath(out_path)}")
    else:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        print(text)


if __name__ == "__main__":
    main(sys.argv[1:])
