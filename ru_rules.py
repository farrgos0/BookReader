"""
Личные правила произношения. Хранятся на устройстве (браузер, приложение) и приходят с каждым запросом озвучки:

    "rules": [{"w": "гг", "c": false, "s": "главный герой"}, {"w": "Селен", "c": true, "s": "Сел+ен"}],
    "opts":  {"date_order": "dmy"}

  w — слово или сокращение как в книге (любые символы: «гг», «OK», «т.е.»);
  c — учитывать регистр;
  s — как звучит: только русские буквы, «ё», «+» перед ударной гласной, пробел и дефис.

Правила применяются ДО встроенного нормализатора и важнее его. Слова замены помечаются (MARK): их не трогает
нормализатор чисел, а слова с «+» — и расстановка ударений (ru_yo.py): звучат ровно как записано.
Слову без «+» ударение ставит Silero — без ударения синтез звучит плохо; буквы при этом как в правиле:
«е» не станет «ё», «ё» не станет «е».
На диске сервера правила не хранятся.
"""
import hashlib
import json
import re
from collections import OrderedDict

MARK = ""            # перед каждым словом замены: «не трогать»
MAX_RULES = 2000
VOWELS = "аеёиоуыэюяАЕЁИОУЫЭЮЯ"
SAY_RE = re.compile(r"^[А-Яа-яЁё+\- ]+$")
OPTIONS = {"date_order": ("dmy", "mdy")}


class RuleError(ValueError):
    pass


def check_say(s):
    """Ошибка в поле «как звучит» (текстом) или None."""
    s = (s or "").strip()
    if not s:
        return "пусто"
    if re.search(r"\d", s):
        return "цифры не принимаются — напишите словами"
    if re.search(r"[A-Za-z]", s):
        return "латиница не принимается — напишите русскими буквами"
    if not SAY_RE.match(s):
        return "можно только русские буквы, «ё», «+», пробел и дефис"
    if "++" in s:
        return "два «+» подряд"
    if re.search(rf"\+(?![{VOWELS}])", s):
        return "«+» ставится перед ударной гласной"
    if not re.search(r"[А-Яа-яЁё]", s):
        return "нет букв"
    return None


def _pattern(word, case):
    w = re.escape(word)
    w = re.sub(r"[её]", "[её]", w)
    w = re.sub(r"[ЕЁ]", "[ЕЁ]", w)
    if not case:
        w = re.sub(r"\[её\]|\[ЕЁ\]", "[еЕёЁ]", w)
    left = r"(?<![\w])" if re.match(r"\w", word) else ""
    right = r"(?![\w])" if re.search(r"\w$", word) else ""
    return re.compile(left + w + right, 0 if case else re.IGNORECASE)


class Pron:
    """Набор правил одного запроса (уже отфильтрованный устройством под книгу) и личные настройки."""

    def __init__(self, rules=(), opts=None):
        clean = []
        for r in rules or ():
            if not isinstance(r, dict):
                continue
            w = str(r.get("w") or "").strip()
            s = str(r.get("s") or "").strip()
            if not w or len(w) > 60 or len(s) > 200 or check_say(s):
                continue   # ошибочное правило пропускаем, остальные работают
            clean.append((w, bool(r.get("c")), " ".join(s.split())))
        clean.sort(key=lambda r: -len(r[0]))   # длинные раньше: «т.е.» раньше «т»
        self.rules = clean[:MAX_RULES]
        self.opts = {}
        for k, allowed in OPTIONS.items():
            v = (opts or {}).get(k)
            if v in allowed and v != allowed[0]:
                self.opts[k] = v
        canon = json.dumps([self.rules, sorted(self.opts.items())], ensure_ascii=False)
        self.key = hashlib.sha1(canon.encode("utf-8")).hexdigest()[:12] if (self.rules or self.opts) else ""
        self._compiled = [(_pattern(w, c), s) for w, c, s in self.rules]

    def apply(self, text):
        for rx, say in self._compiled:
            marked = " ".join(MARK + part for part in say.split(" "))
            text = rx.sub(lambda m: marked, text)
        return text


EMPTY = Pron()
_cache = OrderedDict()


def from_request(req):
    """Правила и настройки из JSON запроса озвучки → Pron (с кэшем скомпилированных наборов)."""
    rules, opts = req.get("rules"), req.get("opts")
    if not rules and not opts:
        return EMPTY
    if rules is not None and not isinstance(rules, list):
        raise RuleError("rules — список правил")
    if opts is not None and not isinstance(opts, dict):
        raise RuleError("opts — словарь настроек")
    raw = json.dumps([rules, opts], ensure_ascii=False, sort_keys=True)
    p = _cache.get(raw)
    if p is None:
        p = Pron(rules, opts)
        _cache[raw] = p
        while len(_cache) > 64:
            _cache.popitem(last=False)
    else:
        _cache.move_to_end(raw)
    return p


def strip(text):
    return text.replace(MARK, "")
