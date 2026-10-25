"""
BookReader — локальный сервер озвучки на Silero TTS.

Запуск:  python server.py               (модель Silero; видеокарта, если доступна, иначе процессор)
         python server.py --device cpu  (только процессор)
         python server.py --fake     (без модели, гудки — для проверки интерфейса)

Открыть в браузере: http://127.0.0.1:8765

Настройки (адрес, порт, имя, папка каталога…) — в файле server.json в папке данных BookReader
(Windows: %LOCALAPPDATA%\\BookReader, Linux: ~/.local/share/bookreader, macOS: ~/Library/Application Support/BookReader).
Параметры командной строки важнее файла. Журнал сервера — там же, в папке logs.
"""

import argparse
import hashlib
import html
import zipfile
import inspect
import io
import json
import math
import os
import re
import select
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.parse
import wave
import webbrowser
from collections import OrderedDict, deque
from concurrent.futures import Future, ThreadPoolExecutor
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

try:
    import numpy as np
except ImportError:
    sys.stderr.write("Не установлены библиотеки BookReader (numpy). Запустите install.bat в папке BookReader —\n"
                     "он поставит всё нужное. Или вручную: python -m pip install -r requirements.txt\n")
    sys.exit(3)

import paths

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(ROOT, "web")
CATALOG_DIR = os.path.join(ROOT, "catalog")   # книги, которые клиенты могут скачать с сервера
# «Этот компьютер»: сюда открывается браузер и стучится проверка «сервер уже запущен?».
# Именно 127.0.0.1, а не localhost: библиотека читалки в браузере привязана к адресу страницы —
# смена адреса «потеряла» бы книги пользователя.
LOOPBACK = "127.0.0.1"

VERSION = "1.1.0"
SAMPLE_RATE = 48000
DEFAULT_SPEAKER = "aidar"
MAX_CHUNK = 800          # Silero не любит слишком длинные куски
CACHE_SIZE = 400         # сколько готовых фрагментов держать в памяти
OPUS_BITRATE = "32k"     # Opus для клиента: речь практически без потерь, ~14 МБ на час
BATCH_MAX = 32           # фраз в одном пакетном запросе
BULK_PER_CLIENT = 4      # пакетов предзагрузки от одного клиента одновременно (у приложения их 2)
BULK_TOTAL = 48          # пакетов от всех клиентов одновременно; сверх — «сервер занят, повторите позже» (429)
LIVE_TOTAL = 64          # запросов живого чтения одновременно (их не отклоняем, пока не дойдёт до абсурда)

# В Silero два разных женских голоса с одинаковым именем (xenia и kseniya) — второй называем «Ксюша»,
# чтобы в списке не было двух «Ксений». Порядок списка: мужские, затем женские.
SPEAKER_NAMES = {
    "aidar": "Айдар — мужской",
    "eugene": "Евгений — мужской",
    "baya": "Байя — женский",
    "xenia": "Ксения — женский",
    "kseniya": "Ксюша — женский",
}


# ---------- подготовка текста ----------

import en_translit
import ru_numbers

ENGLISH = True  # --no-english выключает транслитерацию


def numbers_to_words(text):
    """Числа и римские цифры → слова (ru_numbers.py). Silero сама цифры не читает."""
    try:
        text = ru_numbers.normalize(text)
    except Exception as e:  # noqa: BLE001 — озвучка не должна падать из-за одного абзаца
        print("ru_numbers: ошибка на тексте:", repr(text[:80]), repr(e))
    # подстраховка: если цифры всё же остались — читаем их по одной
    return re.sub(r"\d+", lambda m: " " + ru_numbers.digits(m.group()) + " ", text)


LIGATURES = {"\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi", "\ufb04": "ffl", "\ufb05": "st", "\ufb06": "st"}
# мягкие переносы, невидимые пробелы и управляющие символы — Silero на них спотыкается
INVISIBLE = re.compile(r"[\u00ad\u200b-\u200f\u2028-\u202e\u2060-\u206f\ufeff\x00-\x08\x0b-\x1f\x7f]")


def clean_for_speech(text):
    text = INVISIBLE.sub("", text)
    text = text.replace("\u00a0", " ").replace("\u2026", "...")
    text = text.replace("\u2013", "\u2014").replace("\u2011", "-").replace("\u00b1", " плюс-минус ")
    for k, v in LIGATURES.items():
        text = text.replace(k, v)
    text = ru_numbers.abbreviations(text)
    text = numbers_to_words(text)
    text = ru_numbers.units_after_words(text)
    if ENGLISH:
        try:
            text = en_translit.transliterate(text)
        except Exception as e:  # noqa: BLE001
            print("en_translit: ошибка на тексте:", repr(text[:80]), repr(e))
    text = re.sub(r"[\[\]{}<>*#_`~|/\\]", " ", text)
    text = re.sub(r"[^\w\s.,!?:;—\-()\"'«»„“”]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def split_text(text, max_len=MAX_CHUNK):
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    chunks, current = [], ""
    for sentence in sentences:
        while len(sentence) > max_len:  # очень длинное предложение режем по пробелу
            cut = sentence.rfind(" ", 0, max_len)
            cut = cut if cut > 0 else max_len
            if current:
                chunks.append(current)
                current = ""
            chunks.append(sentence[:cut])
            sentence = sentence[cut:].strip()
        if current and len(current) + len(sentence) + 1 > max_len:
            chunks.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        chunks.append(current)
    return chunks


def has_letters(text):
    return re.search(r"[A-Za-zА-Яа-яЁё]", text) is not None


# ---------- движки ----------

def load_silero():
    """Модель Silero v5_ru. Уже скачанная (или положенная установщиком из пакетов) берётся из папки
    torch hub напрямую — без обращения к GitHub; если её нет — torch.hub скачивает её с GitHub."""
    import torch
    local = os.path.join(torch.hub.get_dir(), "snakers4_silero-models_master")
    if os.path.isfile(os.path.join(local, "hubconf.py")):
        try:
            model, _ = torch.hub.load(repo_or_dir=local, model="silero_tts", source="local", language="ru", speaker="v5_ru")
            return model
        except Exception as e:  # noqa: BLE001 — повреждённая папка: пробуем обычным способом
            print("Модель из папки не загрузилась (" + str(e)[:200] + ") — пробую через GitHub")
    model, _ = torch.hub.load(repo_or_dir="snakers4/silero-models", model="silero_tts",
                              language="ru", speaker="v5_ru", trust_repo=True)
    return model


class SileroEngine:
    def __init__(self, device="cpu"):
        import torch

        print("Загружаю модель Silero (v5_ru)...")
        self.torch = torch
        model = load_silero()
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
            if device == "cpu" and shutil.which("nvidia-smi"):
                print("Видеокарта NVIDIA есть, но установленный torch собран без CUDA — синтез пойдёт на процессоре.")
                print("Чтобы считать на видеокарте, поставьте torch с CUDA, например:")
                print("    python -m pip install torch --index-url https://download.pytorch.org/whl/cu128")
        elif device == "cuda" and not torch.cuda.is_available():
            print("CUDA недоступна, остаюсь на CPU")
            device = "cpu"
        model.to(torch.device(device))
        self.model = model
        self.device = device
        self.lock = threading.Lock()

        speakers = list(getattr(model, "speakers", []) or [])
        self.speakers = [s for s in speakers if s != "random"] or list(SPEAKER_NAMES)

        try:
            params = inspect.signature(model.apply_tts).parameters
        except (TypeError, ValueError):
            params = {}
        self.extra = {}
        if "put_accent" in params:
            self.extra["put_accent"] = True
        if "put_yo" in params:
            self.extra["put_yo"] = True
        if device == "cuda":
            try:
                name = torch.cuda.get_device_name(0)
            except Exception:  # noqa: BLE001
                name = "CUDA"
            print(f"Синтез на видеокарте: {name}")
            try:  # прогрев: первые вызовы на GPU заметно медленнее
                self.synth("Проверка.", self.speakers[0])
            except Exception:  # noqa: BLE001
                pass
        print(f"Готово. Устройство: {device}. Голоса: {', '.join(self.speakers)}")

    def synth(self, text, speaker):
        with self.lock, self.torch.inference_mode():
            try:
                audio = self.model.apply_tts(
                    text=text, speaker=speaker, sample_rate=SAMPLE_RATE, **self.extra
                )
            except Exception:
                # иногда модель спотыкается о символы — пробуем только буквы и пунктуацию
                simple = re.sub(r"[^А-Яа-яЁё0-9\s.,!?—\-]", " ", text)
                simple = re.sub(r"\s+", " ", simple).strip()
                if not has_letters(simple):
                    return np.zeros(0, dtype=np.float32)
                audio = self.model.apply_tts(
                    text=simple, speaker=speaker, sample_rate=SAMPLE_RATE, **self.extra
                )
        return audio.detach().cpu().numpy().astype(np.float32)


class FakeEngine:
    """Без модели: короткий тон, длина пропорциональна тексту. Для проверки интерфейса."""

    speakers = list(SPEAKER_NAMES)
    device = "fake"

    def synth(self, text, speaker):
        time.sleep(0.15)
        dur = min(0.4 + len(text) * 0.03, 20)
        t = np.arange(int(SAMPLE_RATE * dur)) / SAMPLE_RATE
        freq = 300 + (abs(hash(speaker)) % 5) * 60
        return (0.15 * np.sin(2 * math.pi * freq * t)).astype(np.float32)


# ---------- сжатие в Opus ----------

class OpusEncoder:
    """Ogg Opus через ffmpeg (из PATH или пакета imageio-ffmpeg). Нет ffmpeg — клиенту уходит WAV."""

    def __init__(self):
        self.exe = shutil.which("ffmpeg")
        if not self.exe:
            try:
                import imageio_ffmpeg
                self.exe = imageio_ffmpeg.get_ffmpeg_exe()
            except Exception:  # noqa: BLE001
                self.exe = None
        self.codec = None
        if self.exe:
            try:
                out = subprocess.run([self.exe, "-hide_banner", "-encoders"], capture_output=True, text=True,
                                     timeout=20, creationflags=NO_WINDOW).stdout
                if "libopus" in out:
                    self.codec = ["-c:a", "libopus", "-b:a", OPUS_BITRATE, "-vbr", "on"]
                elif re.search(r"\bopus\b", out):
                    self.codec = ["-c:a", "opus", "-strict", "-2", "-b:a", OPUS_BITRATE]
            except Exception as e:  # noqa: BLE001
                print("ffmpeg не запускается:", e)
        if self.codec:
            print(f"Сжатие для приложения: Opus {OPUS_BITRATE} (ffmpeg: {self.exe})")
        else:
            print("Opus недоступен (нет ffmpeg) — приложение будет получать WAV, это в ~20 раз больше.")
            print("Установите кодировщик:  python -m pip install imageio-ffmpeg   и перезапустите сервер.")

    @property
    def ok(self):
        return self.codec is not None

    def encode(self, pcm_bytes):
        cmd = [self.exe, "-hide_banner", "-loglevel", "error", "-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", "1",
               "-i", "pipe:0", *self.codec, "-f", "ogg", "pipe:1"]
        r = subprocess.run(cmd, input=pcm_bytes, capture_output=True, timeout=120, creationflags=NO_WINDOW)
        if r.returncode != 0 or not r.stdout.startswith(b"OggS"):
            raise RuntimeError("ffmpeg: " + r.stderr.decode("utf-8", "replace")[-300:])
        return r.stdout


NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def to_wav(pcm_bytes):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm_bytes)
    return buf.getvalue()


# ---------- синтез фрагмента ----------

class ClientGone(Exception):
    """Клиент отключился, пока ждал очереди — считать для него больше нечего."""


class Busy(Exception):
    """Слишком много запросов сразу — клиент повторит через retry секунд."""

    def __init__(self, message, retry=3):
        super().__init__(message)
        self.retry = retry


class Turn:
    """Кто просит модель: клиент, живое ли это чтение, и как узнать, что он ещё на связи."""

    def __init__(self, client="local", live=False, gone=None):
        self.client = client
        self.live = live
        self.gone = gone or (lambda: False)


class FairQueue:
    """
    Очередь к модели для всех клиентов сразу.
    Модель одна и считает одну фразу за раз, поэтому фразы выдаются по кругу: фраза клиенту A, фраза клиенту B,
    фраза клиенту C, снова A… Сколько бы пакетов ни прислал один клиент (книга целиком), остальные ждут
    не дольше одной его фразы. Живое чтение (человек слушает прямо сейчас) идёт впереди предзагрузки.
    """

    def __init__(self):
        self.cond = threading.Condition()
        self.busy = False
        self.lanes = (OrderedDict(), OrderedDict())   # [0] — живое чтение, [1] — предзагрузка; клиент → билеты
        self.served = 0
        self.requests = {}      # (клиент, живое?) → запросов в работе
        self.bulk_total = 0
        self.live_total = 0

    # ----- допуск запросов (не очередь фраз, а защита от лавины запросов) -----

    def admit(self, turn):
        with self.cond:
            k = (turn.client, turn.live)
            if turn.live:
                if self.live_total >= LIVE_TOTAL:
                    raise Busy("сервер занят: слишком много слушателей одновременно", 2)
                self.live_total += 1
            else:
                if self.bulk_total >= BULK_TOTAL:
                    raise Busy("сервер занят предзагрузкой для других устройств — загрузка продолжится сама", 10)
                if self.requests.get(k, 0) >= BULK_PER_CLIENT:
                    raise Busy("с этого устройства уже идёт загрузка — пакет повторится сам", 3)
                self.bulk_total += 1
            self.requests[k] = self.requests.get(k, 0) + 1

    def leave(self, turn):
        with self.cond:
            k = (turn.client, turn.live)
            n = self.requests.get(k, 0) - 1
            if n > 0:
                self.requests[k] = n
            else:
                self.requests.pop(k, None)
            if turn.live:
                self.live_total -= 1
            else:
                self.bulk_total -= 1

    # ----- очередь фраз -----

    def _head(self):
        for lane in self.lanes:
            for tickets in lane.values():
                return tickets[0]
        return None

    def acquire(self, turn):
        ticket = object()
        lane = self.lanes[0 if turn.live else 1]
        with self.cond:
            lane.setdefault(turn.client, deque()).append(ticket)
            try:
                while self.busy or self._head() is not ticket:
                    # раз в секунду проверяем, не отключился ли клиент (ушёл из Wi-Fi, закрыл приложение)
                    if not self.cond.wait(1.0) and turn.gone():
                        raise ClientGone()
            except BaseException:
                tickets = lane.get(turn.client)
                if tickets is not None:
                    try:
                        tickets.remove(ticket)
                    except ValueError:
                        pass
                    if not tickets:
                        del lane[turn.client]
                self.cond.notify_all()
                raise
            # фраза наша; клиент уходит в конец круга (если у него есть ещё фразы)
            tickets = lane[turn.client]
            tickets.popleft()
            if tickets:
                lane.move_to_end(turn.client)
            else:
                del lane[turn.client]
            self.busy = True
            self.served += 1

    def release(self):
        with self.cond:
            self.busy = False
            self.cond.notify_all()

    def stats(self):
        with self.cond:
            clients = {c for c, _ in self.requests}
            return {"clients": len(clients), "live": self.live_total, "bulk": self.bulk_total,
                    "waiting": sum(len(t) for lane in self.lanes for t in lane.values()), "served": self.served}


class Synthesizer:
    def __init__(self, engine, encoder=None):
        self.engine = engine
        self.encoder = encoder
        self.cache = OrderedDict()
        self.cache_lock = threading.Lock()
        self.inflight = {}          # фразы, которые уже считаются для кого-то: второй клиент ждёт их, а не считает заново
        self.queue = FairQueue()
        self.pool = ThreadPoolExecutor(max_workers=max(2, min(8, (os.cpu_count() or 4) // 2)))

    def speaker(self, speaker):
        if speaker not in self.engine.speakers:
            return DEFAULT_SPEAKER if DEFAULT_SPEAKER in self.engine.speakers else self.engine.speakers[0]
        return speaker

    def pcm(self, text, speaker, pause):
        """Фраза → 16-битный PCM 48 кГц моно (bytes), с паузой в конце."""
        cleaned = clean_for_speech(text)
        pieces = []
        if has_letters(cleaned):
            gap = np.zeros(int(SAMPLE_RATE * 0.12), dtype=np.float32)
            for chunk in split_text(cleaned):
                audio = self.engine.synth(chunk, speaker)
                if audio.size:
                    pieces.extend([audio, gap])
            if pieces:
                pieces.pop()
        pieces.append(np.zeros(int(SAMPLE_RATE * max(pause, 0.05)), dtype=np.float32))
        audio = np.concatenate(pieces)
        return (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2").tobytes()

    def fmt(self, want):
        return "opus" if want == "opus" and self.encoder is not None and self.encoder.ok else "wav"

    def _key(self, text, speaker, pause, fmt):
        return (fmt, speaker, text, round(pause, 2), en_translit.version)

    def _cached(self, key):
        with self.cache_lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key]
        return None

    def _store(self, key, data):
        with self.cache_lock:
            self.cache[key] = data
            while len(self.cache) > CACHE_SIZE:
                self.cache.popitem(last=False)

    def _encode(self, pcm, fmt):
        if fmt == "opus":
            try:
                return self.encoder.encode(pcm)
            except Exception as e:  # noqa: BLE001 — сломался ffmpeg: отдаём WAV, клиент поймёт по заголовку файла
                print("Opus не получился, отдаю WAV:", e)
        return to_wav(pcm)

    # ----- одна фраза: кэш → уже считается для другого → считаем сами в свою очередь -----

    def _claim(self, key):
        """(future, моя ли это работа). Если фраза в кэше или уже считается — просто ждать её future."""
        with self.cache_lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                f = Future()
                f.set_result(self.cache[key])
                return f, False
            f = self.inflight.get(key)
            if f is not None:
                return f, False
            f = Future()
            self.inflight[key] = f
            return f, True

    def _finish(self, key, fut, data=None, error=None):
        with self.cache_lock:
            if self.inflight.get(key) is fut:
                del self.inflight[key]
            if error is None:
                self.cache[key] = data
                self.cache.move_to_end(key)
                while len(self.cache) > CACHE_SIZE:
                    self.cache.popitem(last=False)
        if error is None:
            fut.set_result(data)
        else:
            fut.set_exception(error)

    def _model(self, text, speaker, pause, turn):
        """Модель — строго по очереди (FairQueue), одна фраза за раз."""
        self.queue.acquire(turn)
        try:
            return self.pcm(text, speaker, pause)
        finally:
            self.queue.release()

    def _start(self, key, text, speaker, pause, fmt, turn):
        """Взять фразу: future с готовыми байтами. Сжатие идёт в фоне, модель тем временем считает следующую."""
        fut, mine = self._claim(key)
        if not mine:
            return fut
        try:
            pcm = self._model(text, speaker, pause, turn)
        except BaseException as e:  # noqa: BLE001 — и ClientGone: ждущие эту фразу посчитают её сами
            self._finish(key, fut, error=e)
            if isinstance(e, ClientGone):
                raise
            return fut
        enc = self.pool.submit(self._encode, pcm, fmt)

        def done(e, key=key, fut=fut):
            err = e.exception()
            self._finish(key, fut, None if err else e.result(), err)
        enc.add_done_callback(done)
        return fut

    def _result(self, fut, key, text, speaker, pause, fmt, turn):
        """Дождаться фразы. Если её считали для клиента, который ушёл, — посчитать самим."""
        for _ in range(3):
            try:
                return fut.result()
            except ClientGone:
                if turn.gone():
                    raise
                fut = self._start(key, text, speaker, pause, fmt, turn)
        return fut.result()

    def audio(self, text, speaker, pause, want="wav", turn=None):
        """Одна фраза. Возвращает (bytes, формат)."""
        turn = turn or Turn(live=True)
        speaker = self.speaker(speaker)
        pause = max(0.0, min(float(pause or 0), 3.0))
        fmt = self.fmt(want)
        key = self._key(text, speaker, pause, fmt)
        fut = self._start(key, text, speaker, pause, fmt, turn)
        return self._result(fut, key, text, speaker, pause, fmt, turn), fmt

    def wav(self, text, speaker, pause):
        return self.audio(text, speaker, pause, "wav")[0]

    def batch(self, items, speaker, want="opus", turn=None):
        """Пакет фраз (предзагрузка книги). Каждая фраза встаёт в общую очередь отдельно — клиенты чередуются."""
        turn = turn or Turn()
        speaker = self.speaker(speaker)
        fmt = self.fmt(want)
        jobs = []
        for it in items:
            try:
                text = as_text(it.get("text"))
            except BadRequest:
                jobs.append(None)
                continue
            pause = max(0.0, min(as_pause(it.get("pause", 0.2)), 3.0))
            key = self._key(text, speaker, pause, fmt)
            if turn.gone():
                raise ClientGone()
            jobs.append((self._start(key, text, speaker, pause, fmt, turn), key, text, pause))
        out = []
        for j in jobs:
            if j is None:
                out.append(None)
                continue
            fut, key, text, pause = j
            try:
                out.append(self._result(fut, key, text, speaker, pause, fmt, turn))
            except ClientGone:
                raise
            except Exception as e:  # noqa: BLE001 — фраза не удалась: в ответе длина 0, остальные целы
                print("Ошибка синтеза:", repr(e))
                out.append(None)
        return out, fmt



# ---------- каталог книг на сервере ----------

BOOK_EXTS = (".fb2.zip", ".fb2", ".epub", ".mobi", ".azw3", ".azw", ".prc", ".pdf", ".docx", ".odt", ".rtf",
             ".txt", ".md", ".html", ".htm", ".xhtml", ".zip", ".rar", ".7z")
FORMAT_LABEL = {".fb2.zip": "FB2", ".fb2": "FB2", ".epub": "EPUB", ".mobi": "MOBI", ".azw3": "AZW3", ".azw": "AZW",
                ".prc": "MOBI", ".pdf": "PDF", ".docx": "DOCX", ".odt": "ODT", ".rtf": "RTF", ".txt": "TXT",
                ".md": "MD", ".html": "HTML", ".htm": "HTML", ".xhtml": "HTML", ".zip": "ZIP", ".rar": "RAR", ".7z": "7Z"}


def _book_ext(name):
    low = name.lower()
    for e in BOOK_EXTS:
        if low.endswith(e):
            return e
    return None


def _xml_text(raw):
    """Байты начала XML → строка с учётом кодировки из заголовка (FB2 часто в windows-1251)."""
    m = re.search(rb'encoding=["\']([\w\-]+)', raw[:200])
    enc = m.group(1).decode("ascii", "ignore") if m else "utf-8"
    try:
        return raw.decode(enc, "replace")
    except LookupError:
        return raw.decode("utf-8", "replace")


def _tag(text, name):
    m = re.search(r"<(?:\w+:)?%s\b[^>]*>(.*?)</(?:\w+:)?%s>" % (name, name), text, re.S | re.I)
    return html.unescape(re.sub(r"<[^>]+>|\s+", " ", m.group(1))).strip() if m else ""


def _fb2_meta(raw):
    text = _xml_text(raw)
    info = re.search(r"<title-info>(.*?)</title-info>", text, re.S)
    block = info.group(1) if info else text
    authors = []
    for a in re.findall(r"<author>(.*?)</author>", block, re.S):
        name = " ".join(x for x in (_tag(a, "first-name"), _tag(a, "middle-name"), _tag(a, "last-name")) if x) or _tag(a, "nickname")
        if name:
            authors.append(name)
    return _tag(block, "book-title"), authors


def _epub_meta(z):
    try:
        cont = z.read("META-INF/container.xml").decode("utf-8", "replace")
        opf = re.search(r'full-path="([^"]+)"', cont).group(1)
        text = z.read(opf).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return "", []
    creators = [html.unescape(re.sub(r"<[^>]+>", "", c)).strip()
                for c in re.findall(r"<dc:creator\b[^>]*>(.*?)</dc:creator>", text, re.S | re.I)]
    return _tag(text, "dc:title") or _tag(text, "title"), [c for c in creators if c]


def _chapters_label(n):
    k = n % 100
    w = "глав" if 11 <= k <= 14 else {1: "глава", 2: "главы", 3: "главы", 4: "главы"}.get(n % 10, "глав")
    return f"{n} {w}"


def _sha_file(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    return h


def book_meta(path, ext):
    """Название и авторы без полного разбора книги (для списка каталога)."""
    try:
        if ext == ".fb2":
            with open(path, "rb") as f:
                return _fb2_meta(f.read(256 * 1024))
        if ext in (".fb2.zip", ".zip", ".epub"):
            with zipfile.ZipFile(path) as z:
                if ext == ".epub" or "META-INF/container.xml" in z.namelist():
                    return _epub_meta(z)
                inner = next((n for n in z.namelist() if n.lower().endswith(".fb2")), None)
                if inner:
                    with z.open(inner) as f:
                        return _fb2_meta(f.read(256 * 1024))
    except Exception:  # noqa: BLE001
        pass
    return "", []


README_NAME = "ПРОЧТИ.txt"


class Catalog:
    """Папка catalog: всё, что туда положено (с подпапками), видно клиентам. Библиотека браузера/телефона — не видна."""

    def __init__(self, root):
        self.root = root
        self.lock = threading.Lock()
        self.known = {}      # путь → (size, mtime, запись)
        self.by_id = {}
        os.makedirs(root, exist_ok=True)
        readme = os.path.join(root, README_NAME)
        if not os.path.exists(readme) and paths.can_write(root):
            with open(readme, "w", encoding="utf-8") as f:
                f.write("Кладите сюда книги (FB2, EPUB, MOBI/AZW3, PDF, DOCX, ODT, RTF, TXT, HTML, MD), можно по подпапкам.\n"
                        "Их видно в BookReader: кнопка «Каталог» в списке книг (на телефоне и в браузере).\n"
                        "Книга, разбитая на файлы по главам, — положите её архивом (ZIP, RAR или 7z): сервер сам соберёт из него\n"
                        "одну книгу FB2 (файлы по порядку имён, каждый — глава), и клиенты скачают уже готовую книгу.\n"
                        "Ваша личная библиотека в браузере и на телефоне серверу не передаётся и отсюда не видна.\n")

    def scan(self):
        items, seen = [], set()
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            for name in sorted(filenames):
                ext = _book_ext(name)
                if not ext or name.startswith(".") or name == README_NAME:
                    continue
                path = os.path.join(dirpath, name)
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                if st.st_size == 0:
                    continue  # пустой файл (ещё копируется или битый) — не показываем
                seen.add(path)
                with self.lock:
                    cached = self.known.get(path)
                if cached and cached[0] == st.st_size and cached[1] == st.st_mtime:
                    if cached[2]:
                        items.append(cached[2])
                    continue
                h = _sha_file(path)
                if h is None:
                    continue
                rel = os.path.relpath(path, self.root)
                folder = os.path.dirname(rel).replace("\\", "/")
                stem = name[: -len(ext)]
                serve = path
                built = self.built(path, ext, h) if ext in (".zip", ".rar", ".7z") else None
                if built is False:  # архив, из которого ничего не собрать (сообщение уже в окне сервера) — не повторяем
                    with self.lock:
                        self.known[path] = (st.st_size, st.st_mtime, None, None)
                    continue
                if built:
                    # архив из многих файлов → клиенту отдаём уже собранную книгу FB2 (и её id)
                    serve, info = built
                    h = _sha_file(serve)
                    title, authors = info["title"], info["authors"]
                    name, fmt = info.get("name") or stem + ".fb2", info.get("format") or "FB2 · " + _chapters_label(info["chapters"])
                    size = os.path.getsize(serve)
                else:
                    title, authors = book_meta(path, ext)
                    fmt, size = FORMAT_LABEL.get(ext, ext.strip(".").upper()), st.st_size
                item = {
                    # тот же id, что у книги в библиотеке читалки (SHA-256, первые 10 байт) —
                    # клиент видит, что книга у него уже есть
                    "id": h.hexdigest()[:20],
                    "name": name, "folder": folder,
                    "title": title or stem.replace("_", " "), "authors": authors,
                    "format": fmt,
                    "size": size, "mtime": int(st.st_mtime),
                }
                with self.lock:
                    self.known[path] = (st.st_size, st.st_mtime, item, serve)
                items.append(item)
        with self.lock:
            for p in list(self.known):
                if p not in seen:
                    del self.known[p]
            self.by_id = {k[2]["id"]: (k[3], k[2]["name"]) for k in self.known.values() if k[2]}
        return items

    # ---------- архивы из многих файлов: собранная книга в папке данных ----------
    def built(self, path, ext, h):
        """(путь к собранной книге, сведения) — для архива с несколькими книгами (главами);
        None — обычный архив с одной книгой (отдаётся как есть); False — не получилось."""
        import json
        import assemble
        key = h.hexdigest()[:20]
        out_dir = os.path.join(paths.data_dir(), "catalog-built")
        meta_path = os.path.join(out_dir, key + ".json")
        if os.path.exists(meta_path):
            try:
                with open(meta_path, encoding="utf-8") as f:
                    info = json.load(f)
                if info.get("single") and ext == ".zip":
                    return None
                if os.path.exists(os.path.join(out_dir, info["file"])):
                    return os.path.join(out_dir, info["file"]), info
            except (OSError, ValueError, KeyError):
                pass
        os.makedirs(out_dir, exist_ok=True)
        label = os.path.relpath(path, self.root)
        try:
            content = assemble.read_archive(path)
            names = assemble.pick_parts(list(content))
            if len(names) < 2:
                if ext == ".zip" or not names:
                    if ext != ".zip":
                        print(f"Каталог: в архиве «{label}» нет книг — пропускаю")
                        return False
                    with open(meta_path, "w", encoding="utf-8") as f:
                        json.dump({"single": True}, f)
                    return None
                # RAR/7z с одной книгой — распаковываем её (клиенты RAR не открывают)
                inner = names[0]
                data = content[inner]
                fname = key + os.path.splitext(inner)[1].lower()
                with open(os.path.join(out_dir, fname), "wb") as f:
                    f.write(data)
                tmp_title, tmp_authors = book_meta(os.path.join(out_dir, fname), _book_ext(fname))
                info = {"file": fname, "name": os.path.basename(inner.replace("\\", "/")), "title": tmp_title,
                        "authors": tmp_authors, "format": FORMAT_LABEL.get(_book_ext(fname) or "", ""), "chapters": 1}
            else:
                print(f"Каталог: собираю книгу из архива «{label}» ({len(names)} файлов)...")
                data, info = assemble.assemble(path, content)
                info["file"] = key + ".fb2"
                with open(os.path.join(out_dir, info["file"]), "wb") as f:
                    f.write(data)
                print(f"  готово: «{info['title']}», глав: {info['chapters']}" + (f", пропущено файлов: {info['skipped']}" if info["skipped"] else ""))
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(info, f, ensure_ascii=False)
            return os.path.join(out_dir, info["file"]), info
        except Exception as e:  # noqa: BLE001
            print(f"Каталог: архив «{label}» не собран: {e}")
            return False

    def path_of(self, book_id):
        with self.lock:
            p = self.by_id.get(book_id)
        if p is None:
            self.scan()
            with self.lock:
                p = self.by_id.get(book_id)
        return p

# ---------- HTTP ----------

MAX_BODY = 4 * 1024 * 1024   # больше этого клиенты не шлют (пакет 32 фраз по 5000 символов)


class BadRequest(Exception):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


def as_text(v):
    """Текст фразы из запроса: None → "", число → строка, длина ограничена."""
    if v is None:
        return ""
    if isinstance(v, (list, dict)):
        raise BadRequest("текст фразы должен быть строкой")
    return str(v)[:5000]


def as_pause(v):
    try:
        p = float(v)
    except (TypeError, ValueError):
        return 0.2
    return p if math.isfinite(p) else 0.2


def make_handler(synth, catalog=None):
    class Handler(SimpleHTTPRequestHandler):
        # Windows иногда отдаёт .js как text/plain — задаём типы явно
        extensions_map = {
            **SimpleHTTPRequestHandler.extensions_map,
            ".js": "application/javascript; charset=utf-8",
            ".mjs": "application/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".html": "text/html; charset=utf-8",
            ".svg": "image/svg+xml",
            ".json": "application/json",
        }

        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=WEB_DIR, **kwargs)

        # зависшее соединение (клиент пропал посреди запроса) не держит поток вечно
        timeout = 120

        def log_message(self, fmt, *args):
            # частые служебные запросы (озвучка, проверка связи) не пишем — только если ответ с ошибкой
            first = str(args[0]) if args else ""
            code = str(args[1]) if len(args) > 1 else ""
            if ("/api/tts" in first or "/api/ping" in first) and not code.startswith(("4", "5")):
                return
            sys.stderr.write("%s\n" % (fmt % args))

        def send_error(self, code, message=None, explain=None):
            # вместо HTML-страницы — JSON с понятным текстом (клиенты показывают его человеку)
            texts = {404: "такой страницы или команды нет на сервере BookReader",
                     405: "этот запрос сервер не поддерживает", 400: "сервер не понял запрос",
                     414: "слишком длинный адрес запроса", 501: "этот запрос сервер не поддерживает"}
            try:
                self.send_json({"error": texts.get(int(code), message or "ошибка сервера")}, int(code))
            except (ConnectionError, BrokenPipeError, OSError):
                pass

        def end_headers(self):
            self.send_header("Cache-Control", "no-store")
            # клиентские приложения (BookReaderClient) ходят к серверу с другого адреса
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-BookReader-Client")
            super().end_headers()

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def send_json(self, obj, status=200, headers=None):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            # обратный слэш (в том числе %5c) и «..» в адресе — сразу 404: на Windows стандартный обработчик
            # такие пути «сглаживает» и отдаёт главную страницу вместо отказа
            raw = urllib.parse.unquote(self.path.split("?", 1)[0])
            if "\\" in raw or "/../" in raw + "/" or raw.startswith(".."):
                self.send_error(404)
                return
            if self.path.startswith("/api/ping"):
                self.send_json({
                    "ok": True, "name": "BookReader", "version": VERSION,
                    "device": synth.engine.device, "time": time.time(),
                    "client": self.client_address[0], "queue": synth.queue.stats(),
                    "host": server_name(), "id": SERVER_ID,
                })
                return
            if self.path.startswith("/api/catalog"):
                self.send_catalog()
                return
            if self.path.startswith("/api/info"):
                eng = synth.engine
                self.send_json({
                    "speakers": [{"id": s, "name": SPEAKER_NAMES.get(s, s)}
                                 for s in sorted(eng.speakers, key=lambda x: list(SPEAKER_NAMES).index(x) if x in SPEAKER_NAMES else 99)],
                    "default": DEFAULT_SPEAKER if DEFAULT_SPEAKER in eng.speakers else eng.speakers[0],
                    "sample_rate": SAMPLE_RATE,
                    "device": eng.device,
                    "formats": ["opus", "wav"] if synth.fmt("opus") == "opus" else ["wav"],
                    "batch": BATCH_MAX,
                    "catalog": catalog is not None,
                    "english": en_translit.status() if ENGLISH else "off",
                    "name": "BookReader", "version": VERSION, "fair_queue": True,
                })
                return
            super().do_GET()

        def send_catalog(self):
            if catalog is None:
                self.send_json({"error": "каталог на этом сервере выключен"}, 404)
                return
            path = self.path.split("?")[0]
            if path in ("/api/catalog", "/api/catalog/"):
                items = catalog.scan()
                self.send_json({"name": "BookReader", "count": len(items), "items": items})
                return
            m = re.fullmatch(r"/api/catalog/file/([0-9a-f]{20})", path)
            hit = catalog.path_of(m.group(1)) if m else None
            p, name = hit if hit else (None, None)
            if not p or not os.path.isfile(p):
                self.send_json({"error": "книга не найдена в каталоге"}, 404)
                return
            try:
                size = os.path.getsize(p)
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(size))
                self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + urllib.parse.quote(name))
                self.end_headers()
                with open(p, "rb") as f:
                    shutil.copyfileobj(f, self.wfile, 1 << 16)
            except (ConnectionError, BrokenPipeError):
                pass

        def read_body(self):
            """Тело запроса: по Content-Length или потоком (Transfer-Encoding: chunked — так шлёт .NET)."""
            if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
                data = b""
                while True:
                    line = self.rfile.readline(64).strip()
                    try:
                        size = int(line.split(b";")[0] or b"0", 16)
                    except ValueError:
                        raise BadRequest("повреждённый запрос (chunked)") from None
                    if size == 0:
                        self.rfile.readline(64)  # завершающая пустая строка
                        return data
                    if len(data) + size > MAX_BODY:
                        raise BadRequest("запрос слишком большой", 413)
                    data += self.rfile.read(size)
                    self.rfile.readline(64)  # CRLF после куска
            try:
                length = int(self.headers.get("Content-Length", 0) or 0)
            except ValueError:
                raise BadRequest("неверная длина запроса") from None
            if length > MAX_BODY:
                raise BadRequest("запрос слишком большой", 413)
            return self.rfile.read(length) if length > 0 else b""

        def read_json(self):
            raw = self.read_body()
            try:
                req = json.loads(raw.decode("utf-8") or "{}")
            except UnicodeDecodeError:
                raise BadRequest("текст запроса не в кодировке UTF-8") from None
            except ValueError:
                raise BadRequest("запрос не в формате JSON") from None
            if not isinstance(req, dict):
                raise BadRequest("ожидался JSON-объект")
            return req

        def send_bytes(self, data, ctype):
            try:
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except (ConnectionError, BrokenPipeError):
                pass  # клиент передумал — ничего страшного

        def turn(self, live):
            """Кто спрашивает: устройство (заголовок X-BookReader-Client) или, без него, IP-адрес."""
            cid = re.sub(r"[^\w\-]", "", self.headers.get("X-BookReader-Client", ""))[:64]
            return Turn(f"{self.client_address[0]}/{cid}" if cid else self.client_address[0], live, self.gone)

        def gone(self):
            """Клиент закрыл соединение? (проверка без чтения: данные, если есть, остаются в сокете)"""
            try:
                ready, _, _ = select.select([self.connection], [], [], 0)
                if not ready:
                    return False
                return self.connection.recv(1, socket.MSG_PEEK) == b""
            except (OSError, ValueError):
                return True

        def do_POST(self):
            path = self.path.split("?")[0]
            if path not in ("/api/tts", "/api/tts_batch"):
                self.send_error(404)
                return
            try:
                req = self.read_json()
                speaker = str(req.get("speaker") or DEFAULT_SPEAKER)
                want = str(req.get("format") or "wav")
                live = path == "/api/tts" or bool(req.get("live"))
                if path == "/api/tts_batch":
                    items = req.get("items") or []
                    if not isinstance(items, list) or len(items) > BATCH_MAX:
                        raise BadRequest(f"нужен список фраз items, не больше {BATCH_MAX}")
                    items = [it if isinstance(it, dict) else {"text": it} for it in items]
                else:
                    text = as_text(req.get("text"))
                turn = self.turn(live)
                synth.queue.admit(turn)
                try:
                    t0 = time.time()
                    if path == "/api/tts_batch":
                        parts, fmt = synth.batch(items, speaker, want, turn)
                    else:
                        data, fmt = synth.audio(text, speaker, as_pause(req.get("pause", 0.2)), want, turn)
                finally:
                    synth.queue.leave(turn)
                if path == "/api/tts_batch":
                    # ответ: для каждой фразы 4 байта длины (little-endian) + данные; 0 — фраза не удалась
                    body = b"".join(struct.pack("<I", len(p or b"")) + (p or b"") for p in parts)
                    q = synth.queue.stats()
                    who = f", клиентов: {q['clients']}" if q["clients"] > 1 else ""
                    sys.stderr.write(f"пакет: {len(items)} фраз за {time.time() - t0:.1f} с ({fmt}{who})\n")
                    self.send_bytes(body, "application/x-bookreader-batch; format=" + fmt)
                    return
            except ClientGone:
                self.close_connection = True   # клиент ушёл, пока ждал очереди, — отвечать некому
                return
            except Busy as e:
                self.send_json({"error": str(e), "retry": e.retry}, 429, {"Retry-After": str(e.retry)})
                return
            except BadRequest as e:
                self.send_json({"error": str(e)}, e.code)
                return
            except (ConnectionError, BrokenPipeError):
                return
            except Exception as e:  # noqa: BLE001
                print("Ошибка синтеза:", repr(e))
                self.send_json({"error": f"сбой синтеза ({type(e).__name__}: {e})"}, 500)
                return
            self.send_bytes(data, "audio/ogg" if fmt == "opus" else "audio/wav")

    return Handler


DISCOVERY_PORT = 8765                     # UDP: приложение спрашивает «есть тут BookReader?» широковещательно
DISCOVERY_MAGIC = b"BOOKREADER_DISCOVER"


SERVER_ID = os.urandom(8).hex()           # номер этого запуска: клиент склеивает ответы одного сервера с разных адресов
SERVER_NAME = ""                          # имя сервера для клиентов; задаётся --name, иначе имя ПК


def server_name():
    return SERVER_NAME or socket.gethostname()


def own_addresses():
    """Все IPv4-адреса этого ПК (включая 127.x) — на поиск с них сервер не отвечает."""
    ips = {LOOPBACK}
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    ips.update(lan_addresses())
    return ips


def discovery_reply(http_port):
    return json.dumps({"name": f"BookReader ({server_name()})", "app": "BookReader", "host": server_name(),
                       "port": http_port, "version": VERSION, "id": SERVER_ID}, ensure_ascii=False).encode("utf-8")


def discovery_should_answer(data, ip, own):
    """Отвечать ли на UDP-пакет: это запрос поиска BookReader и он пришёл не с этого же ПК."""
    return data.strip().startswith(DISCOVERY_MAGIC) and ip not in own


def start_discovery(http_port):
    """
    Отвечает на поиск сервера из приложения (кнопка «Найти сервер в сети»).
    Запросы с собственных адресов ПК пропускает: приложение на этом же ПК находит сервер напрямую
    через 127.0.0.1, а ответ «самому себе» в сети давал бы лишний дубль в списке.
    """
    def run():
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("", DISCOVERY_PORT))
        except OSError as e:
            print(f"Поиск сервера по сети недоступен (UDP {DISCOVERY_PORT} занят: {e}). Подключение по адресу работает.")
            return
        reply = discovery_reply(http_port)
        own, own_at = set(), 0.0
        while True:
            try:
                data, addr = s.recvfrom(512)
                if time.time() - own_at > 30:      # адреса ПК меняются (Wi-Fi, VPN) — обновляем раз в 30 с
                    own, own_at = own_addresses(), time.time()
                if discovery_should_answer(data, addr[0], own):
                    s.sendto(reply, addr)
            except OSError:
                time.sleep(0.5)

    threading.Thread(target=run, daemon=True, name="discovery").start()


class QuietServer(ThreadingHTTPServer):
    """Обрыв связи клиентом (телефон ушёл из Wi-Fi, вкладку закрыли) — не ошибка, в окно не пишем."""
    daemon_threads = True

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError, OSError)):
            return
        super().handle_error(request, client_address)


def lan_addresses():
    """IP-адреса этого ПК в локальных сетях (включая VPN)."""
    import socket
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    try:
        # UDP-«подключение» к любому несуществующему адресу частной сети ничего не отправляет,
        # но система выбирает исходящий адрес — это и есть адрес ПК в локальной сети
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith("127."))


# ---------- настройки и журнал ----------

DEFAULT_CONFIG = {
    "host": "0.0.0.0",      # 0.0.0.0 — доступ из локальной сети (телефон); 127.0.0.1 — только этот ПК
    "port": 8765,
    "device": "auto",       # auto | cuda | cpu
    "name": "",             # имя сервера в поиске у приложений; пусто — имя ПК
    "catalog": "",          # папка каталога книг; пусто — папка catalog рядом с программой (или в папке данных)
    "catalog_enabled": True,
    "english": True,        # читать английские слова по-русски
    "open_browser": True,
    "update_repo": "",      # «владелец/репозиторий» на GitHub, откуда update.bat -Online берёт обновления
}
CONFIG_HELP = ("Настройки сервера BookReader. host: 0.0.0.0 — доступ из сети, 127.0.0.1 — только этот ПК; "
               "device: auto/cuda/cpu; name: имя в поиске у приложений (пусто — имя ПК); catalog: папка каталога "
               "(пусто — по умолчанию); update_repo: репозиторий GitHub для update.bat -Online. "
               "Параметры командной строки важнее этого файла.")


def load_config():
    """server.json в папке данных: создаётся со значениями по умолчанию, битый файл не мешает запуску."""
    cfg = dict(DEFAULT_CONFIG)
    path = paths.config_path()
    if not os.path.exists(path):
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"_справка": CONFIG_HELP, **DEFAULT_CONFIG}, f, ensure_ascii=False, indent=2)
        except OSError:
            pass
        return cfg
    try:
        with open(path, encoding="utf-8-sig") as f:
            user = json.load(f)
        if isinstance(user, dict):
            for k, v in user.items():
                if k in cfg and isinstance(v, type(DEFAULT_CONFIG[k])):
                    cfg[k] = v
    except (OSError, ValueError) as e:
        print(f"Файл настроек {path} не прочитан ({e}) — использую настройки по умолчанию.")
    return cfg


LOG_MAX_BYTES = 2 * 1024 * 1024   # один файл журнала
LOG_KEEP = 3                      # сколько прошлых файлов хранить: server.1.log … server.3.log
LOG_MAX_AGE_DAYS = 30             # прочие старые журналы в папке logs удаляются


class _RotatingLog:
    """Файл журнала, который не растёт бесконечно: больше LOG_MAX_BYTES — уходит в server.1.log
    (прежние сдвигаются, самый старый удаляется), и запись продолжается в новый server.log.
    Проверка — на каждой записи, а не только при запуске: сервер может работать неделями.
    Итого на диске не больше (LOG_KEEP + 1) * LOG_MAX_BYTES."""

    def __init__(self, path, max_bytes=LOG_MAX_BYTES, keep=LOG_KEEP):
        self.path, self.max_bytes, self.keep = path, max_bytes, keep
        self.lock = threading.Lock()
        self.f = None
        self._open()
        if self.size > self.max_bytes:
            self._rotate()

    def _open(self):
        self.f = open(self.path, "a", encoding="utf-8")  # noqa: SIM115 — открыт до конца работы
        try:
            self.size = os.path.getsize(self.path)
        except OSError:
            self.size = 0

    def _old(self, n):
        base, ext = os.path.splitext(self.path)
        return f"{base}.{n}{ext}"

    def _rotate(self):
        try:
            self.f.close()
        except OSError:
            pass
        try:
            for n in range(self.keep, 0, -1):
                src = self.path if n == 1 else self._old(n - 1)
                if os.path.exists(src):
                    os.replace(src, self._old(n))
        except OSError:
            pass   # файл занят (открыт в блокноте и т. п.) — продолжим в тот же, попробуем позже
        self._open()

    def write(self, text):
        with self.lock:
            self.f.write(text)
            self.f.flush()
            self.size += len(text.encode("utf-8", "replace"))
            if self.size > self.max_bytes:
                self._rotate()

    def flush(self):
        with self.lock:
            self.f.flush()


class _Tee:
    """Вывод в консоль и одновременно в журнал (logs/server.log в папке данных)."""

    def __init__(self, stream, log):
        self.stream, self.log = stream, log

    def write(self, text):
        try:
            self.stream.write(text)
        except (OSError, ValueError, UnicodeError, AttributeError):
            pass
        try:
            self.log.write(text)
        except (OSError, ValueError):
            pass
        return len(text)

    def flush(self):
        for s in (self.stream, self.log):
            try:
                s.flush()
            except (OSError, ValueError, AttributeError):
                pass

    def __getattr__(self, name):
        return getattr(self.stream, name)


def clean_old_logs(folder, keep_names, max_age_days=LOG_MAX_AGE_DAYS):
    """Удаляет из папки журналов файлы *.log старше max_age_days (кроме текущих)."""
    limit = time.time() - max_age_days * 86400
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for name in names:
        full = os.path.join(folder, name)
        if name in keep_names or not name.lower().endswith(".log") or not os.path.isfile(full):
            continue
        try:
            if os.path.getmtime(full) < limit:
                os.remove(full)
        except OSError:
            pass


def start_log():
    """Журнал сервера: logs/server.log в папке данных, с ограничением размера. Возвращает путь."""
    try:
        folder = paths.logs_dir()
        path = os.path.join(folder, "server.log")
        clean_old_logs(folder, {"server.log", "install.log"})
        log = _RotatingLog(path)
        log.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} запуск BookReader {VERSION} =====\n")
        sys.stdout = _Tee(sys.stdout, log)
        sys.stderr = _Tee(sys.stderr, log)
        return path
    except OSError:
        return None


def server_running(port):
    """На этом ПК уже работает BookReader на этом порту?"""
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://{LOOPBACK}:{port}/api/ping", timeout=1.5) as r:
            return json.loads(r.read().decode("utf-8")).get("name") == "BookReader"
    except Exception:  # noqa: BLE001
        return False


def main():
    cfg = load_config()
    ap = argparse.ArgumentParser(description="BookReader — сервер озвучки Silero. Настройки по умолчанию — в " + paths.config_path())
    ap.add_argument("--host", default=cfg["host"], help="0.0.0.0 — доступ из локальной сети (телефон), 127.0.0.1 — только этот ПК")
    ap.add_argument("--port", type=int, default=cfg["port"])
    ap.add_argument("--device", choices=["auto", "cuda", "cpu"], default=cfg["device"] if cfg["device"] in ("auto", "cuda", "cpu") else "auto",
                    help="где считать: auto — видеокарта, если доступна")
    ap.add_argument("--cuda", action="store_true", help="то же, что --device cuda")
    ap.add_argument("--fake", action="store_true", help="без модели, для проверки интерфейса")
    ap.add_argument("--no-browser", action="store_true", default=not cfg["open_browser"])
    ap.add_argument("--catalog", default=cfg["catalog"] or None, help="папка с книгами, которые можно скачать с сервера (каталог)")
    ap.add_argument("--no-catalog", action="store_true", default=not cfg["catalog_enabled"], help="не показывать каталог книг клиентам")
    ap.add_argument("--name", default=cfg["name"], help="имя сервера в списке поиска у клиентов (по умолчанию — имя ПК)")
    ap.add_argument("--no-english", action="store_true", default=not cfg["english"], help="не транслитерировать английские слова")
    ap.add_argument("--open-if-running", action="store_true",
                    help="если сервер уже запущен — открыть читалку в браузере и выйти (код 0), иначе код 1")
    args = ap.parse_args()
    if args.open_if_running:
        if server_running(args.port):
            webbrowser.open(f"http://{LOOPBACK}:{args.port}/")
            sys.exit(0)
        sys.exit(1)
    log_path = start_log()
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleTitleW("BookReader — сервер озвучки (не закрывайте это окно)")
        except Exception:  # noqa: BLE001
            pass

    global ENGLISH, SERVER_NAME
    ENGLISH = not args.no_english
    SERVER_NAME = re.sub(r"\s+", " ", args.name).strip()[:60]
    if ENGLISH:
        en_translit.preload(background=True)

    engine = FakeEngine() if args.fake else SileroEngine("cuda" if args.cuda else args.device)
    synth = Synthesizer(engine, OpusEncoder())

    # каталог: из настроек; иначе папка catalog рядом с программой, а если туда писать нельзя (Program Files) —
    # в папке данных пользователя
    catalog_dir = os.path.abspath(os.path.expanduser(args.catalog)) if args.catalog else (
        CATALOG_DIR if os.path.isdir(CATALOG_DIR) or paths.can_write(ROOT) else os.path.join(paths.data_dir(), "catalog"))
    catalog = None if args.no_catalog else Catalog(catalog_dir)
    if catalog:
        print(f"Каталог книг для клиентов: {catalog.root}  (найдено: {len(catalog.scan())})")
    server = QuietServer((args.host, args.port), make_handler(synth, catalog))
    url = f"http://{LOOPBACK if args.host in ('0.0.0.0', '', LOOPBACK) else args.host}:{args.port}"
    print(f"BookReader запущен: {url}  (Ctrl+C — остановить)")
    print(f"Настройки: {paths.config_path()}" + (f"   Журнал: {log_path}" if log_path else ""))
    if args.host in ("0.0.0.0", ""):
        start_discovery(args.port)
        ips = lan_addresses()
        if ips:
            print(f"Имя сервера в поиске: BookReader ({server_name()})  (поменять: --name \"Мой ПК\")")
            print("Адрес для телефона / клиента BookReader:")
            for ip in ips:
                print(f"    {ip}:{args.port}")
            print("Если телефон не подключается — разрешите Python в брандмауэре Windows (частные сети).")
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановлено")


if __name__ == "__main__":
    main()
