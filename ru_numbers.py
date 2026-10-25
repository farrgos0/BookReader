"""
Перевод чисел в русском тексте в слова — для озвучки Silero (сама модель цифры пропускает).

Без внешних зависимостей. Учитывает:
  * целые и дробные числа: 1 000 000, 3,5, -7
  * порядковые с окончаниями: 5-й, 2-я, 90-е, 1990-х, 3-го
  * годы и века: в 1987 году, 1812 г., XIX век, в XX в., 20 века
  * даты: 12 мая 1987, 12.05.1987
  * римские цифры до C (100): «Глава IV», «Пётр I», «XIX век», «IV» в заголовке
  * проценты, градусы, №, время 12:30
  * согласование: 1 книга → одна, 2 книги → две, до 5 лет → до пяти

Проверка из консоли:  python ru_numbers.py "В 1987 году, в XX веке, 5% и глава IV"
"""

import re
import sys

# ---------- количественные ----------

UNITS = ["ноль", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять"]
TEENS = ["десять", "одиннадцать", "двенадцать", "тринадцать", "четырнадцать", "пятнадцать",
         "шестнадцать", "семнадцать", "восемнадцать", "девятнадцать"]
TENS = ["", "", "двадцать", "тридцать", "сорок", "пятьдесят", "шестьдесят", "семьдесят",
        "восемьдесят", "девяносто"]
HUNDREDS = ["", "сто", "двести", "триста", "четыреста", "пятьсот", "шестьсот", "семьсот",
            "восемьсот", "девятьсот"]
# (форма для 1, для 2–4, для 5+, род числительного перед ней)
SCALES = [
    None,
    ("тысяча", "тысячи", "тысяч", "f"),
    ("миллион", "миллиона", "миллионов", "m"),
    ("миллиард", "миллиарда", "миллиардов", "m"),
    ("триллион", "триллиона", "триллионов", "m"),
]

GEN = {
    "ноль": "ноля", "один": "одного", "одна": "одной", "одно": "одного", "два": "двух", "две": "двух",
    "три": "трёх", "четыре": "четырёх", "пять": "пяти", "шесть": "шести", "семь": "семи",
    "восемь": "восьми", "девять": "девяти", "десять": "десяти", "сорок": "сорока",
    "девяносто": "девяноста", "сто": "ста", "двести": "двухсот", "триста": "трёхсот",
    "четыреста": "четырёхсот", "пятьсот": "пятисот", "шестьсот": "шестисот", "семьсот": "семисот",
    "восемьсот": "восьмисот", "девятьсот": "девятисот",
    "двадцать": "двадцати", "тридцать": "тридцати", "пятьдесят": "пятидесяти",
    "шестьдесят": "шестидесяти", "семьдесят": "семидесяти", "восемьдесят": "восьмидесяти",
}
for _t in TEENS[1:]:
    GEN[_t] = _t[:-1] + "и"  # одиннадцать → одиннадцати


def plural(n, one, few, many):
    n = abs(n)
    if 11 <= n % 100 <= 14:
        return many
    if n % 10 == 1:
        return one
    if 2 <= n % 10 <= 4:
        return few
    return many


def _triad(n, gender):
    words = []
    h, rest = divmod(n, 100)
    if h:
        words.append(HUNDREDS[h])
    if 10 <= rest < 20:
        words.append(TEENS[rest - 10])
    else:
        t, u = divmod(rest, 10)
        if t:
            words.append(TENS[t])
        if u:
            if u == 1:
                words.append({"m": "один", "f": "одна", "n": "одно"}[gender])
            elif u == 2:
                words.append("две" if gender == "f" else "два")
            else:
                words.append(UNITS[u])
    return words


def _groups(n):
    groups = []
    while n:
        n, g = divmod(n, 1000)
        groups.append(g)
    return groups


def cardinal(n, gender="m", case="nom"):
    """Количественное числительное. case: nom или gen."""
    if n == 0:
        return "ноль" if case == "nom" else "ноля"
    if n < 0:
        return "минус " + cardinal(-n, gender, case)
    groups = _groups(n)
    if len(groups) > len(SCALES):
        return digits(str(n))
    words = []
    for s in range(len(groups) - 1, -1, -1):
        g = groups[s]
        if not g:
            continue
        if s == 0:
            part = _triad(g, gender)
            words += [GEN.get(w, w) for w in part] if case == "gen" else part
        else:
            one, few, many, sg = SCALES[s]
            part = _triad(g, sg)
            if case == "gen":
                part = [GEN.get(w, w) for w in part]
                scale = (one[:-1] + "и" if sg == "f" else one + "а") if (g % 10 == 1 and g % 100 != 11) else many
            else:
                scale = plural(g, one, few, many)
            if g == 1 and case == "nom":
                part = []  # «тысяча», а не «одна тысяча»
            words += part + [scale]
    return " ".join(words)


def digits(s):
    return " ".join(UNITS[int(c)] for c in s if c.isdigit())


# ---------- порядковые ----------

# основа, тип склонения: h — как «пятый», o — как «второй», t — «третий»
ORD_UNITS = [("нулев", "o"), ("перв", "h"), ("втор", "o"), ("трет", "t"), ("четвёрт", "h"),
             ("пят", "h"), ("шест", "o"), ("седьм", "o"), ("восьм", "o"), ("девят", "h")]
ORD_TEENS = [(w[:-1], "h") for w in TEENS]  # десят, одиннадцат, ...
ORD_TENS = [None, None, ("двадцат", "h"), ("тридцат", "h"), ("сороков", "o"), ("пятидесят", "h"),
            ("шестидесят", "h"), ("семидесят", "h"), ("восьмидесят", "h"), ("девяност", "h")]
ORD_HUNDREDS = [None, ("сот", "h"), ("двухсот", "h"), ("трёхсот", "h"), ("четырёхсот", "h"),
                ("пятисот", "h"), ("шестисот", "h"), ("семисот", "h"), ("восьмисот", "h"),
                ("девятисот", "h")]
ORD_SCALES = [None, "тысячн", "миллионн", "миллиардн", "триллионн"]

_HARD = {
    "m": {"nom": "ый", "gen": "ого", "dat": "ому", "acc": "ый", "ins": "ым", "prep": "ом"},
    "f": {"nom": "ая", "gen": "ой", "dat": "ой", "acc": "ую", "ins": "ой", "prep": "ой"},
    "n": {"nom": "ое", "gen": "ого", "dat": "ому", "acc": "ое", "ins": "ым", "prep": "ом"},
    "p": {"nom": "ые", "gen": "ых", "dat": "ым", "acc": "ые", "ins": "ыми", "prep": "ых"},
}
_THIRD = {
    "m": {"nom": "ий", "gen": "ьего", "dat": "ьему", "acc": "ий", "ins": "ьим", "prep": "ьем"},
    "f": {"nom": "ья", "gen": "ьей", "dat": "ьей", "acc": "ью", "ins": "ьей", "prep": "ьей"},
    "n": {"nom": "ье", "gen": "ьего", "dat": "ьему", "acc": "ье", "ins": "ьим", "prep": "ьем"},
    "p": {"nom": "ьи", "gen": "ьих", "dat": "ьим", "acc": "ьи", "ins": "ьими", "prep": "ьих"},
}


def _ord_word(stem, kind, gender, case):
    if kind == "t":
        return stem + _THIRD[gender][case]
    if kind == "o" and gender == "m" and case in ("nom", "acc"):
        return stem + "ой"
    return stem + _HARD[gender][case]


def ordinal(n, gender="m", case="nom"):
    """Порядковое числительное: 1987, m, prep → тысяча девятьсот восемьдесят седьмом."""
    if n == 0:
        return _ord_word("нулев", "o", gender, case)
    groups = _groups(n)
    if len(groups) > len(SCALES):
        return digits(str(n))
    low = next(i for i, g in enumerate(groups) if g)
    head = cardinal(n - groups[low] * 1000 ** low) if n - groups[low] * 1000 ** low else ""
    g = groups[low]
    if low > 0:
        # 1000 → тысячный, 2000 → двухтысячный, 300000 → трёхсоттысячный
        if g == 1:
            prefix = ""
        elif g == 100:
            prefix = "сто"
        else:
            prefix = "".join(GEN.get(w, w) for w in _triad(g, "m")).replace("одного", "одно")
        last = _ord_word(prefix + ORD_SCALES[low], "h", gender, case)
        return f"{head} {last}".strip()
    h, rest = divmod(g, 100)
    words = []
    if rest == 0:
        stem = ORD_HUNDREDS[h]
    else:
        if h:
            words.append(HUNDREDS[h])
        if 10 <= rest < 20:
            stem = ORD_TEENS[rest - 10]
        else:
            t, u = divmod(rest, 10)
            if u == 0:
                stem = ORD_TENS[t]
            else:
                if t:
                    words.append(TENS[t])
                stem = ORD_UNITS[u]
    words.append(_ord_word(stem[0], stem[1], gender, case))
    return f"{head} {' '.join(words)}".strip()


# ---------- римские цифры ----------

_ROMAN_VALUES = [(100, "C"), (90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX"),
                 (5, "V"), (4, "IV"), (1, "I")]
_ROMAN_MAP = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}


def int_to_roman(n):
    out = ""
    for v, s in _ROMAN_VALUES:
        while n >= v:
            out += s
            n -= v
    return out


def roman_to_int(s):
    """Число 1..100 или None, если это не корректная римская запись."""
    s = s.upper().replace("Х", "X").replace("І", "I").replace("С", "C")
    if not s or any(c not in _ROMAN_MAP for c in s):
        return None
    total, prev = 0, 0
    for ch in reversed(s):
        v = _ROMAN_MAP[ch]
        total = total - v if v < prev else total + v
        prev = max(prev, v)
    if not 1 <= total <= 100 or int_to_roman(total) != s:
        return None
    return total


# ---------- грамматика по соседнему слову ----------

def _forms(stem, gender, endings):
    return {stem + e: (gender, c) for e, c in endings}


_FEM_A = [("а", "nom"), ("ы", "gen"), ("и", "gen"), ("е", "prep"), ("у", "acc"), ("ой", "ins")]
_FEM_YA = [("я", "nom"), ("и", "gen"), ("е", "prep"), ("ю", "acc"), ("ей", "ins"), ("ёй", "ins")]
_MASC = [("", "nom"), ("а", "gen"), ("у", "dat"), ("ом", "ins"), ("е", "prep"),
         ("ы", ("p", "nom")), ("ов", ("p", "gen")), ("ах", ("p", "prep")), ("ам", ("p", "dat"))]


def _noun_table():
    t = {}
    for w in ["глав", "книг", "сцен", "картин", "лекци", "серия"]:
        t.update(_forms(w, "f", _FEM_A))
    t.update(_forms("стать", "f", _FEM_YA))
    t.update(_forms("сери", "f", _FEM_YA))
    t.update({"часть": ("f", "nom"), "части": ("f", "gen"), "частью": ("f", "ins")})
    for w in ["том", "раздел", "акт", "параграф", "пункт", "класс", "съезд", "эпизод", "урок"]:
        for e, c in _MASC:
            g, cs = (c if isinstance(c, tuple) else ("m", c))
            t[w + e] = (g, cs)
    t.update({"явление": ("n", "nom"), "явления": ("n", "gen"), "явлении": ("n", "prep")})
    return t


CHAPTER_NOUNS = _noun_table()

CENTURY = {
    "век": ("m", "nom"), "века": ("m", "gen"), "веку": ("m", "dat"), "веком": ("m", "ins"),
    "веке": ("m", "prep"), "веков": ("p", "gen"), "веках": ("p", "prep"), "векам": ("p", "dat"),
    "столетие": ("n", "nom"), "столетия": ("n", "gen"), "столетии": ("n", "prep"),
    "тысячелетие": ("n", "nom"), "тысячелетия": ("n", "gen"), "тысячелетии": ("n", "prep"),
}
YEAR = {
    "год": ("m", "nom"), "года": ("m", "gen"), "году": ("m", "prep"), "годом": ("m", "ins"),
    "годе": ("m", "prep"), "годы": ("p", "nom"), "годов": ("p", "gen"), "годах": ("p", "prep"),
    "годам": ("p", "dat"), "годами": ("p", "ins"),
}
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
          "сентября", "октября", "ноября", "декабря"]
UNITS_ABBR = {
    "кг": ("килограмм", "килограмма", "килограммов", "m"),
    "км": ("километр", "километра", "километров", "m"),
    "см": ("сантиметр", "сантиметра", "сантиметров", "m"),
    "мм": ("миллиметр", "миллиметра", "миллиметров", "m"),
    "руб": ("рубль", "рубля", "рублей", "m"),
    "коп": ("копейка", "копейки", "копеек", "f"),
    "тыс": ("тысяча", "тысячи", "тысяч", "f"),
    "млн": ("миллион", "миллиона", "миллионов", "m"),
    "млрд": ("миллиард", "миллиарда", "миллиардов", "m"),
}
CURRENCY = {
    "$": ("доллар", "доллара", "долларов", "m"),
    "€": ("евро", "евро", "евро", "n"),
    "£": ("фунт", "фунта", "фунтов", "m"),
    "₽": ("рубль", "рубля", "рублей", "m"),
    "¥": ("иена", "иены", "иен", "f"),
}
FEMALE_NAMES = ("екатерин", "елизавет", "анн", "мари", "елен", "изабелл", "виктори", "ольг",
                "софь", "софи", "маргарит", "луиз", "кристин", "жанн", "агафь")
GEN_PREPS = {"до", "от", "из", "около", "более", "менее", "свыше", "после", "без", "для",
             "кроме", "среди", "против", "у", "вокруг", "возле", "ради", "из-за", "больше",
             "меньше", "порядка", "нескольких", "вместо"}

_CASE_BY_SUFFIX = [
    (("ыми", "ими", "ми"), ("p", "ins")),
    (("ых", "их", "х"), ("p", "gen")),
    (("ого", "его", "го"), ("m", "gen")),
    (("ому", "ему", "му"), ("m", "dat")),
    (("ым", "им"), ("m", "ins")),
    (("ом", "ем", "м"), ("m", "prep")),
    (("ую", "ю"), ("f", "acc")),
    (("ая", "яя", "я"), ("f", "nom")),
    (("ой",), ("m", "nom")),
    (("ый", "ий", "й"), ("m", "nom")),
    (("ое", "ее", "е"), ("n", "nom")),
]


def _suffix_case(suf, n):
    suf = suf.lower()
    for ends, gc in _CASE_BY_SUFFIX:
        if suf in ends:
            if gc == ("n", "nom") and n >= 10 and n % 10 == 0:
                return ("p", "nom")  # 90-е → девяностые
            return gc
    return ("m", "nom")


def _name_case(name):
    """Пётр I / Петра I / Екатерина II / Екатерины II → род и падеж порядкового."""
    low = name.lower()
    fem = low.startswith(FEMALE_NAMES)
    if fem:
        for e, c in [("ой", "ins"), ("ей", "ins"), ("ы", "gen"), ("и", "gen"),
                     ("е", "dat"), ("у", "acc"), ("ю", "acc"), ("а", "nom"), ("я", "nom")]:
            if low.endswith(e):
                return ("f", c)
        return ("f", "nom")
    for e, c in [("ом", "ins"), ("ем", "ins"), ("а", "gen"), ("я", "gen"), ("у", "dat"),
                 ("ю", "dat"), ("е", "prep")]:
        if low.endswith(e):
            return ("m", c)
    return ("m", "nom")


def _prev_word(text, pos):
    m = re.search(r"([\w-]+)[\s ]*$", text[:pos])
    return m.group(1).lower() if m else ""


def _next_word(text, pos):
    m = re.match(r"[\s ]*([А-Яа-яЁё]+)", text[pos:])
    return m.group(1).lower() if m else ""


# ---------- основная функция ----------

ROMAN_RE = r"[IVXLC]+|[IVXLCХІС]*[ХІ][IVXLCХІС]*"
NUM = r"\d+"


def normalize(text):
    if not re.search(r"\d|[IVXLC]", text):
        return text

    t = text.replace(" ", " ").replace(" ", " ")
    t = t.replace("№", " номер ").replace("§", " параграф ")

    # 1 000 000 → 1000000
    t = re.sub(r"(?<=\d) (?=\d{3}(?!\d))", "", t)
    # даты ISO: 2024-01-05 → «пятое января две тысячи двадцать четвёртого года» (до разбора диапазонов)
    def date_iso(m):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if not (1 <= d <= 31 and 1 <= mo <= 12):
            return m.group(0)
        return f"{ordinal(d, 'n')} {MONTHS[mo - 1]} {ordinal(y, 'm', 'gen')} года"
    t = re.sub(r"\b(\d{4})-(\d{2})-(\d{2})\b", date_iso, t)

    # дроби: 1/2 → «одна вторая», 3/4 → «три четвёртых»; 1920/1080 → «тысяча девятьсот двадцать на тысячу восемьдесят»
    def fraction(m):
        a_, b_ = int(m.group(1)), int(m.group(2))
        if b_ == 0:
            return m.group(0)
        if 0 < a_ < b_ <= 100:
            one = a_ % 10 == 1 and a_ % 100 != 11
            den = ordinal(b_, "f", "nom") if one else ordinal(b_, "p", "gen")
            return f"{cardinal(a_, 'f')} {den}"
        return f"{cardinal(a_)} на {cardinal(b_)}"
    t = re.sub(r"(?<![\d/.,])(\d{1,4})\s*/\s*(\d{1,4})(?![\d/.,])", fraction, t)

    # минус перед числом: «-5», «−5» (не диапазон 1941-1945)
    t = re.sub(r"(?<![\w\d])[−-](?=\d)", "минус ", t)
    # диапазоны 1941–1945, 5-10
    t = re.sub(r"(?<=\d)\s*[–—]\s*(?=\d)", " — ", t)
    t = re.sub(r"(?<=\d)-(?=\d)", " — ", t)

    # даты 12.05.1987
    def date_dmy_parts(d, mo, y, whole):
        d, mo = int(d), int(mo)
        if not (1 <= d <= 31 and 1 <= mo <= 12):
            return whole
        y = int(y) + (2000 if len(y) == 2 and int(y) < 30 else 1900 if len(y) == 2 else 0)
        return f"{ordinal(d, 'n')} {MONTHS[mo - 1]} {ordinal(y, 'm', 'gen')} года"
    t = re.sub(r"\b(\d{1,2})([./])(\d{1,2})\2(\d{4}|\d{2})\b", lambda m: date_dmy_parts(m.group(1), m.group(3), m.group(4), m.group(0)), t)

    # время 12:30
    def clock(m):
        mm = m.group(2)
        minutes = "ноль ноль" if mm == "00" else "ноль " + UNITS[int(mm)] if mm[0] == "0" else cardinal(int(mm))
        return f"{cardinal(int(m.group(1)))} {minutes}"
    t = re.sub(r"\b(\d{1,2}):(\d{2})\b", clock, t)

    # день + месяц (+ год)
    month_re = "|".join(MONTHS)

    def day_month(m):
        d = int(m.group(1))
        prev = _prev_word(t, m.start())
        case = "gen" if prev in {"с", "до", "от", "после", "около", "со"} else "dat" if prev in {"к", "по"} else "nom"
        return f"{ordinal(d, 'n', case)} {m.group(2)}"
    t = re.sub(rf"\b(\d{{1,2}})\s+({month_re})\b", day_month, t, flags=re.IGNORECASE)
    t = re.sub(rf"\b({month_re})\s+(\d{{3,4}})(\s*(?:г\.|года\b))?",
               lambda m: f"{m.group(1)} {ordinal(int(m.group(2)), 'm', 'gen')} года", t, flags=re.IGNORECASE)

    # проценты и градусы
    def percent(m):
        num = m.group(1)
        if re.search(r"[.,]", num):
            return f"{decimal_words(num)} процента"
        n = int(num)
        return f"{cardinal(n)} {plural(n, 'процент', 'процента', 'процентов')}"
    t = re.sub(r"(\d+(?:[.,]\d+)?)\s*%", percent, t)

    def degrees(m):
        num = m.group(1)
        unit = {"C": " по Цельсию", "С": " по Цельсию", "F": " по Фаренгейту"}.get(m.group(2) or "", "")
        if re.search(r"[.,]", num):
            return f"{decimal_words(num)} градуса{unit}"
        n = int(num)
        return f"{cardinal(n)} {plural(n, 'градус', 'градуса', 'градусов')}{unit}"
    t = re.sub(r"(\d+(?:[.,]\d+)?)\s*°\s*([CСF])?", degrees, t)

    # порядковые с окончанием: 5-й, 1990-х, 3-го, 2-я
    t = re.sub(r"\b(\d+)\s*-\s*(ыми|ими|ми|ых|их|х|ого|его|го|ому|ему|му|ым|им|ом|ем|м|ую|ю|ая|яя|я|ой|ый|ий|й|ое|ее|е)\b",
               lambda m: ordinal(int(m.group(1)), *_suffix_case(m.group(2), int(m.group(1)))), t)

    # диапазон лет: 1941 — 1945 годы / гг.
    def year_range(m):
        word = m.group(3).lower()
        prev = _prev_word(t, m.start())
        if word in ("гг.", "гг"):
            c, noun = ("prep", "годах") if prev == "в" else ("nom", "годы")
        else:
            c, noun = YEAR[word][1], m.group(3)
        return f"{ordinal(int(m.group(1)), 'm', c)} — {ordinal(int(m.group(2)), 'm', c)} {noun}"
    t = re.sub(r"\b(\d{1,4})\s*—\s*(\d{1,4})\s+(годы|годов|годах|годам|годами|гг\.)(?![\w])", year_range, t,
               flags=re.IGNORECASE)

    # единицы: 5 кг, 3,5 км, 100 руб., 10 тыс.
    def unit(m):
        num, u = m.group(1), m.group(2).lower().rstrip(".")
        one, few, many, g = UNITS_ABBR[u]
        if re.search(r"[.,]", num):
            return f"{decimal_words(num)} {few}"
        n = int(num)
        return f"{cardinal(n, g)} {plural(n, one, few, many)}"
    t = re.sub(r"(\d+(?:[.,]\d+)?)\s*(" + "|".join(UNITS_ABBR) + r")(?:\.|(?![\w]))", unit, t)

    # валюта: $100, 100 €, 5₽
    def money(m):
        num = m.group(2) or m.group(3)
        sym = m.group(1) or m.group(4)
        one, few, many, g = CURRENCY[sym]
        if re.search(r"[.,]", num):
            return f"{decimal_words(num)} {few}"
        n = int(num)
        return f"{cardinal(n, g)} {plural(n, one, few, many)}"
    t = re.sub(r"([$€£₽¥])\s?(\d+(?:[.,]\d+)?)|(\d+(?:[.,]\d+)?)\s?([$€£₽¥])", money, t)

    # годы: 1987 год/году/г.
    def year(m):
        n = int(m.group(1))
        word = m.group(2).lower()
        if word in ("г.", "г"):
            prev = _prev_word(t, m.start())
            g, c = ("m", "prep") if prev == "в" else ("m", "gen") if prev in GEN_PREPS | {"с"} else ("m", "nom")
            noun = {"prep": "году", "gen": "года", "nom": "год"}[c]
        elif word in ("гг.", "гг"):
            prev = _prev_word(t, m.start())
            g, c = ("p", "prep") if prev == "в" else ("p", "nom")
            noun = "годах" if c == "prep" else "годы"
        else:
            g, c = YEAR[word]
            noun = m.group(2)
        return f"{ordinal(n, g, c)} {noun}"
    t = re.sub(r"\b(\d{1,4})\s+(год|года|году|годом|годе|годы|годов|годах|годам|годами|гг\.|г\.)(?![\w])", year, t,
               flags=re.IGNORECASE)

    # сокращения веков: «XIX в.» → «XIX века», «в XX в.» → «в XX веке», «XIX—XX вв.» → «веков»
    def century_abbr(m):
        prev = _prev_word(t, m.start())
        many = m.group(2) == "вв."
        if prev == "в":
            noun = "веках" if many else "веке"
        else:
            noun = "веков" if many else "века"
        return f"{m.group(1)} {noun}"
    t = re.sub(r"((?<![\w])(?:[IVXLCХІ]+|\d{1,2})(?:\s*—\s*(?:[IVXLCХІ]+|\d{1,2}))?)\s*(вв?\.)", century_abbr, t)

    # века арабскими: 20 век, 19 века
    t = re.sub(r"\b(\d{1,2})\s+(" + "|".join(CENTURY) + r")\b",
               lambda m: f"{ordinal(int(m.group(1)), *CENTURY[m.group(2).lower()])} {m.group(2)}", t, flags=re.IGNORECASE)

    t = _romans(t)
    t = _decimals_and_integers(t)
    return re.sub(r"[ \t]{2,}", " ", t)


# общеупотребительные сокращения (работают и без чисел)
_ABBR = [
    (r"\bт\.\s?е\.", "то есть"),
    (r"\bт\.\s?к\.", "так как"),
    (r"\bт\.\s?д\.", "так далее"),
    (r"\bт\.\s?п\.", "тому подобное"),
    (r"\bи\s+др\.", "и другие"),
    (r"\bи\s+пр\.", "и прочее"),
    (r"\bстр\.\s?(?=\d)", "страница "),
    (r"\bгл\.\s?(?=\d|[IVXLC]+\b)", "глава "),
    (r"(?<!\d)(?<!\d\s)\bсм\.(?!\s?\d)", "смотри"),
]
_MULT_UNIT = re.compile(r"\b(тысяч[аи]?|миллион(?:а|ов)?|миллиард(?:а|ов)?)\s+(кг|км|см|мм|руб|коп)\.?(?![\w])")


def abbreviations(text):
    """«т. е.» → «то есть», «стр. 5» → «страница 5». Вызывать ДО normalize()."""
    for pat, rep in _ABBR:
        text = re.sub(pat, rep, text)
    return text


def units_after_words(text):
    """«две тысячи км» → «две тысячи километров». Вызывать ПОСЛЕ normalize()."""
    return _MULT_UNIT.sub(lambda m: f"{m.group(1)} {UNITS_ABBR[m.group(2)][2]}", text)


def decimal_words(num):
    a, b = re.split(r"[.,]", num, maxsplit=1)
    b = b[:3]
    ia, ib = int(a), int(b)
    whole = f"{cardinal(ia, 'f')} {plural(ia, 'целая', 'целых', 'целых')}"
    den = {1: ("десятая", "десятых"), 2: ("сотая", "сотых"), 3: ("тысячная", "тысячных")}[len(b)]
    frac = f"{cardinal(ib, 'f')} {den[0] if ib % 10 == 1 and ib % 100 != 11 else den[1]}"
    return f"{whole} {frac}"


def _romans(t):
    roman_tok = r"(?<![\w])([IVXLCХІ]+)(?![\w])"

    def repl(m):
        tok = m.group(1)
        latin_only = re.fullmatch(r"[IVXLC]+", tok) is not None
        n = roman_to_int(tok)
        if n is None:
            return tok
        nxt = _next_word(t, m.end())
        nxt_abbr = re.match(r"\s*(вв?\.)", t[m.end():])
        prev = _prev_word(t, m.start())
        prev_raw = re.search(r"([A-ZА-ЯЁ][a-zа-яё]+)[\s ]+$", t[:m.start()])

        # века: XIX век, в XX веке, XIX—XX веков
        def century(noun):
            g, c = CENTURY[noun]
            return ordinal(n, "m" if g == "p" else g, c)
        if nxt in CENTURY:
            return century(nxt)
        rng = re.match(r"\s*—\s*[IVXLCХІ]+\s+([а-яё]+)", t[m.end():])
        if rng and rng.group(1) in CENTURY:
            return century(rng.group(1))
        if not latin_only:
            return tok  # кириллица, похожая на римские цифры, — только при «век»
        # Глава IV, в главе IV, Том II
        if prev in CHAPTER_NOUNS:
            return ordinal(n, *CHAPTER_NOUNS[prev])
        # Пётр I, Екатерины II
        if prev_raw and len(tok) <= 5:
            return ordinal(n, *_name_case(prev_raw.group(1)))
        # одиночные I, V, C в обычном тексте не трогаем (могут быть английскими словами)
        if len(tok) == 1:
            whole = t.strip(" .:\n")
            return cardinal(n) if whole == tok else tok
        return cardinal(n)

    # строка-заголовок целиком из римского числа: «IV.» → «четыре»
    return re.sub(roman_tok, repl, t)


def _decimals_and_integers(t):
    def dec(m):
        return decimal_words(m.group(0))
    t = re.sub(r"(?<![\d.,])\d+[.,]\d{1,3}(?![\d]|[.,]\d)", dec, t)

    def integer(m):
        s = m.group(0)
        if len(s) > 1 and s.startswith("0"):
            return digits(s)
        if len(s) > 15:
            return digits(s)
        n = int(s)
        nxt = _next_word(t, m.end())
        prev = _prev_word(t, m.start())
        gender = "m"
        last = n % 10 if not 11 <= n % 100 <= 14 else 0
        if nxt:
            if last == 1 and nxt[-1] in "ая":
                gender = "f"
            elif last == 1 and nxt[-1] in "оеё" and nxt not in ("ее", "её"):
                gender = "n"
            elif last == 2 and nxt[-1] in "иы":
                gender = "f"
        case = "gen" if prev in GEN_PREPS else "nom"
        return cardinal(n, gender, case)
    t = re.sub(r"\d+", integer, t)
    return t


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    src = " ".join(sys.argv[1:]) or "В 1987 году, в XX веке, 5% и Глава IV."
    print(normalize(src))
