"""
Книга из архива с множеством файлов (по файлу на главу) — для каталога сервера.

В каталог кладут, например, «Том_1.rar» со 100 файлами «Книга_Глава_1._Название.docx» … Клиенту нужна
одна готовая книга, поэтому сервер сам распаковывает архив, расставляет файлы по порядку имён
«по-человечески» (2 раньше 10) и собирает из них один FB2: каждый файл — глава.

Поддерживается: архивы ZIP (встроенно), RAR и 7z (через tar.exe — он есть в Windows 10 1803+ и 11,
или через 7-Zip, если установлен). Внутри: FB2, DOCX, ODT, TXT, MD, HTML/XHTML, EPUB.

Название главы: заголовок в начале файла (первый заголовок FB2 или первая строка вида «Глава 5…»),
иначе имя файла без общей для всех файлов части («Книга_Глава_1._Название» → «Глава 1. Название»).
Название книги: общая часть имён файлов (+ «том N» из имени архива), иначе имя архива.
"""
import html
import os
import re
import shutil
import subprocess
import tempfile
import uuid
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter

BUILD = 2   # версия сборки: при изменении сервер пересобирает книги каталога из архивов

PART_EXTS = (".fb2", ".docx", ".odt", ".txt", ".md", ".html", ".htm", ".xhtml", ".epub")
ARCHIVE_EXTS = (".zip", ".rar", ".7z")
NO_WINDOW = 0x08000000 if os.name == "nt" else 0

CHAP_RE = re.compile(r"^(глава|часть|книга|том|пролог|эпилог|интерлюдия|предисловие|послесловие|экстра|"
                     r"chapter|part|book|prologue|epilogue|interlude)(?=$|[\s.,:;!?№\d_—–-])", re.I)


def natural_key(s):
    return [int(t) if t.isdigit() else t.casefold() for t in re.split(r"(\d+)", s)]


def _ext(name):
    low = name.lower()
    return next((e for e in PART_EXTS if low.endswith(e)), None)


def _skip(name):
    parts = name.replace("\\", "/").split("/")
    return any(p.startswith(".") or p == "__MACOSX" for p in parts)


def pick_parts(names):
    """Файлы книги в архиве: одного формата (есть FB2 — только они, иначе самый частый), по порядку имён."""
    books = [n for n in names if _ext(n) and not _skip(n) and not n.endswith("/")]
    if not books:
        return []
    cnt = Counter(_ext(n) for n in books)
    main = ".fb2" if ".fb2" in cnt else cnt.most_common(1)[0][0]
    return sorted((n for n in books if _ext(n) == main), key=natural_key)


# ---------- архивы ----------

def _tools():
    """Чем распаковать RAR/7z: tar.exe (libarchive, есть в Windows) или 7-Zip."""
    out = []
    win_tar = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "System32", "tar.exe")
    if os.name == "nt" and os.path.isfile(win_tar):
        out.append(("tar", win_tar))
    for p in (shutil.which("7z"), shutil.which("7za"),
              os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "7-Zip", "7z.exe"),
              os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "7-Zip", "7z.exe")):
        if p and os.path.isfile(p):
            out.append(("7z", p))
    if os.name != "nt":
        for t in (shutil.which("bsdtar"),):
            if t:
                out.append(("tar", t))
    return out


def read_archive(path):
    """{имя файла: байты} — все книжные файлы архива. RAR/7z распаковываются во временную папку целиком:
    имена берём из самой файловой системы (вывод tar.exe в консоль бывает в другой кодировке)."""
    if path.lower().endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            return {_zip_name(i): z.read(i) for i in z.infolist()
                    if not i.is_dir() and _ext(_zip_name(i)) and not _skip(_zip_name(i))}
    tools = _tools()
    if not tools:
        raise RuntimeError("нечем распаковать " + os.path.splitext(path)[1].upper().strip(".") +
                           " (нужен tar.exe из Windows 10/11 или 7-Zip)")
    tmp = tempfile.mkdtemp(prefix="bookreader-")
    try:
        last = None
        for kind, exe in tools:
            cmd = [exe, "-xf", path, "-C", tmp] if kind == "tar" else [exe, "x", "-y", "-o" + tmp, path]
            try:
                r = subprocess.run(cmd, capture_output=True, timeout=600, creationflags=NO_WINDOW)
            except (OSError, subprocess.TimeoutExpired) as e:
                last = str(e)
                continue
            if r.returncode == 0:
                break
            last = _decode(r.stderr)[-300:].strip()
            shutil.rmtree(tmp, ignore_errors=True)
            os.makedirs(tmp, exist_ok=True)
        else:
            raise RuntimeError("архив не распаковался: " + (last or "неизвестная ошибка"))
        out = {}
        for dp, _, files in os.walk(tmp):
            for f in files:
                full = os.path.join(dp, f)
                rel = os.path.relpath(full, tmp).replace("\\", "/")
                if _ext(rel) and not _skip(rel):
                    with open(full, "rb") as fh:
                        out[rel] = fh.read()
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _decode(b):
    for enc in ("utf-8", "cp866", "cp1251"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", "replace")


def _zip_name(info):
    # имена без флага UTF-8 — часто cp866 (архиваторы Windows)
    if info.flag_bits & 0x800:
        return info.filename
    try:
        return info.filename.encode("cp437").decode("cp866")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return info.filename


# ---------- разбор частей → (заголовок или None, [элементы FB2 как строки]) ----------

def _esc(s):
    return html.escape(s, quote=False)


def _norm(s):
    return re.sub(r"\s+", " ", s or "").strip()


def _p(text):
    return f"<p>{_esc(text)}</p>"


def _xml_text_of(b):
    m = re.search(rb'encoding=["\']([\w\-]+)', b[:200])
    enc = m.group(1).decode("ascii", "ignore") if m else "utf-8"
    try:
        return b.decode(enc)
    except (LookupError, UnicodeDecodeError):
        return b.decode("utf-8", "replace")


def _strip_ns(el):
    for e in el.iter():
        if isinstance(e.tag, str) and "}" in e.tag:
            e.tag = e.tag.split("}", 1)[1]
        for k in list(e.attrib):
            if "}" in k:
                e.attrib[k.split("}", 1)[1]] = e.attrib.pop(k)
    return el


class _Lenient(__import__("html.parser").parser.HTMLParser):
    """Терпимый разбор битого FB2 (частая ошибка конвертеров: «<table><p><tr>…</td></p>»):
    лишние закрывающие теги пропускаются, незакрытые закрываются сами."""
    VOID = {"image", "empty-line", "br", "img"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tb, self.stack = ET.TreeBuilder(), []

    def handle_starttag(self, tag, attrs):
        tag = tag.split(":")[-1] if tag.startswith(("l:", "xlink:")) else tag
        self.tb.start(tag, {k.split(":")[-1]: v or "" for k, v in attrs})
        if tag in self.VOID:
            self.tb.end(tag)
        else:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.tb.start(tag, {k.split(":")[-1]: v or "" for k, v in attrs})
        self.tb.end(tag)

    def handle_endtag(self, tag):
        if tag in self.stack:
            while self.stack:
                t = self.stack.pop()
                self.tb.end(t)
                if t == tag:
                    break

    def handle_data(self, data):
        if self.stack:
            self.tb.data(data)

    def result(self):
        while self.stack:
            self.tb.end(self.stack.pop())
        return self.tb.close()


def _parse_lenient(text):
    try:
        return ET.fromstring(text)
    except ET.ParseError:
        pass
    # 1) типичная поломка: абзацы вокруг строк и ячеек таблицы
    fixed = re.sub(r"(?is)<table\b.*?</table>", lambda m: re.sub(r"(?i)</?p\s*>", "", m.group(0)), text)
    try:
        return ET.fromstring(fixed)
    except ET.ParseError:
        pass
    # 2) всё остальное — терпимым разбором (теги FB2 в нижнем регистре, как и положено)
    p = _Lenient()
    p.feed(re.sub(r"(?i)<(/?)(title|style|script)\b", r"<\1fb-\2", fixed))   # в HTML их содержимое — сырой текст
    p.close()
    root = p.result()
    for e in root.iter():
        if e.tag.startswith("fb-"):
            e.tag = e.tag[3:]
    if root.tag != "fictionbook":
        raise ValueError("не FB2")
    root.tag = "FictionBook"
    return root


def _fb2_part(b):
    """FB2: блоки первого тела (без сносок и картинок). Заголовок — у первого раздела, если есть."""
    text = _xml_text_of(b)
    text = re.sub(r"^<\?xml[^>]*\?>", "", text.lstrip("\ufeff").lstrip())
    root = _strip_ns(_parse_lenient(text))
    meta = root.find("description/title-info")
    book_title = _norm(meta.findtext("book-title")) if meta is not None else ""
    authors = []
    if meta is not None:
        for a in meta.findall("author"):
            name = " ".join(x for x in (_norm(a.findtext("first-name")), _norm(a.findtext("middle-name")),
                                        _norm(a.findtext("last-name"))) if x) or _norm(a.findtext("nickname"))
            if name:
                authors.append(name)
    body = next((bd for bd in root.findall("body") if bd.get("name") not in ("notes", "comments")), None)
    if body is None:
        return None, [], book_title, authors
    for parent in body.iter():   # картинки без двоичных данных из файла не показать — убираем
        for img in [c for c in parent if c.tag == "image"]:
            parent.remove(img)
    kids = [c for c in body if c.tag not in ("title", "epigraph")]
    title = None
    blocks = []
    sections = [c for c in kids if c.tag == "section"]
    if len(sections) == 1 and len(kids) == 1:
        sec = sections[0]
        t = sec.find("title")
        if t is not None:
            title = _norm(" ".join(t.itertext()))
            sec.remove(t)
        blocks = [ET.tostring(c, encoding="unicode") for c in sec]
    else:
        blocks = [ET.tostring(c, encoding="unicode") for c in kids]
    if title is None and blocks:
        first = kids[0] if kids and kids[0].tag != "section" else None
        if first is not None and first.tag == "p" and CHAP_RE.match(_norm("".join(first.itertext()))):
            title = _norm("".join(first.itertext()))
            blocks = blocks[1:]
    return title, blocks, book_title, authors


W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _docx_part(b):
    with zipfile.ZipFile(__import__("io").BytesIO(b)) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    paras = []
    for p in root.iter(W + "p"):
        t = "".join(x.text or "" for x in p.iter(W + "t"))
        t = _norm(t)
        if t:
            st = p.find(f"{W}pPr/{W}pStyle")
            paras.append((t, (st.get(W + "val") if st is not None else "") or ""))
    return _paras_part(paras)


def _odt_part(b):
    with zipfile.ZipFile(__import__("io").BytesIO(b)) as z:
        root = _strip_ns(ET.fromstring(z.read("content.xml")))
    paras = []
    for el in root.iter():
        if el.tag in ("p", "h"):
            t = _norm("".join(el.itertext()))
            if t:
                paras.append((t, "heading" if el.tag == "h" else ""))
    return _paras_part(paras)


def _paras_part(paras):
    """Абзацы с именами стилей: первый — заголовок главы, если это стиль заголовка или строка «Глава …»."""
    title = None
    if paras:
        t, st = paras[0]
        if len(t) <= 150 and (re.search(r"heading|title|заголов", st, re.I) or CHAP_RE.match(t)):
            title, paras = t, paras[1:]
    return title, [_p(t) for t, _ in paras], "", []


def _text_part(b):
    text = b.decode("utf-8-sig", "replace") if not b.startswith(b"\xff\xfe") else b.decode("utf-16", "replace")
    if "\ufffd" in text:
        text = b.decode("cp1251", "replace")
    lines = [_norm(ln.lstrip("#")) for ln in text.splitlines()]
    return _paras_part([(ln, "") for ln in lines if ln])


def _html_part(b):
    text = b.decode("utf-8", "replace")
    text = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", "", text)
    paras = []
    for m in re.finditer(r"(?is)<(p|h[1-6]|div|li|blockquote)\b[^>]*>(.*?)</\1>", text):
        t = _norm(html.unescape(re.sub(r"<[^>]+>", " ", m.group(2))))
        if t:
            paras.append((t, "heading" if m.group(1).lower().startswith("h") else ""))
    return _paras_part(paras)


def _epub_part(b):
    with zipfile.ZipFile(__import__("io").BytesIO(b)) as z:
        cont = z.read("META-INF/container.xml").decode("utf-8", "replace")
        opf_path = re.search(r'full-path="([^"]+)"', cont).group(1)
        opf = z.read(opf_path).decode("utf-8", "replace")
        base = os.path.dirname(opf_path)
        items = dict(re.findall(r'<item\b[^>]*\bid="([^"]+)"[^>]*\bhref="([^"]+)"', opf))
        items.update({k: v for v, k in re.findall(r'<item\b[^>]*\bhref="([^"]+)"[^>]*\bid="([^"]+)"', opf)})
        paras = []
        for idref in re.findall(r'<itemref\b[^>]*\bidref="([^"]+)"', opf):
            href = items.get(idref)
            if not href:
                continue
            try:
                part = z.read(os.path.normpath(os.path.join(base, href)).replace("\\", "/"))
            except KeyError:
                continue
            _, blocks, _, _ = _html_part(part)
            paras.extend((html.unescape(re.sub(r"<[^>]+>", "", x)), "") for x in blocks)
    return _paras_part(paras)


PARSERS = {".fb2": _fb2_part, ".docx": _docx_part, ".odt": _odt_part, ".txt": _text_part, ".md": _text_part,
           ".html": _html_part, ".htm": _html_part, ".xhtml": _html_part, ".epub": _epub_part}


# ---------- имена ----------

def _clean(name):
    base = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", name.replace("\\", "/").split("/")[-1])
    return _norm(base.replace("_", " "))


def _common_prefix(names):
    """Общее начало имён (до слова «Глава» / номера) — у большинства файлов. «Книга Глава 1» → «Книга »."""
    cands = []
    for n in names:
        m = re.search(r"(?i)\b(глава|часть|chapter|part|том)\b|\d", n)
        cands.append(n[: m.start()] if m else "")
    pre, k = Counter(cands).most_common(1)[0]
    return pre if pre.strip() and k >= max(2, len(names) // 2) else ""


def chapter_names(names):
    """Имена файлов → названия глав без общей части."""
    clean = [_clean(n) for n in names]
    pre = _common_prefix(clean)
    out = []
    for c in clean:
        t = c[len(pre):] if pre and c.startswith(pre) else c
        t = t.strip(" .-—–") or c
        if re.fullmatch(r"\d+|[IVXLC]+", t):   # остался только номер — «Глава 7»
            t = "Глава " + t
        out.append(t)
    return out, pre.strip(" .-—–")


def book_title(archive_name, prefix, fb2_titles):
    base = _clean(archive_name)
    vol = re.search(r"(?i)(?:том|tom|vol(?:ume)?|книга|kniga|book)[\s.№#-]*(\d+)", base)
    if len(set(fb2_titles)) == 1 and fb2_titles[0]:
        t = fb2_titles[0]
    elif prefix:
        t = prefix
    else:
        return base
    if vol and not re.search(r"(?i)том\s*" + vol.group(1) + r"\b", t):
        t += f". Том {vol.group(1)}"
    return t


# ---------- сборка ----------

def assemble(path, data=None):
    """Архив → (байты FB2, сведения {title, authors, chapters, skipped}). None — в архиве одна книга (собирать нечего).
    data — уже прочитанное содержимое архива (read_archive)."""
    if data is None:
        data = read_archive(path)
    names = pick_parts(list(data))
    if len(names) < 2:
        return None
    fallback, prefix = chapter_names(names)
    sections, fb2_titles, authors, skipped = [], [], [], 0
    for n, fb in zip(names, fallback):
        b = data.get(n)
        if b is None:
            skipped += 1
            continue
        try:
            title, blocks, bt, au = PARSERS[_ext(n)](b)
        except Exception:  # noqa: BLE001 — битый файл: пропускаем, остальные главы остаются
            skipped += 1
            continue
        if bt:
            fb2_titles.append(bt)
        if au and not authors:
            authors = au
        sections.append((title or fb, blocks))
    if not sections:
        raise RuntimeError("в архиве нет файлов, которые получилось прочитать")
    title = book_title(os.path.basename(path), prefix, fb2_titles)
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0" xmlns:l="http://www.w3.org/1999/xlink">',
           "<description><title-info><genre>prose</genre>"]
    for a in authors:
        out.append(f"<author><nickname>{_esc(a)}</nickname></author>")
    out.append(f"<book-title>{_esc(title)}</book-title><lang>ru</lang></title-info>")
    out.append(f"<document-info><program-used>BookReader: собрано из {len(sections)} файлов «{_esc(os.path.basename(path))}»"
               f"</program-used><id>{uuid.uuid5(uuid.NAMESPACE_URL, os.path.basename(path))}</id><version>1.0</version>"
               "</document-info></description><body>")
    for t, blocks in sections:
        out.append(f"<section><title><p>{_esc(t)}</p></title>")
        out.extend(blocks or ["<empty-line/>"])
        out.append("</section>")
    out.append("</body></FictionBook>")
    xml = "\n".join(out)
    return xml.encode("utf-8"), {"title": title, "authors": authors, "chapters": len(sections), "skipped": skipped}
