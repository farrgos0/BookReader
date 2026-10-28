"""
Аудиокниги в каталоге сервера (шаг 8): MP3, M4B/M4A/MP4, OGG/Opus, FLAC, WAV.

Клиент слушает книгу прямо из файла, поэтому книга из нескольких частей отдаётся одним ZIP **без сжатия**
(части лежат в нём подряд, их можно проигрывать по смещению). Архив, где аудио сжато, и RAR/7z с аудио
перекладываются в такой ZIP один раз и хранятся в папке данных сервера (catalog-built), как собранные книги.

Для списка каталога достаточно названия и автора из тегов: ID3v2/ID3v1 (MP3), ilst (MP4/M4B),
Vorbis comment (OGG, FLAC). Полный разбор (длительность, главы, обложка) делает клиент (web/audiometa.js).
"""
import os
import re
import struct
import zipfile

AUDIO_EXTS = (".mp3", ".m4b", ".m4a", ".mp4", ".ogg", ".oga", ".opus", ".flac", ".wav")
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")
BOOK_EXTS = (".fb2", ".epub", ".docx", ".odt", ".rtf", ".mobi", ".azw", ".azw3", ".prc", ".html", ".htm", ".xhtml", ".md")
LIMIT = 4000 * 1024 * 1024   # ZIP без ZIP64: до ~4 ГБ
BUILD = 1                    # версия перекладки: при изменении сервер перекладывает аудиокниги заново


def natural_key(s):
    return [int(t) if t.isdigit() else t.casefold() for t in re.split(r"(\d+)", s)]


def is_audio(name):
    return name.lower().endswith(AUDIO_EXTS)


def _skip(name):
    parts = name.replace("\\", "/").split("/")
    return any(p.startswith(".") or p == "__MACOSX" for p in parts)


def label(name):
    """Подпись формата для каталога: «MP3 · аудио»."""
    ext = os.path.splitext(name)[1].lower().strip(".")
    return (ext.upper() or "АУДИО") + " · аудио"


# ---------- теги (только название / автор / альбом) ----------

def _text(raw, enc):
    if enc == 1:
        s = raw.decode("utf-16", "replace")
    elif enc == 2:
        s = raw.decode("utf-16-be", "replace")
    elif enc == 3:
        s = raw.decode("utf-8", "replace")
    else:
        try:
            s = raw.decode("utf-8")
        except UnicodeDecodeError:
            s = raw.decode("cp1251", "replace")   # «latin1» в русских MP3 почти всегда cp1251
    return s.split("\x00")[0].strip()


def _synchsafe(b):
    return (b[0] << 21) | (b[1] << 14) | (b[2] << 7) | b[3]


def _id3(f, out):
    head = f.read(10)
    if len(head) < 10 or head[:3] != b"ID3":
        return
    ver, size = head[3], _synchsafe(head[6:10])
    tag = f.read(min(size, 512 * 1024))   # картинки дальше не нужны
    keys = {"TIT2": "title", "TT2": "title", "TPE1": "artist", "TP1": "artist", "TALB": "album", "TAL": "album",
            "TPE2": "albumartist", "TP2": "albumartist"}
    p, hl, il = 0, (6 if ver == 2 else 10), (3 if ver == 2 else 4)
    while p + hl <= len(tag):
        fid = tag[p:p + il].decode("latin-1")
        if not re.fullmatch(r"[A-Z0-9]{3,4}", fid):
            break
        if ver == 2:
            n = int.from_bytes(tag[p + 3:p + 6], "big")
        elif ver == 4:
            n = _synchsafe(tag[p + 4:p + 8])
        else:
            n = int.from_bytes(tag[p + 4:p + 8], "big")
        body = tag[p + hl:p + hl + n]
        p += hl + n
        if fid in keys and body and keys[fid] not in out:
            out[keys[fid]] = _text(body[1:], body[0])


def _id3v1(f, size, out):
    if size < 128:
        return
    f.seek(size - 128)
    t = f.read(128)
    if t[:3] != b"TAG":
        return
    for key, a, b in (("title", 3, 33), ("artist", 33, 63), ("album", 63, 93)):
        if key not in out:
            v = _text(t[a:b], 0)
            if v:
                out[key] = v


def _mp4(f, size, out):
    """Атомы верхнего уровня по заголовкам; moov — целиком (теги в moov/udta/meta/ilst)."""
    p, moov = 0, None
    while p + 8 <= size:
        f.seek(p)
        h = f.read(16)
        n, typ = struct.unpack(">I4s", h[:8])
        hl = 8
        if n == 1:
            n, hl = struct.unpack(">Q", h[8:16])[0], 16
        elif n == 0:
            n = size - p
        if n < hl:
            return
        if typ == b"moov":
            if n > 64 * 1024 * 1024:
                return
            moov = f.read(n - hl) if hl == 16 else h[8:] + f.read(n - 16)
            break
        p += n
    if not moov:
        return
    keys = {b"\xa9nam": "title", b"\xa9ART": "artist", b"aART": "albumartist", b"\xa9alb": "album"}

    def walk(b, a, e, meta=False):
        q = a + (4 if meta else 0)
        while q + 8 <= e:
            n, typ = struct.unpack(">I4s", b[q:q + 8])
            if n < 8 or q + n > e:
                return
            if typ in (b"udta", b"ilst"):
                walk(b, q + 8, q + n)
            elif typ == b"meta":
                walk(b, q + 8, q + n, True)
            elif typ in keys and keys[typ] not in out:
                d = b.find(b"data", q + 8, q + n)
                if d > 0:
                    out[keys[typ]] = b[d + 12:q + n].decode("utf-8", "replace").strip()
            q += n
    walk(moov, 0, len(moov))


def _vorbis(b, out):
    try:
        vlen = struct.unpack("<I", b[:4])[0]
        p = 4 + vlen
        cnt = struct.unpack("<I", b[p:p + 4])[0]
        p += 4
        for _ in range(min(cnt, 10000)):
            n = struct.unpack("<I", b[p:p + 4])[0]
            s = b[p + 4:p + 4 + n].decode("utf-8", "replace")
            p += 4 + n
            k, _, v = s.partition("=")
            key = {"TITLE": "title", "ARTIST": "artist", "ALBUM": "album", "ALBUMARTIST": "albumartist"}.get(k.upper())
            if key and key not in out and v.strip():
                out[key] = v.strip()
    except (struct.error, IndexError):
        pass


def _ogg(f, out):
    data = f.read(256 * 1024)
    for sig, skip in ((b"OpusTags", 8), (b"\x03vorbis", 7)):
        i = data.find(sig)
        if i >= 0:
            _vorbis(data[i + skip:], out)
            return


def _flac(f, out):
    if f.read(4) != b"fLaC":
        return
    while True:
        h = f.read(4)
        if len(h) < 4:
            return
        last, typ, n = h[0] & 0x80, h[0] & 0x7F, int.from_bytes(h[1:4], "big")
        if typ == 4:
            _vorbis(f.read(n), out)
            return
        f.seek(n, 1)
        if last:
            return


def tags(path):
    """{'title', 'artist', 'album', 'albumartist'} — что нашлось; ошибки молча (файл всё равно попадёт в каталог)."""
    out = {}
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            head = f.read(12)
            f.seek(0)
            if head[4:8] == b"ftyp":
                _mp4(f, size, out)
            elif head[:4] == b"OggS":
                _ogg(f, out)
            elif head[:4] == b"fLaC":
                _flac(f, out)
            elif head[:3] == b"ID3":
                _id3(f, out)
                if not out:
                    _id3v1(f, size, out)
            else:
                _id3v1(f, size, out)
    except (OSError, ValueError, struct.error):
        pass
    return out


def book_info(tag, fallback):
    """Название и авторы для каталога: альбом (книга из частей) или название, иначе имя файла."""
    title = tag.get("album") or tag.get("title") or fallback
    a = tag.get("artist") or tag.get("albumartist") or ""
    authors = [x.strip() for x in re.split(r"\s*[;/]\s*", a) if x.strip()][:5]
    return title, authors


# ---------- ZIP с аудио ----------

def zip_kind(path):
    """'audio' — в ZIP аудио (и нет файлов обычных книг), 'audio-repack' — то же, но аудио сжато; None — не аудио."""
    try:
        with zipfile.ZipFile(path) as z:
            infos = [i for i in z.infolist() if not i.is_dir() and not _skip(i.filename)]
    except (OSError, zipfile.BadZipFile):
        return None
    names = [i.filename.lower() for i in infos]
    if not any(n.endswith(AUDIO_EXTS) for n in names) or any(n.endswith(BOOK_EXTS) for n in names):
        return None
    deflated = any(i.compress_type != zipfile.ZIP_STORED for i in infos if i.filename.lower().endswith(AUDIO_EXTS))
    return "audio-repack" if deflated else "audio"


def zip_tags(path):
    """Теги первой части (по порядку имён) аудио-ZIP. Файл части читается из архива во временный буфер — только начало."""
    import io
    import tempfile
    with zipfile.ZipFile(path) as z:
        audio = sorted((i for i in z.infolist() if is_audio(i.filename) and not _skip(i.filename)), key=lambda i: natural_key(i.filename))
        if not audio:
            return {}, []
        first = audio[0]
        # MP4 хранит теги в конце — для него нужен весь файл; остальным хватит начала
        need_all = first.filename.lower().endswith((".m4b", ".m4a", ".mp4"))
        with z.open(first) as src, tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(first.filename)[1]) as tmp:
            if need_all:
                while True:
                    chunk = src.read(1 << 20)
                    if not chunk:
                        break
                    tmp.write(chunk)
            else:
                tmp.write(src.read(1024 * 1024))
            name = tmp.name
    try:
        return tags(name), [i.filename for i in audio]
    finally:
        try:
            os.remove(name)
        except OSError:
            pass


def pack(out_path, items):
    """items: [(имя в архиве, путь к файлу | (путь к ZIP, имя записи))] → ZIP без сжатия, части по порядку items."""
    total = 0
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_STORED, allowZip64=False) as z:
        for arcname, src in items:
            if isinstance(src, tuple):
                with zipfile.ZipFile(src[0]) as zin, zin.open(src[1]) as fin:
                    info = zin.getinfo(src[1])
                    total += info.file_size
                    if total > LIMIT:
                        raise RuntimeError("книга больше 4 ГБ")
                    zi = zipfile.ZipInfo(arcname, date_time=info.date_time)
                    zi.compress_type = zipfile.ZIP_STORED
                    with z.open(zi, "w", force_zip64=False) as fout:
                        while True:
                            chunk = fin.read(1 << 20)
                            if not chunk:
                                break
                            fout.write(chunk)
            else:
                total += os.path.getsize(src)
                if total > LIMIT:
                    raise RuntimeError("книга больше 4 ГБ")
                z.write(src, arcname, zipfile.ZIP_STORED)


def repack_zip(path, out_path):
    """Аудио-ZIP со сжатием → ZIP без сжатия (аудио и картинки, по порядку имён). True — готово."""
    with zipfile.ZipFile(path) as z:
        names = [i.filename for i in z.infolist() if not i.is_dir() and not _skip(i.filename)
                 and i.filename.lower().endswith(AUDIO_EXTS + IMAGE_EXTS)]
    names.sort(key=natural_key)
    pack(out_path, [(n, (path, n)) for n in names])
    return True


def pack_dir(root, out_path):
    """Папка (с подпапками) с аудио → ZIP без сжатия. Имена — относительные пути, по порядку «по-человечески»."""
    files = []
    for dp, dirs, fs in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in fs:
            rel = os.path.relpath(os.path.join(dp, f), root).replace("\\", "/")
            if not _skip(rel) and rel.lower().endswith(AUDIO_EXTS + IMAGE_EXTS):
                files.append(rel)
    files.sort(key=natural_key)
    pack(out_path, [(rel, os.path.join(root, rel)) for rel in files])
    return True


def pack_archive(path, out_path):
    """RAR/7z → ZIP без сжатия, если в архиве есть аудио. False — аудио нет."""
    import shutil
    import subprocess
    import tempfile
    import assemble
    tools = assemble._tools()
    if not tools:
        raise RuntimeError("нечем распаковать " + os.path.splitext(path)[1].upper().strip(".") + " (нужен tar.exe или 7-Zip)")
    tmp = tempfile.mkdtemp(prefix="bookreader-audio-")
    try:
        last = None
        for kind, exe in tools:
            cmd = [exe, "-xf", path, "-C", tmp] if kind == "tar" else [exe, "x", "-y", "-o" + tmp, path]
            try:
                r = subprocess.run(cmd, capture_output=True, timeout=1800, creationflags=assemble.NO_WINDOW)
            except (OSError, subprocess.TimeoutExpired) as e:
                last = str(e)
                continue
            if r.returncode == 0:
                break
            last = assemble._decode(r.stderr)[-300:].strip()
            shutil.rmtree(tmp, ignore_errors=True)
            os.makedirs(tmp, exist_ok=True)
        else:
            raise RuntimeError("архив не распаковался: " + (last or "неизвестная ошибка"))
        has = any(is_audio(f) for _, _, fs in os.walk(tmp) for f in fs)
        if not has:
            return False
        pack_dir(tmp, out_path)
        return True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
