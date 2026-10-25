"""
Английские слова → русские буквы, чтобы русский голос Silero читал их правильно.

Порядок:
  1. исключения из data/en_ru.json (слова и фразы, правишь сам);
  2. словарь произношения CMUdict (data/cmudict.dict): слово → звуки → буквы по таблице из en_ru.json;
  3. аббревиатуры из заглавных букв — по буквам (BBC → би-би-си);
  4. запасные буквенные правила из en_ru.json.

CMUdict (~3.5 МБ) скачивается сам при первом запуске. Файл en_ru.json перечитывается
при изменении — можно дописывать исключения, не перезапуская сервер.

Проверка из консоли:  python en_translit.py "Games Workshop released Warhammer in London"
"""

import json
import os
import re
import sys
import threading
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
RULES_PATH = os.path.join(DATA, "en_ru.json")


def _dict_path():
    """Словарь рядом с программой, если он там уже есть или туда можно писать; иначе — в папке данных пользователя."""
    here = os.path.join(DATA, "cmudict.dict")
    if os.path.exists(here):
        return here
    import paths  # noqa: PLC0415 — нужен только при первом скачивании
    return os.path.join(paths.writable_dir(DATA, "data"), "cmudict.dict")


DICT_URLS = [
    "https://raw.githubusercontent.com/cmusphinx/cmudict/master/cmudict.dict",
    "https://github.com/cmusphinx/cmudict/raw/master/cmudict.dict",
]

VOWELS = {"AA", "AE", "AH", "AO", "AW", "AY", "EH", "ER", "EY", "IH", "IY", "OW", "OY", "UH", "UW"}
RU_VOWELS = set("аеёиоуыэюя")

_lock = threading.Lock()
_rules = None
_rules_mtime = None
_cmu = None
_cmu_state = "not loaded"
version = 0  # растёт при каждой перезагрузке правил — сервер добавляет его в ключ кеша


# ---------- загрузка ----------

def _load_rules():
    global _rules, _rules_mtime, version
    try:
        mtime = os.path.getmtime(RULES_PATH)
    except OSError:
        mtime = None
    if _rules is not None and mtime == _rules_mtime:
        return _rules
    with _lock:
        if _rules is not None and mtime == _rules_mtime:
            return _rules
        try:
            with open(RULES_PATH, encoding="utf-8") as f:
                raw = json.load(f)
        except Exception as e:  # noqa: BLE001
            print("en_ru.json не прочитан:", e)
            if _rules is not None:
                return _rules
            raw = {}
        exc = {k.lower(): v for k, v in raw.get("exceptions", {}).items()}
        phrases = sorted((k for k in exc if " " in k or "-" in k), key=len, reverse=True)
        combos = {tuple(k.split()): v for k, v in raw.get("combos", {}).items()}
        _rules = {
            "exceptions": exc,
            "phrase_re": re.compile(r"(?<![A-Za-z])(" + "|".join(re.escape(p) for p in phrases) + r")(?![A-Za-z])",
                                    re.IGNORECASE) if phrases else None,
            "phonemes": raw.get("phonemes", {}),
            "initial": raw.get("initial", {}),
            "combos": combos,
            "combo_max": max((len(k) for k in combos), default=1),
            "aa_with_o": raw.get("aa_with_o"),
            "soft_sign": raw.get("soft_sign", True),
            "letter_names": raw.get("letter_names", {}),
            "letters": [(re.compile(p), r) for p, r in raw.get("letters", [])],
        }
        _rules_mtime = mtime
        version += 1
        return _rules


def _parse_cmu(path):
    d = {}
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if not line or line.startswith(";;;"):
                continue
            word, *ph = line.split()
            word = word.lower()
            if word.endswith(")"):  # вариант произношения: word(2)
                continue
            d.setdefault(word, ph)
    return d


def _ensure_cmu():
    global _cmu, _cmu_state
    if _cmu is not None or _cmu_state == "loading":
        return
    _cmu_state = "loading"
    try:
        dict_path = _dict_path()
        if not os.path.exists(dict_path):
            for url in DICT_URLS:
                try:
                    print("Скачиваю словарь английского произношения CMUdict...")
                    tmp = dict_path + ".part"
                    urllib.request.urlretrieve(url, tmp)
                    os.replace(tmp, dict_path)
                    break
                except Exception as e:  # noqa: BLE001
                    print("  не вышло:", url, e)
        if os.path.exists(dict_path):
            _cmu = _parse_cmu(dict_path)
            _cmu_state = f"ok ({len(_cmu)} слов)"
            print("CMUdict загружен:", len(_cmu), "слов")
        else:
            _cmu_state = "нет словаря — только запасные правила"
            print("CMUdict недоступен — английские слова читаются по запасным правилам.",
                  "Можно положить cmudict.dict в папку data вручную.")
    except Exception as e:  # noqa: BLE001
        _cmu_state = f"ошибка: {e}"
        print("CMUdict: ошибка", e)


def preload(background=True):
    """Вызвать при старте сервера: подтянет правила и словарь."""
    _load_rules()
    if background:
        threading.Thread(target=_ensure_cmu, daemon=True).start()
    else:
        _ensure_cmu()


def status():
    return _cmu_state


# ---------- перевод одного слова ----------

def _from_phonemes(ph, spelling, rules):
    out = []
    i = 0
    has_o = "o" in spelling
    pmap, init, combos = rules["phonemes"], rules["initial"], rules["combos"]
    while i < len(ph):
        # сочетания (без цифр ударения)
        done = False
        for n in range(min(rules["combo_max"], len(ph) - i), 1, -1):
            key = tuple(re.sub(r"\d", "", p) for p in ph[i:i + n])
            if key in combos:
                val = combos[key]
                prev = "".join(out)
                if rules["soft_sign"] and val[:1] in "юяеё" and prev and prev[-1] not in RU_VOWELS and prev[-1] not in "ьъй":
                    val = "ь" + val
                out.append(val)
                i += n
                done = True
                break
        if done:
            continue
        p = ph[i]
        base = re.sub(r"\d", "", p)
        prev = "".join(out)
        at_start = not prev or prev[-1] in RU_VOWELS
        if base in VOWELS and at_start and (p in init or base in init):
            out.append(init.get(p, init.get(base)))
        elif base == "AA" and has_o and rules["aa_with_o"]:
            out.append(rules["aa_with_o"])
        else:
            out.append(pmap.get(p, pmap.get(base, "")))
        i += 1
    return "".join(out)


def _by_letters(word, rules):
    w = word.lower()
    for rx, rep in rules["letters"]:
        w = rx.sub(rep, w)
    return re.sub(r"[a-z]", "", w)


def word_to_ru(word, rules=None):
    rules = rules or _load_rules()
    low = word.lower()
    if low in rules["exceptions"]:
        return rules["exceptions"][low]
    cmu = _cmu or {}
    if low in cmu:
        return _from_phonemes(cmu[low], low, rules)
    # притяжательное 's и сокращения
    m = re.fullmatch(r"(.+?)'(s|ll|re|ve|d|t|m)", low)
    if m:
        base = word_to_ru(m.group(1), rules)
        tail = {"s": "с", "ll": "л", "re": "р", "ve": "в", "d": "д", "t": "т", "m": "м"}[m.group(2)]
        return base + tail
    # аббревиатура
    if word.isupper() and len(word) <= 5:
        return "-".join(rules["letter_names"].get(c.lower(), c) for c in word)
    return _by_letters(word, rules)


# ---------- текст ----------

WORD_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")


def transliterate(text):
    if not re.search(r"[A-Za-z]", text):
        return text
    rules = _load_rules()
    if _cmu is None and _cmu_state == "not loaded":
        _ensure_cmu()
    if rules["phrase_re"]:
        text = rules["phrase_re"].sub(lambda m: rules["exceptions"][m.group(0).lower()], text)

    def repl(m):
        w = m.group(0)
        ru = word_to_ru(w, rules)
        return ru.capitalize() if w[:1].isupper() and ru else ru

    return WORD_RE.sub(repl, text)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    preload(background=False)
    print(transliterate(" ".join(sys.argv[1:]) or "Games Workshop released Warhammer in London"))
