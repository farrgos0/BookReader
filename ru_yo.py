"""
Ударения и «ё» перед синтезом — под контролем BookReader, а не только Silero.

Silero ставит «ё» и ударения сама (см. tests/accent_check.py):
  1) омографы — нейросетью по контексту («королева» → корол+ева / корол+ёва);
  2) остальные слова — по одному, без контекста («селен» → сел+ён).
Почти все её ошибки — в словах с заглавной буквы: имена и фамилии («Королева» → «Королёва», «Селен» → «Селён»),
а заглавная в начале предложения сбивает выбор омографа.

Поэтому сервер сам вызывает расстановку Silero (model.accentor) и поправляет результат:
  * слово в начале предложения разбирается со строчной буквы — как обычное слово, а не имя;
  * слово с заглавной в середине предложения — имя: «ё», которой не было в тексте, убираем
    (кроме имён, где «ё» правильная: Пётр, Фёдор, Семён, Артём, Алёна…);
  * готовый текст (с «+» и «ё») уходит в синтез с выключенной авторасстановкой — Silero его уже не меняет.
Личные правила произношения (шаг 3) встанут сюда же: их слова не трогаются вовсе.
"""
import re

# имена, в которых «ё» правильная, — во всех формах (без «ё», в нижнем регистре)
_YO_NAMES_BASE = {
    "пётр": None,                     # Петра, Петру — уже без «ё»
    "фёдор": "m", "семён": "m", "артём": "m", "парамён": "m",
    "алёна": "f", "алёша": "f", "лёша": "f", "лёва": "f", "лёня": "fя", "серёжа": "f", "фёкла": "f",
    "матрёна": "f", "ерёма": "f", "алёнушка": "f", "настёна": "f", "сёма": "f", "фёдя": "fя", "тёма": "f",
    "фёдорович": "m", "семёнович": "m", "артёмович": "m", "фёдоровна": "f", "семёновна": "f", "артёмовна": "f",
}


def _forms(base, kind):
    if kind is None:
        return {base}
    if kind == "m":
        return {base + e for e in ("", "а", "у", "ом", "е", "ы", "ов")} | ({base[:-2] + "ем", base[:-2] + "е"} if base.endswith("ич") else set())
    s = base[:-1]
    if kind == "fя":
        return {base, s + "и", s + "е", s + "ю", s + "ей"}
    return {base, s + "ы", s + "и", s + "е", s + "у", s + "ой", s + "ей"}


YO_NAMES = set()
for _b, _k in _YO_NAMES_BASE.items():
    YO_NAMES |= _forms(_b, _k)

# мужские фамилии на -ёв (Королёв, Горбачёв, Соловьёв): «ё» в суффиксе обычно правильная.
# Только формы, которые не совпадают с обычными словами: «Королёва/-ёву/-ёве/-ёвой» пишутся
# так же, как «королева/королеву/королеве/королевой», — их не берём.
_YO_SURNAME = re.compile(r"ёв(?:ым|ых|ыми)?$")

_WORD = re.compile(r"[А-Яа-яЁё+]+")
_SENT_END = re.compile(r"[.!?…]+[»\"')\]]*$")


def _letters(tok):
    m = _WORD.search(tok)
    return m.group(0) if m else ""


def _is_capitalized(w):
    w = w.replace("+", "")
    return len(w) > 1 and w[0].isupper() and not w.isupper()


def sentence_starts(tokens):
    """Номера слов (по пробелам), с которых начинается предложение."""
    starts, new = set(), True
    for i, tok in enumerate(tokens):
        if not _letters(tok):
            if re.fullmatch(r"[—–-]+", tok):
                new = True if new or i == 0 else new  # тире в начале реплики
            continue
        if new:
            starts.add(i)
        new = bool(_SENT_END.search(tok))
    return starts


def _lower_first(tok):
    for j, c in enumerate(tok):
        if c.isalpha():
            return tok[:j] + c.lower() + tok[j + 1:]
    return tok


def _upper_first(tok):
    for j, c in enumerate(tok):
        if c.isalpha():
            return tok[:j] + c.upper() + tok[j + 1:]
    return tok


def _drop_new_yo(orig, acc):
    """«ё», которой не было в исходном слове, → «е» (с сохранением регистра)."""
    if "ё" in orig.lower():
        return acc
    return acc.replace("ё", "е").replace("Ё", "Е")


def prepare(text, accentor, protect=()):
    """Текст с ударениями и «ё» для синтеза. accentor — model.accentor Silero.
    protect — номера слов, которые не трогать (личные правила)."""
    mark = "\ue000"   # ru_rules.MARK: слово личного правила — не трогаем
    parts = re.split(r"(\s+)", text)
    marked = {k // 2 for k, p in enumerate(parts) if k % 2 == 0 and mark in p}
    if marked:
        protect = set(protect) | marked
        parts = [p.replace(mark, "") for p in parts]
        text = "".join(parts)
    tokens = parts[0::2]
    starts = sentence_starts(tokens)
    lowered = set()
    src = list(tokens)
    for i in starts:
        tok = tokens[i]
        w = _letters(tok)
        if _is_capitalized(w) and i not in protect:
            src[i] = _lower_first(tok)
            lowered.add(i)
    kw = dict(put_stress=True, put_stress_homo=True, put_yo=True, put_yo_homo=True, stress_single_vowel=True)
    acc = accentor(text, **kw)                       # как есть — ударения омографов по нему
    acc_parts = re.split(r"(\s+)", acc)
    acc_tokens = acc_parts[0::2]
    if len(acc_tokens) != len(tokens):
        return acc   # разбивка не совпала — отдаём как есть, без поправок
    low_tokens = acc_tokens
    if lowered:
        joined = "".join(p if k % 2 else src[k // 2] for k, p in enumerate(parts))
        low_tokens = re.split(r"(\s+)", accentor(joined, **kw))[0::2]
        if len(low_tokens) != len(tokens):
            low_tokens, lowered = acc_tokens, set()
    out = []
    for i, (orig, a) in enumerate(zip(tokens, acc_tokens)):
        if i in protect:
            out.append(orig)
            continue
        if i in lowered:
            # начало предложения: ударение — как Silero решила сама; а если с заглавной и со строчной
            # «ё» разная — верим строчной: заглавная сбивает Silero в обе стороны
            low = _upper_first(low_tokens[i])
            if "ё" not in orig.lower() and ("ё" in a.lower()) != ("ё" in low.lower()):
                a = low   # «ё» решаем по слову со строчной: «Королёва» → «Королева», «Пчелы!» → «Пчёлы!»
        elif _is_capitalized(_letters(orig)):
            bare = _letters(a).replace("+", "").lower()
            if bare not in YO_NAMES and not _YO_SURNAME.search(bare):
                a = _drop_new_yo(orig, a)
        out.append(a)
    seps = acc_parts[1::2]
    res = []
    for i, tok in enumerate(out):
        res.append(tok)
        if i < len(seps):
            res.append(seps[i])
    return "".join(res)


def find_accentor(model, speaker=None):
    """Расстановка ударений внутри модели Silero: у v5 она в model.packages[…].accentor (у старых — model.accentor)."""
    acc = getattr(model, "accentor", None)
    if acc is not None:
        return acc
    packages = getattr(model, "packages", None) or []
    if speaker is not None and hasattr(model, "speaker_to_package"):
        i = model.speaker_to_package.get(speaker)
        if i is not None and getattr(packages[i], "accentor", None) is not None:
            return packages[i].accentor
    for p in packages:
        if getattr(p, "accentor", None) is not None:
            return p.accentor
    return None
