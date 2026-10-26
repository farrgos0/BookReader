"""Проверочный набор нормализатора (tests/normalizer_cases.tsv).

Отчёт:  python tests/normalizer_cases.py         — все случаи: что ждём и что получается сейчас
        python tests/normalizer_cases.py todo    — только то, что ещё не сделано
        python tests/normalizer_cases.py --out normalizer-report.txt  — в файл (UTF-8; перенаправление «>»
                                                  в Windows PowerShell портит кодировку)
Тест (unittest): случаи «ok» должны совпадать всегда; «todo» только считаются.
"""
import contextlib
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CASES = os.path.join(HERE, "normalizer_cases.tsv")


def norm(s):
    s = s.lower().replace("ё", "е").replace("—", " ").replace("-", " ")
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def same(got, want):
    return norm(got) == norm(want)


def load():
    rows = []
    with open(CASES, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.rstrip("\n")
            if not line.strip() or line.startswith("#"):
                continue
            p = line.split("\t")
            while len(p) < 5:
                p.append("")
            rows.append({"line": n, "group": p[0], "text": p[1], "want": p[2], "state": p[3] or "ok", "note": p[4]})
    return rows


def speak(text):
    sys.path.insert(0, os.path.dirname(HERE))
    with contextlib.redirect_stdout(io.StringIO()):
        import server
        was, server.ENGLISH = server.ENGLISH, True   # как у сервера по умолчанию (другие тесты выключают)
        try:
            return server.clean_for_speech(text)
        finally:
            server.ENGLISH = was


def main(argv):
    out_path = None
    if "--out" in argv:
        i = argv.index("--out")
        out_path = argv[i + 1] if i + 1 < len(argv) else "normalizer-report.txt"
        argv = argv[:i] + argv[i + 2:]
    if out_path:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = report(argv)
        with open(out_path, "w", encoding="utf-8-sig") as f:   # с BOM — Блокнот и PowerShell откроют правильно
            f.write(buf.getvalue())
        print(f"report: {os.path.abspath(out_path)}")
        return code
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    return report(argv)


def report(argv):
    only_todo = "todo" in argv
    rows = load()
    good = bad = todo_done = 0
    group = None
    for r in rows:
        got = speak(r["text"])
        ok = same(got, r["want"])
        if r["state"] == "ok":
            good += ok
            bad += not ok
        else:
            todo_done += ok
        if only_todo and r["state"] == "ok" and ok:
            continue
        if r["group"] != group:
            group = r["group"]
            print(f"\n== {group}")
        mark = "✓" if ok else ("✗ ОШИБКА" if r["state"] == "ok" else "· todo")
        print(f"  {mark}  {r['text']}\n       ждём:     {r['want']}" + ("" if ok else f"\n       сейчас:   {got}") +
              (f"\n       ({r['note']})" if r["note"] else ""))
    todo = sum(r["state"] != "ok" for r in rows)
    print(f"\nВсего {len(rows)}: работает {good}, сломалось {bad}, в плане {todo} (из них уже получается {todo_done})")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
