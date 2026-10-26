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

import ru_names
import ru_nouns

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

# дательный и творительный (для «к 3 урокам», «с 2 главами»); предложный = родительный, кроме «одном/одной»
DAT = dict(GEN)
DAT.update({"один": "одному", "одна": "одной", "одно": "одному", "два": "двум", "две": "двум", "три": "трём",
            "четыре": "четырём", "двести": "двумстам", "триста": "трёмстам", "четыреста": "четырёмстам",
            "пятьсот": "пятистам", "шестьсот": "шестистам", "семьсот": "семистам", "восемьсот": "восьмистам",
            "девятьсот": "девятистам", "ноль": "нолю"})
INS = {"один": "одним", "одна": "одной", "одно": "одним", "два": "двумя", "две": "двумя", "три": "тремя",
       "четыре": "четырьмя", "пять": "пятью", "шесть": "шестью", "семь": "семью", "восемь": "восемью",
       "девять": "девятью", "десять": "десятью", "сорок": "сорока", "девяносто": "девяноста", "сто": "ста",
       "двадцать": "двадцатью", "тридцать": "тридцатью", "пятьдесят": "пятьюдесятью",
       "шестьдесят": "шестьюдесятью", "семьдесят": "семьюдесятью", "восемьдесят": "восемьюдесятью",
       "двести": "двумястами", "триста": "тремястами", "четыреста": "четырьмястами", "пятьсот": "пятьюстами",
       "шестьсот": "шестьюстами", "семьсот": "семьюстами", "восемьсот": "восемьюстами",
       "девятьсот": "девятьюстами", "ноль": "нолём"}
for _t in TEENS[1:]:
    INS[_t] = _t[:-1] + "ью"  # одиннадцать → одиннадцатью
PREP = dict(GEN)
PREP.update({"один": "одном", "одна": "одной", "одно": "одном"})


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
    """Количественное числительное. case: nom, acc, gen, dat, ins, prep (косвенные — до тысячи)."""
    if case == "acc":
        words = cardinal(n, gender, "nom").split()
        if gender == "f" and words and words[-1] == "одна":
            words[-1] = "одну"   # «за одну книгу»
        return " ".join(words)
    if case in ("dat", "ins", "prep"):
        if not 0 <= n < 1000:
            return cardinal(n, gender, "gen")
        table = {"dat": DAT, "ins": INS, "prep": PREP}[case]
        return " ".join(table.get(w, w) for w in cardinal(n, gender, "nom").split())
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

DAYS_IN_MONTH = [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]


def _date_case(prev):
    """Падеж дня в дате по предлогу: «до первого», «к первому», «на первое»."""
    if prev in ("до", "с", "со", "от", "после", "около", "для", "без", "из", "у", "кроме", "начиная"):
        return "gen"
    if prev in ("к", "ко"):
        return "dat"
    return "nom"


def _clock_words(h, mm):
    minutes = "ноль ноль" if mm == "00" else "ноль " + UNITS[int(mm)] if mm[0] == "0" else cardinal(int(mm))
    return f"{cardinal(h)} {minutes}"


class _YearMatch:
    """Совпадение «2001 г.» и «2001г.» — с одинаковыми группами для year()."""
    def __init__(self, m):
        self._m = m

    def group(self, i):
        if i == 2:
            return self._m.group(2) or self._m.group(3)
        return self._m.group(i)

    def start(self, *a):
        return self._m.start(*a)

ROMAN_RE = r"[IVXLC]+|[IVXLCХІС]*[ХІ][IVXLCХІС]*"
NUM = r"\d+"


# личные настройки нормализатора (порядок даты и т. п.) — приходят от устройства вместе с запросом
DEFAULT_OPTIONS = {"date_order": "dmy"}


def normalize(text, options=None):
    opts = dict(DEFAULT_OPTIONS, **(options or {}))
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
        if re.search(r":\s*$", t[:m.start()]) and a_ <= b_:
            # характеристики: «Уровень навыка: 5/10», «HP: 50/100» — «пять из десяти»
            return f"{cardinal(a_)} из {cardinal(b_, 'm', 'gen')}"
        if 0 < a_ < b_ <= 100:
            one = a_ % 10 == 1 and a_ % 100 != 11
            den = ordinal(b_, "f", "nom") if one else ordinal(b_, "p", "gen")
            return f"{cardinal(a_, 'f')} {den}"
        return f"{cardinal(a_)} на {cardinal(b_)}"
    t = re.sub(r"(?<![\d/.,])(\d{1,4})\s*/\s*(\d{1,4})(?![\d/.,])", fraction, t)

    # плюс перед числом: «+25°», «(+15)»
    t = re.sub(r"(?<![\w\d)])\+(?=\d)", "плюс ", t)
    # минус перед числом: «-5», «−5» (не диапазон 1941-1945)
    t = re.sub(r"(?<![\w\d])[−-](?=\d)", "минус ", t)
    # диапазоны 1941–1945, 5-10
    t = re.sub(r"(?<=\d)\s*[–—]\s*(?=\d)", " — ", t)
    t = re.sub(r"(?<=\d)-(?=\d)", " — ", t)

    # даты 12.05.1987
    def date_dmy(m):
        a_, b_, y = m.group(1), m.group(3), m.group(4)
        d, mo = (int(b_), int(a_)) if opts.get("date_order") == "mdy" else (int(a_), int(b_))
        if not (1 <= d <= DAYS_IN_MONTH[mo - 1] if 1 <= mo <= 12 else False):
            return m.group(0)
        y = int(y) + (2000 if len(y) == 2 and int(y) < 30 else 1900 if len(y) == 2 else 0)
        case = _date_case(_prev_word(t, m.start()))
        return f"{ordinal(d, 'n', case)} {MONTHS[mo - 1]} {ordinal(y, 'm', 'gen')} года"
    # 1.2.2001, 01.02.2001, 1.2.2001г., 1.2.2001 г., 12/05/1987
    t = re.sub(r"(?<![\d.,/])(\d{1,2})([./])(\d{1,2})\2(\d{4}|\d{2})(?![\d])(?:\s*(?:г\.|года\b|г(?![\w])))?", date_dmy, t)

    # 9:30 am, 9 AM, 11 P.M. — только рядом со временем
    def ampm(m):
        h, mm, pm = int(m.group(1)), m.group(2), m.group(3).lower() == "p"
        if not 1 <= h <= 12 or (mm and int(mm) > 59):
            return m.group(0)
        h24 = h % 12 + (12 if pm else 0)
        part = "ночи" if h24 < 4 else "утра" if h24 < 12 else "дня" if h24 < 17 else "вечера"
        if mm:
            return f"{_clock_words(h, mm)} {part}"
        return f"{cardinal(h)} {plural(h, 'час', 'часа', 'часов')} {part}"
    t = re.sub(r"\b(\d{1,2})(?::(\d{2}))?\s*([AaPp])\.?\s?[Mm]\.?(?![\w])", ampm, t)

    # время 12:30
    def clock(m):
        return _clock_words(int(m.group(1)), m.group(2))
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
    t = re.sub(r"\b(\d{1,4})(?:\s+(год|года|году|годом|годе|годы|годов|годах|годам|годами|гг\.|г\.)|\s*(гг\.|г\.))(?![\w])",
               lambda m: year(_YearMatch(m)), t,
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

    # «с 1 по 5 главу» → «с первой по пятую главу», «с 3 по 7 урок»
    def from_to(m):
        an = ru_nouns.analyses(m.group(4))
        a = ru_nouns.pick(an, "по") if an else None
        if not a or a[4] != "sg":
            return m.group(0)
        return f"{m.group(1)} {ordinal(int(m.group(2)), a[1], 'gen')} по {ordinal(int(m.group(3)), a[1], a[3])} {m.group(4)}"
    t = re.sub(r"(?<![\w])([Сс]о?)\s+(\d{1,4})\s+по\s+(\d{1,4})\s+([А-Яа-яЁё]+)", from_to, t)

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
    (r"(?<=смотри )рис\.|\bрис\.\s?(?=\d)", "рисунок "),
]
_MULT_UNIT = re.compile(r"\b(тысяч[аи]?|миллион(?:а|ов)?|миллиард(?:а|ов)?)\s+(кг|км|см|мм|руб|коп)\.?(?![\w])")


# разговорные сокращения — целым словом, без учёта регистра
COLLOQUIAL = {"спс": "спасибо", "пжлст": "пожалуйста", "пжл": "пожалуйста", "плз": "пожалуйста",
              "мб": "может быть", "кмк": "как мне кажется", "оч": "очень"}
# «норм» — и «нормально», и родительный падеж «норм» (нет норм): заменяем только после этих слов или одно в предложении
_NORM_BEFORE = {"всё", "все", "вроде", "да", "ну", "это", "тоже", "так", "как", "вполне", "уже", "было",
                "будет", "очень", "оч", "впринципе", "принципе", "норм", "и", "а", "но", "мне", "тебе", "нам", "там"}


def _colloquial(text):
    def word(m):
        w = m.group(0)
        rep = COLLOQUIAL.get(w.lower())
        if not rep:
            return w
        return rep.capitalize() if w[0].isupper() else rep
    text = re.sub(r"(?<![\w.])[А-Яа-яЁё]+(?![\w.])", word, text)

    def norm_word(m):
        before = text[:m.start()]
        prev = re.search(r"([А-Яа-яЁё]+)[\s,]*$", before)
        sentence_start = not re.search(r"[А-Яа-яЁё0-9]", re.split(r"[.!?…]\s*", before)[-1])
        if sentence_start or (prev and prev.group(1).lower() in _NORM_BEFORE):
            return "Нормально" if m.group(0)[0].isupper() else "нормально"
        return m.group(0)
    return re.sub(r"(?<![\w])[Нн]орм(?![\w])", norm_word, text)


def _level_abbr(text):
    """Листы персонажей: «1 ур.» → «первый уровень», «на 10 ур.» → «на десятом уровне», «ур. 5» → «уровень 5»."""
    def num_lvl(m):
        n = int(m.group(1))
        case = ru_nouns.PREP_CASE.get(_prev_word(text, m.start()), "nom")
        if case == "dat" and _prev_word(text, m.start()) == "по":
            case = "dat"
        return f"{ordinal(n, 'm', case)} {ru_nouns.form_of('уровень', case)}"
    text = re.sub(r"(?<![\d.,])(\d{1,3})\s*ур\.(?![\w])", num_lvl, text)
    text = re.sub(r"(?<![\w])ур\.\s*(?=\d)", "уровень ", text)
    return re.sub(r"(?<![\w])ур\.(?![\w])", "уровень", text)


# ---------- слова через точки: S.T.A.L.K.E.R., Щ.И.Т., Ф.С.Б. ----------

RU_LETTERS = {"а": "а", "б": "бэ", "в": "вэ", "г": "гэ", "д": "дэ", "е": "е", "ё": "ё", "ж": "жэ", "з": "зэ",
              "и": "и", "й": "и краткое", "к": "ка", "л": "эль", "м": "эм", "н": "эн", "о": "о", "п": "пэ",
              "р": "эр", "с": "эс", "т": "тэ", "у": "у", "ф": "эф", "х": "ха", "ц": "цэ", "ч": "чэ", "ш": "ша",
              "щ": "ща", "ъ": "твёрдый знак", "ы": "ы", "ь": "мягкий знак", "э": "э", "ю": "ю", "я": "я"}
RU_VOWELS_SET = set("аеёиоуыэюя")
# сокращения, которые принято читать не по названиям букв (с точками и без)
ACRONYMS = {"США": "сэ-шэ-а"}
# английское слово по словарю: функция «слово → русскими буквами или None» (ставит server.py, см. en_translit)
EN_WORD = None
EN_LETTER = None


def _readable_ru(w):
    """Можно ли прочитать буквы словом: ЩИТ, ГОСТ, ООН — да; ФСБ, США, СССР — нет."""
    w = w.lower()
    if len(w) < 2 or not any(c in RU_VOWELS_SET for c in w):
        return False
    if w[0] not in RU_VOWELS_SET and w[1] not in RU_VOWELS_SET:
        return False
    run = 0
    for c in w:
        run = 0 if c in RU_VOWELS_SET else run + 1
        if run >= 3:
            return False
    return True


def dotted_words(text):
    """«S.T.A.L.K.E.R.» → «сталкер», «Щ.И.Т.а» → «щита», «Ф.С.Б.» → «эф-эс-бэ». Инициалы не трогаем."""
    pat = re.compile(r"(?<![\w.])((?:[A-Z]\.){1,}[A-Z]|(?:[А-ЯЁ]\.){1,}[А-ЯЁ])(\.)?([а-яё]+)?(?![\w])")

    def repl(m):
        letters = m.group(1).replace(".", "")
        suffix = m.group(3) or ""
        before, after = text[:m.start()], text[m.end():]
        if len(letters) <= 2 and not suffix and (
                re.match(r"\s*[A-ZА-ЯЁ][a-zа-яё]", after) or re.search(r"[A-ZА-ЯЁ][a-zа-яё]+\s*$", before)):
            return m.group(0)  # инициалы: «А.С. Пушкин», «Пушкин А.С.»
        if letters in ("AM", "PM") and re.search(r"\d\s*$", before):
            return m.group(0)  # время: «11 P.M.» — разберёт normalize()
        end = ""
        if letters.isascii():
            word = EN_WORD(letters.lower()) if EN_WORD and len(letters) >= 4 else None
            if word:
                return word + suffix + end
            names = [EN_LETTER(c.lower()) if EN_LETTER else c for c in letters]
            return "-".join(names) + (("-" + suffix) if suffix else "") + end
        if letters in ACRONYMS:
            return ACRONYMS[letters] + (("-" + suffix) if suffix else "")
        if _readable_ru(letters):
            return letters.lower() + suffix + end
        return "-".join(RU_LETTERS.get(c.lower(), c) for c in letters) + (("-" + suffix) if suffix else "") + end
    return pat.sub(repl, text)


# ---------- ранги из ранобэ: S-ранг, ранга A, SSS-класс, A+ ----------

RANK_LETTERS = {"S": "эс", "A": "эй", "B": "би", "C": "си", "D": "ди", "E": "и", "F": "эф",
                # те же буквы кириллицей — авторы часто пишут «С-ранг», «А-ранг»
                "А": "эй", "В": "би", "Б": "би", "С": "си", "Д": "ди", "Е": "и", "Ф": "эф"}
_RANK_TOKEN = r"(?:SSS|SS|S|[ABCDEF]|[АВБСДЕФ])(?:\+{1,3}|-(?![А-Яа-яЁё]))?"
_RANK_WORD = r"(?:ранг|ранговы|класс|тир)[а-яё]*|(?:[Tt]ier|[Rr]ank|[Cc]lass)(?:s)?(?![A-Za-z])"


def rank_words(token):
    """«SSS» → «трипл эс», «A+» → «эй плюс», «S++» → «эс плюс плюс»."""
    m = re.fullmatch(r"(SSS|SS|S|.)(\++|-)?", token)
    base, mod = m.group(1), m.group(2) or ""
    if base == "SSS":
        word = "трипл эс"
    elif base == "SS":
        word = "дабл эс"
    else:
        word = RANK_LETTERS.get(base, base)
    if mod.startswith("+"):
        word += " плюс" * len(mod)
    elif mod == "-":
        word += " минус"
    return word


def ranks(text):
    """Буква ранга рядом со словом «ранг» / «класс»: «S-ранг» → «эс-ранг», «ранга C» → «ранга си»
    (а не римское «сто»), «Ранг: SS» → «ранг: дабл эс». Без такого слова буквы не трогаем."""
    # S-ранг, SSS-ранговый, A+ ранга, С-класс
    def before(m):
        tok, sep = m.group(1), m.group(2)
        if re.match(r"[АВБСДЕФ]", tok) and "-" not in sep:
            return m.group(0)   # кириллица через пробел — это предлог или союз: «В ранге», «С рангом», «А ранг…»
        return f"{rank_words(tok)}{'-' if '-' in sep else ' '}{m.group(3)}"
    text = re.sub(rf"(?<![\w+-])({_RANK_TOKEN})(\s?-\s?|\s)({_RANK_WORD})", before, text)
    # ранга S, ранг A+, Ранг: SS, класса B
    return re.sub(rf"(?<![\w])((?:[Рр]анг|[Кк]ласс|[Тт]ир)[а-яё]*:?\s+|(?:[Tt]ier|[Rr]ank|[Cc]lass):?\s+)({_RANK_TOKEN})(?![\w+])",
                  lambda m: m.group(1) + rank_words(m.group(2)), text)


# ---------- «мин.» и «макс.»: минуты, минимум, министерство — или мины ----------

_MINE_CONTEXT = re.compile(r"(?i)сап[её]р|разминир|обезвре|минн[а-яё]+|\bмин[аыуое]й?\b|миной|растяжк|фугас|"
                           r"противотанков|противопехотн|подорва|взрывател|миноиска|заминир")
_MINISTRY = ("обороны", "финансов", "юстиции", "внутренних", "иностранных", "здравоохранения", "путей", "связи",
             "торговли", "культуры", "образования", "просвещения", "промышленности", "сельского", "транспорта")
_ADJ_END = ("ый", "ий", "ой", "ая", "яя", "ое", "ее", "ые", "ие", "ого", "его", "ому", "ему", "ым", "им", "ую",
            "юю", "ых", "их", "ыми", "ими")


def _min_max(text):
    """«через 1 мин.» → «через одну минуту»; «мин. допустимая норма» → «минимально допустимая норма»;
    «мин. значение» → «минимальное значение»; «мин. обороны» → «министерство обороны»; «мин.: 5» → «минимум: 5».
    Рядом про мины (сапёр, разминирование, минное поле…) — «мин» остаётся мин."""
    if not re.search(r"(?i)(?<![\w])(?:мин|макс)\.?", text):
        return text
    mines = bool(_MINE_CONTEXT.search(text))

    def minutes(m):
        if mines:
            return m.group(0)
        n = int(m.group(1))
        prev = _prev_word(text, m.start())
        if prev in ("через", "за", "на", "спустя"):
            case = "acc"
        elif prev in GEN_PREPS or prev in ("с", "со"):
            case = "gen"
        else:
            case = "nom"
        if case == "gen":
            noun = "минуты" if n % 10 == 1 and n % 100 != 11 else "минут"
        else:
            noun = plural(n, "минуту" if case == "acc" else "минута", "минуты", "минут")
        return f"{cardinal(n, 'f', case)} {noun}"
    text = re.sub(r"(?<![\d.,])(\d{1,4})\s*мин(?:\.|(?![\w]))", minutes, text)

    def word(m):
        base, rest = m.group(1), text[m.end():]
        low = base.lower()
        full = {"мин": ("минимальн", "минимально", "минимум"), "макс": ("максимальн", "максимально", "максимум")}[low]
        cap = (lambda w: w.capitalize()) if base[0].isupper() else (lambda w: w)
        if low == "мин" and mines:
            return m.group(0)
        if low == "макс" and base[0].isupper() and not re.match(r"\s*[:\d]|\s*[а-яё]", rest):
            return m.group(0)   # имя «Макс.» в конце предложения
        nxt = re.match(r"\s*([А-Яа-яЁё]+)(?:\s+([А-Яа-яЁё]+))?", rest)
        if low == "мин" and nxt and nxt.group(1).lower() in _MINISTRY:
            return cap("министерство")
        if nxt:
            w1, w2 = nxt.group(1).lower(), (nxt.group(2) or "").lower()
            noun_ie = w1.endswith(("ние", "тие", "вие", "бие", "дие", "лие", "рие", "сие"))   # значение, развитие
            if w1.endswith(_ADJ_END) and not noun_ie and w2:
                return cap(full[1])               # «мин. допустимая норма» → «минимально допустимая»
            ending = ("ая" if w1.endswith(("а", "я", "ость", "есть", "сть", "знь", "чь", "шь", "щь", "жь", "вь", "бь"))
                      else "ое" if w1.endswith(("о", "е")) else "ые" if w1.endswith(("ы", "и")) else "ый")
            if w1.endswith(_ADJ_END) and not noun_ie:
                return cap(full[2])
            return cap(full[0] + ending)          # «мин. значение» → «минимальное значение»
        return cap(full[2])                       # «мин.: 5», «мин. 5» → «минимум»
    return re.sub(r"(?<![\w.])([Мм]ин|[Мм]акс)\.(?![\w])", word, text)


def abbreviations(text):
    """«т. е.» → «то есть», «стр. 5» → «страница 5», «спс» → «спасибо». Вызывать ДО normalize()."""
    for pat, rep in _ABBR:
        text = re.sub(pat, rep, text)
    text = _level_abbr(text)
    text = _min_max(text)
    text = ranks(text)
    text = ru_names.initials(text)
    text = dotted_words(text)
    text = re.sub(r"(?<![\w])(" + "|".join(ACRONYMS) + r")(?![\w])", lambda m: ACRONYMS[m.group(1)], text)
    return _colloquial(text)


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
        # Глава IV, в главе IV, Том II, Уровень IV — порядковое в роде и падеже слова
        an = ru_nouns.analyses(prev) if prev else []
        if an:
            prep = _prev_two(t, m.start() - len(re.search(r"[\w-]*[\s\u00a0]*$", t[:m.start()]).group(0)))[1]
            a = ru_nouns.pick(an, prep)
            if a:
                return ordinal(n, a[1], a[3])
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


def _prev_two(text, pos):
    """Два слова перед позицией: (предыдущее-перед-ним, ближайшее), в нижнем регистре."""
    m = re.search(r"(?:([\w-]+)[\s\u00a0]+)?([\w-]+)[\s\u00a0]*$", text[:pos])
    if not m:
        return "", ""
    return (m.group(1) or "").lower(), m.group(2).lower()


def _number_by_noun(t, m, n, prev, nxt):
    """Число рядом со словом из словаря (ru_nouns): «Глава 4», «на 3 этаже», «2 главы». None — не наш случай."""
    # слово с заглавной после числа — обычно начало нового (заголовок, ячейка): «1 000 000 Уровень навыка»
    an_next = ru_nouns.analyses(nxt) if nxt and not re.match(r"[\s\u00a0]*[А-ЯЁ]", t[m.end():]) else []
    if an_next:
        for a in an_next:
            if ru_nouns.count_agrees(n, a[3], a[4]):
                case = a[3]
                if case == "nom":
                    pass
                elif case == "acc":
                    pass   # «за одну книгу», «купил двадцать одну книгу»
                elif case == "gen":
                    case = "gen" if prev in GEN_PREPS or prev in ("с", "со") else "nom"
                return cardinal(n, a[1] if a[1] in "mfn" else "m", case)
        a = ru_nouns.pick(an_next, prev)
        if a:
            return ordinal(n, a[1], a[3])
        return None
    an_prev = ru_nouns.analyses(prev) if prev else []
    if an_prev and not re.match(r"\s*[-–—]\s*\d", t[m.end():]):
        before, _ = _prev_two(t, m.start() - len(re.search(r"[\w-]+[\s\u00a0]*$", t[:m.start()]).group(0)))
        a = ru_nouns.pick(an_prev, _)
        if a:
            return ordinal(n, a[1], a[3]) if a[2] == "ord" else cardinal(n)
    return None


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
        by_noun = _number_by_noun(t, m, n, prev, nxt)
        if by_noun:
            return by_noun
        gender = "m"
        last = n % 10 if not 11 <= n % 100 <= 14 else 0
        if nxt and nxt not in ru_nouns.PREP_CASE and nxt not in ("и", "или", "а", "но", "что", "как"):
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
