"""
Тесты сервера BookReader перед релизом.

Запуск (из папки BookReader):   python tests/test_server.py
Модель Silero не нужна: сервер поднимается в тестовом режиме (--fake) на свободном порту.
"""
import json
import re
import hashlib
import os
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
# настройки и журналы тестовых серверов — во временной папке, а не в данных пользователя
os.environ["BOOKREADER_HOME"] = tempfile.mkdtemp(prefix="bookreader-test-")
import ru_numbers  # noqa: E402
import server  # noqa: E402

server.ENGLISH = False


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class NormalizerCases(unittest.TestCase):
    """tests/normalizer_cases.tsv: случаи «ok» не должны ломаться (отчёт: python tests/normalizer_cases.py)."""

    def test_cases(self):
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import normalizer_cases as nc
        broken = []
        for r in nc.load():
            if r["state"] != "ok":
                continue
            got = nc.speak(r["text"])
            if not nc.same(got, r["want"]):
                broken.append(f"стр. {r['line']}: «{r['text']}» → «{got}», ждём «{r['want']}»")
        self.assertFalse(broken, "\n" + "\n".join(broken))


class BrokenFb2(unittest.TestCase):
    """Битый FB2 из конвертеров: абзацы вокруг строк таблицы, незакрытые теги — глава не теряется."""

    def test_table_in_p(self):
        import assemble
        x = ('<?xml version="1.0"?><FictionBook><body><section><title><p>Глава 1. Начало</p></title><p>до</p>'
             '<table><p><tr><td> ячейка </td></p><p></tr></p></table><p>после</p></section></body></FictionBook>')
        title, blocks, _, _ = assemble._fb2_part(x.encode())
        self.assertEqual(title, "Глава 1. Начало")
        self.assertIn("ячейка", "".join(blocks))
        self.assertIn("после", "".join(blocks))

    def test_unclosed(self):
        import assemble
        x = '<FictionBook><body><section><title><p>Глава 2</p></title><p>a <strong>b</p><p>c</emphasis> d</p></section></body></FictionBook>'
        title, blocks, _, _ = assemble._fb2_part(x.encode())
        self.assertEqual(title, "Глава 2")
        self.assertEqual(len(blocks), 2)


class Numbers(unittest.TestCase):
    """Числа, даты, единицы, сокращения → слова."""
    CASES = {
        "123": "сто двадцать три",
        "-5": "минус пять",
        "3,14": "три целых четырнадцать сотых",
        "1 000 000": "миллион",
        "12.03.2024": "двенадцатое марта две тысячи двадцать четвёртого года",
        "2024-01-05": "пятое января две тысячи двадцать четвёртого года",
        "05/12/2024": "пятое декабря две тысячи двадцать четвёртого года",
        "в 1990 г. он": "в тысяча девятьсот девяностом году он",
        "XIX–XX вв.": "девятнадцатого—двадцатого веков",
        "Глава IV": "Глава четвёртая",
        "Людовик XIV": "Людовик четырнадцатый",
        "50%": "пятьдесят процентов",
        "20°C": "двадцать градусов по Цельсию",
        "5 кг": "пять килограммов",
        "2 тыс. км": "две тысячи километров",
        "1/2 стакана": "одна вторая стакана",
        "3/4": "три четвёртых",
        "$100": "сто долларов",
        "5 ₽": "пять рублей",
        "т. е. так": "то есть так",
        "и т. д.": "и так далее",
        "стр. 5": "страница пять",
        "5-й": "пятый",
        "в 90-е": "в девяностые",
        "№5": "номер пять",
    }

    def test_cases(self):
        for src, want in self.CASES.items():
            with self.subTest(src=src):
                self.assertEqual(server.clean_for_speech(src), want)

    def test_never_crashes(self):
        weird = ["", " ", "9" * 400, "1/0", "0/0", "XIV" * 50, "\x00\x01", "🙂" * 10, "а" * 20000,
                 "1." * 500, "-" * 100, "%°$€", "12:99", "32.13.2024", "MMMMCMXCIX", "1e10", "+-+-5"]
        for s in weird:
            with self.subTest(s=s[:20]):
                out = server.clean_for_speech(s)
                self.assertIsInstance(out, str)
                self.assertNotRegex(out, r"\d")  # цифр в тексте для Silero остаться не должно

    def test_split_long(self):
        parts = server.split_text("слово " * 2000)
        self.assertTrue(all(len(p) <= server.MAX_CHUNK for p in parts))
        parts = server.split_text("а" * 3000)  # без пробелов вообще
        self.assertTrue(all(len(p) <= server.MAX_CHUNK for p in parts))

    def test_invisible_chars(self):
        self.assertEqual(server.clean_for_speech("при\u00adвет\u200b мир"), "привет мир")



class RecEngine:
    """Модель-заглушка: запоминает, в каком порядке считались фразы, и проверяет, что одновременно — не больше одной."""
    speakers = list(server.SPEAKER_NAMES)
    device = "fake"

    def __init__(self, delay=0.03):
        self.delay = delay
        self.order = []
        self.active = 0
        self.overlap = False
        self.lock = threading.Lock()

    def synth(self, text, speaker):
        with self.lock:
            self.active += 1
            self.overlap |= self.active > 1
            self.order.append(text)
        time.sleep(self.delay)
        with self.lock:
            self.active -= 1
        return server.np.zeros(480, dtype=server.np.float32)


class Fairness(unittest.TestCase):
    """Несколько клиентов одновременно: модель по очереди, клиенты по кругу, живое чтение — первым."""

    def make(self, delay=0.03):
        eng = RecEngine(delay)
        return eng, server.Synthesizer(eng, None)

    def run_threads(self, fns):
        out, errs = {}, []

        def wrap(name, fn):
            try:
                out[name] = fn()
            except Exception as e:  # noqa: BLE001
                errs.append(e)
        ts = [threading.Thread(target=wrap, args=(n, f)) for n, f in fns.items()]
        for t in ts:
            t.start()
            time.sleep(0.005)
        for t in ts:
            t.join(30)
        self.assertEqual(errs, [])
        return out

    def test_round_robin(self):
        eng, syn = self.make()
        fns = {c: (lambda c=c: syn.batch([{"text": f"{c} фраза {i}"} for i in range(6)], "aidar", "wav",
                                         server.Turn(c))) for c in "ABC"}
        out = self.run_threads(fns)
        self.assertFalse(eng.overlap, "модель считала две фразы одновременно")
        for c in "ABC":
            self.assertEqual(len([x for x in out[c][0] if x]), 6)
        who = [t[0] for t in eng.order]
        # после первой фразы клиенты чередуются: в каждой тройке — все трое
        for k in range(1, 16, 3):
            self.assertEqual(set(who[k:k + 3]), set("ABC"), who)

    def test_one_client_many_batches(self):
        eng, syn = self.make()
        fns = {f"A{j}": (lambda j=j: syn.batch([{"text": f"A{j} фраза {i}"} for i in range(8)], "aidar", "wav",
                                               server.Turn("A"))) for j in range(4)}
        fns["B"] = lambda: syn.batch([{"text": f"B фраза {i}"} for i in range(4)], "aidar", "wav", server.Turn("B"))
        self.run_threads(fns)
        who = [t[0] for t in eng.order]
        # у A четыре пакета, у B один — но B не ждёт все пакеты A: его 4 фразы среди первых 9
        self.assertGreaterEqual(who[:9].count("B"), 4, who)

    def test_live_first(self):
        eng, syn = self.make(0.05)
        res = {}

        def bulk(c):
            return syn.batch([{"text": f"{c} фраза {i}"} for i in range(10)], "aidar", "wav", server.Turn(c))

        def live():
            time.sleep(0.2)
            t0 = time.time()
            syn.audio("живое чтение", "aidar", 0.2, "wav", server.Turn("L", live=True))
            res["wait"] = time.time() - t0
        self.run_threads({"A": lambda: bulk("A"), "B": lambda: bulk("B"), "L": live})
        # ждёт не больше пары фраз (текущая + своя), а не 20 фраз очереди
        self.assertLess(res["wait"], 0.35, res)

    def test_same_phrase_once(self):
        eng, syn = self.make(0.1)
        items = [{"text": f"общая фраза {i}"} for i in range(5)]
        out = self.run_threads({c: (lambda c=c: syn.batch(items, "aidar", "wav", server.Turn(c))) for c in "ABC"})
        self.assertEqual(len(eng.order), 5, "одну и ту же фразу для разных клиентов считать один раз")
        self.assertEqual(out["A"][0], out["C"][0])

    def test_client_gone(self):
        eng, syn = self.make(0.05)
        gone = threading.Event()
        res = {}

        def leaver():
            try:
                syn.batch([{"text": f"фраза {i}"} for i in range(20)], "aidar", "wav", server.Turn("A", gone=gone.is_set))
            except server.ClientGone:
                res["gone"] = True

        def stayer():
            time.sleep(0.12)
            # та же фраза, что сейчас в очереди у ушедшего клиента: должна прийти, а не потеряться
            return syn.batch([{"text": "фраза 5"}, {"text": "фраза 6"}], "aidar", "wav", server.Turn("B"))
        t = threading.Thread(target=leaver)
        t.start()
        time.sleep(0.1)
        gone.set()
        out = stayer()
        t.join(10)
        self.assertTrue(res.get("gone"))
        self.assertTrue(all(out[0]), "фразы второго клиента не должны пропасть")
        self.assertLess(len(eng.order), 12, "после ухода клиента его фразы больше не считаются")
        self.assertEqual(syn.queue.stats()["waiting"], 0)
        self.assertFalse(syn.queue.busy)

    def test_synth_error_isolated(self):
        eng, syn = self.make()
        bad = eng.synth

        def synth(text, speaker):
            if "сломай" in text:
                raise RuntimeError("модель упала")
            return bad(text, speaker)
        eng.synth = synth
        out, _ = syn.batch([{"text": "раз"}, {"text": "сломай"}, {"text": "три"}], "aidar", "wav", server.Turn("A"))
        self.assertTrue(out[0] and out[2])
        self.assertIsNone(out[1])
        self.assertFalse(syn.queue.busy)   # ошибка не оставила модель «занятой»
        out, _ = syn.batch([{"text": "сломай"}], "aidar", "wav", server.Turn("B"))
        self.assertIsNone(out[0])           # ошибку не закэшировали как «готово», но и не зависли

    def test_admission(self):
        q = server.FairQueue()
        turns = [server.Turn("A") for _ in range(server.BULK_PER_CLIENT)]
        for t in turns:
            q.admit(t)
        with self.assertRaises(server.Busy) as e:
            q.admit(server.Turn("A"))
        self.assertRegex(str(e.exception), "[а-я]")
        q.admit(server.Turn("B"))           # другой клиент — проходит
        q.admit(server.Turn("A", live=True))  # и живое чтение того же клиента — тоже
        for t in turns:
            q.leave(t)
        q.admit(server.Turn("A"))
        self.assertEqual(q.stats()["clients"], 2)


class Paths(unittest.TestCase):
    """Данные — в папке пользователя, а не рядом с программой; без абсолютных путей."""

    def test_data_dir_and_fallback(self):
        import paths
        self.assertEqual(paths.data_dir(), os.path.abspath(os.environ["BOOKREADER_HOME"]))
        self.assertTrue(paths.can_write(paths.logs_dir()))
        # папку «рядом с программой» создать нельзя (как в Program Files без прав) — берётся папка данных
        blocker = os.path.join(paths.data_dir(), "file-not-dir")
        open(blocker, "w").close()
        got = paths.writable_dir(os.path.join(blocker, "catalog"), "catalog")
        self.assertEqual(got, os.path.join(paths.data_dir(), "catalog"))

    def test_config(self):
        import paths
        cfg_path = paths.config_path()
        if os.path.exists(cfg_path):
            os.remove(cfg_path)
        cfg = server.load_config()
        self.assertEqual(cfg["port"], 8765)
        self.assertTrue(os.path.exists(cfg_path))          # создан с настройками по умолчанию
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump({"port": 9001, "host": 5, "name": "Мой ПК", "лишнее": 1}, f, ensure_ascii=False)
        cfg = server.load_config()
        self.assertEqual((cfg["port"], cfg["host"], cfg["name"]), (9001, "0.0.0.0", "Мой ПК"))  # неверный тип — по умолчанию
        with open(cfg_path, "w", encoding="utf-8") as f:
            f.write("{битый json")
        self.assertEqual(server.load_config()["port"], 8765)  # битый файл не мешает запуску
        os.remove(cfg_path)

    def test_open_if_running_not_running(self):
        r = subprocess.run([sys.executable, os.path.join(ROOT, "server.py"), "--open-if-running", "--port", str(free_port())],
                           capture_output=True, timeout=30)
        self.assertEqual(r.returncode, 1)


class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cat = tempfile.mkdtemp()
        with open(os.path.join(cls.cat, "книга с пробелами & #1.fb2"), "w", encoding="utf-8") as f:
            f.write('<?xml version="1.0" encoding="utf-8"?><FictionBook><description><title-info>'
                    '<book-title>Тест</book-title><author><first-name>Иван</first-name></author></title-info>'
                    '</description><body><section><p>Текст.</p></section></body></FictionBook>')
        open(os.path.join(cls.cat, "empty.fb2"), "w").close()
        # архив «по файлу на главу» — сервер отдаёт его одной собранной книгой FB2
        with zipfile.ZipFile(os.path.join(cls.cat, "Сага_том_2.zip"), "w") as z:
            for k in (1, 10, 2):
                z.writestr(f"Сага_Глава_{k}._Часть_{k}.fb2",
                           '<?xml version="1.0" encoding="utf-8"?><FictionBook><description><title-info>'
                           f'<book-title>Глава {k}</book-title></title-info></description><body><section>'
                           f'<p>Текст главы {k}.</p></section></body></FictionBook>')
            z.writestr("readme.txt", "не книга")
        with open(os.path.join(cls.cat, "broken.epub"), "wb") as f:
            f.write(b"PK\x03\x04garbage")
        cls.port = free_port()
        cls.url = f"http://127.0.0.1:{cls.port}"
        cls.proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "server.py"), "--fake", "--no-browser",
                                     "--no-english", "--host", "0.0.0.0", "--port", str(cls.port), "--catalog", cls.cat],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                urllib.request.urlopen(cls.url + "/api/ping", timeout=1)
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(5)

    def call(self, path, body=None, raw=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(self.url + path, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def error_text(self, data):
        msg = json.loads(data)["error"]
        self.assertRegex(msg, "[а-яё]", "ошибка должна быть по-русски: " + msg)
        return msg

    def test_log_and_running(self):
        import paths
        log = os.path.join(paths.logs_dir(), "server.log")
        self.assertTrue(os.path.exists(log), "журнал сервера — в папке данных")
        self.assertTrue(server.server_running(self.port))
        self.assertFalse(server.server_running(free_port()))

    def test_info_and_ping(self):
        st, d = self.call("/api/info")
        info = json.loads(d)
        self.assertEqual(st, 200)
        self.assertTrue(info["speakers"])
        self.assertTrue(info["catalog"])

    def test_tts_ok(self):
        st, d = self.call("/api/tts", {"text": "Привет, 2024 год!", "speaker": "xenia", "pause": 0.3})
        self.assertEqual(st, 200)
        self.assertEqual(d[:4], b"RIFF")
        st, d = self.call("/api/tts", {"text": "Привет", "format": "opus"})
        self.assertIn(d[:4], (b"OggS", b"RIFF"))

    def test_tts_bad_input(self):
        for raw in (b"{x", b"[1,2]", b'{"text":"\xff"}', b'{"text":[1]}'):
            with self.subTest(raw=raw):
                st, d = self.call("/api/tts", raw=raw)
                self.assertEqual(st, 400)
                self.error_text(d)

    def test_tts_tolerates(self):
        for body in ({"text": None}, {"text": 5}, {"text": "а", "pause": "abc"}, {"text": "а", "pause": -9},
                     {"text": "а", "speaker": "../../x"}, {"text": "а", "format": "mp3"}, {"text": "...!?"},
                     {"text": "слово " * 3000}):
            with self.subTest(body=str(body)[:40]):
                st, d = self.call("/api/tts", body)
                self.assertEqual(st, 200)
                self.assertIn(d[:4], (b"RIFF", b"OggS"))

    def test_batch(self):
        st, d = self.call("/api/tts_batch", {"speaker": "baya", "format": "opus",
                                              "items": [{"text": "раз"}, "два", 3, None, {"text": [1]}]})
        self.assertEqual(st, 200)
        sizes, pos = [], 0
        while pos < len(d):
            n, = struct.unpack_from("<I", d, pos)
            sizes.append(n)
            pos += 4 + n
        self.assertEqual(len(sizes), 5)
        self.assertEqual(sizes[4], 0)  # неверная фраза — пустая, остальные озвучены
        st, d = self.call("/api/tts_batch", {"items": [{"text": "а"}] * (server.BATCH_MAX + 1)})
        self.assertEqual(st, 400)
        self.error_text(d)

    def test_unknown_and_traversal(self):
        for path in ("/api/nope", "/../server.py", "/%2e%2e/server.py", "/..%5cserver.py", "/api/catalog/file/../x"):
            with self.subTest(path=path):
                st, d = self.call(path)
                self.assertEqual(st, 404)
                self.assertNotIn(b"import", d)

    def test_huge_content_length(self):
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        s.sendall(b"POST /api/tts HTTP/1.1\r\nHost: x\r\nContent-Length: 2000000000\r\n\r\n")
        self.assertIn(b"413", s.recv(100))
        s.close()

    def test_catalog(self):
        st, d = self.call("/api/catalog")
        items = json.loads(d)["items"]
        names = {i["name"] for i in items}
        self.assertIn("книга с пробелами & #1.fb2", names)
        self.assertNotIn("empty.fb2", names)        # пустые файлы не показываем
        self.assertIn("broken.epub", names)         # битый файл виден, метаданные — из имени
        book = next(i for i in items if i["name"].startswith("книга"))
        self.assertEqual(book["title"], "Тест")
        st, d = self.call("/api/catalog/file/" + book["id"])
        self.assertEqual(st, 200)
        self.assertIn(b"FictionBook", d)
        st, d = self.call("/api/catalog/file/" + "0" * 20)
        self.assertEqual(st, 404)
        self.error_text(d)

    def test_catalog_assembled(self):
        st, d = self.call("/api/catalog")
        item = next(i for i in json.loads(d)["items"] if i["name"].startswith("Сага"))
        self.assertEqual(item["name"], "Сага_том_2.fb2")
        self.assertTrue(item["format"].startswith("FB2"), item["format"])
        st, d = self.call("/api/catalog/file/" + item["id"])
        self.assertEqual(st, 200)
        self.assertEqual(hashlib.sha256(d).hexdigest()[:20], item["id"])   # id — у собранной книги
        titles = re.findall(r"<section><title><p>(.*?)</p>", d.decode("utf-8"))
        self.assertEqual(titles, ["Глава 1. Часть 1", "Глава 2. Часть 2", "Глава 10. Часть 10"])

    def test_parallel(self):
        results = []

        def work(i):
            results.append(self.call("/api/tts", {"text": f"фраза {i}", "format": "opus"})[0])
        threads = [threading.Thread(target=work, args=(i,)) for i in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(set(results), {200})

    def test_many_clients_http(self):
        """Три устройства качают книги и одно слушает — всем 200, ответы целые."""
        def post(path, body, cid):
            req = urllib.request.Request(self.url + path, data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json", "X-BookReader-Client": cid})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, r.read()
        res, errs = [], []

        def batch(cid, j):
            try:
                st, d = post("/api/tts_batch", {"format": "opus", "items": [f"{cid} {j} фраза {i}" for i in range(6)]}, cid)
                n, pos = 0, 0
                while pos < len(d):
                    ln, = struct.unpack_from("<I", d, pos)
                    self.assertGreater(ln, 0)
                    pos += 4 + ln
                    n += 1
                res.append((st, n))
            except Exception as e:  # noqa: BLE001
                errs.append(e)
        ts = [threading.Thread(target=batch, args=(c, j)) for c in ("tel1", "tel2", "tab") for j in range(2)]
        for t in ts:
            t.start()
        time.sleep(0.4)
        t0 = time.time()
        st, d = post("/api/tts", {"text": "слушаю сейчас", "format": "opus"}, "tel3")
        live = time.time() - t0
        for t in ts:
            t.join(60)
        self.assertEqual(errs, [])
        self.assertEqual(st, 200)
        self.assertEqual(res, [(200, 6)] * 6)
        self.assertLess(live, 1.0, f"живое чтение ждало {live:.1f} с за чужими пакетами")

    def test_too_many_batches_429(self):
        def post(cid):
            req = urllib.request.Request(self.url + "/api/tts_batch", headers={"X-BookReader-Client": cid},
                                         data=json.dumps({"items": [f"{cid} {time.time()} {i}" for i in range(4)]}).encode())
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    return r.status, None, None
            except urllib.error.HTTPError as e:
                return e.code, e.headers.get("Retry-After"), e.read()
        out = []
        ts = [threading.Thread(target=lambda: out.append(post("greedy"))) for _ in range(server.BULK_PER_CLIENT + 3)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(60)
        codes = sorted(o[0] for o in out)
        self.assertEqual(codes.count(200), server.BULK_PER_CLIENT, codes)
        busy = [o for o in out if o[0] == 429]
        self.assertEqual(len(busy), 3)
        self.assertTrue(busy[0][1].isdigit())
        self.error_text(busy[0][2])

    def test_disconnect_frees_queue(self):
        """Клиент отправил большой пакет и пропал — сервер перестаёт считать его фразы."""
        body = json.dumps({"items": [f"пропал {time.time()} {i}" for i in range(server.BATCH_MAX)]}).encode()
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        s.sendall(b"POST /api/tts_batch HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
                  b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
        time.sleep(0.5)
        s.close()
        for _ in range(40):
            q = json.loads(self.call("/api/ping")[1])["queue"]
            if q["bulk"] == 0:
                break
            time.sleep(0.1)
        self.assertEqual(q["bulk"], 0, "сервер продолжает считать для отключившегося клиента")
        st, _ = self.call("/api/tts", {"text": "после обрыва"})
        self.assertEqual(st, 200)

    def test_discovery(self):
        """Ответ на поиск: имя «BookReader (ПК)», порт; на запросы с самого ПК сервер молчит."""
        info = json.loads(server.discovery_reply(8765))
        self.assertRegex(info["name"], r"^BookReader \(.+\)$")
        self.assertEqual(info["port"], 8765)
        self.assertEqual(info["app"], "BookReader")
        self.assertTrue(info["id"])
        own = {"127.0.0.1", "192.0.2.5"}
        self.assertTrue(server.discovery_should_answer(server.DISCOVERY_MAGIC + b"\n", "192.0.2.20", own))
        self.assertFalse(server.discovery_should_answer(server.DISCOVERY_MAGIC, "192.0.2.5", own))
        self.assertFalse(server.discovery_should_answer(server.DISCOVERY_MAGIC, "127.0.0.1", own))
        self.assertFalse(server.discovery_should_answer(b"hello", "192.0.2.20", own))
        self.assertIn("127.0.0.1", server.own_addresses())
        # запрос с этого же ПК — тишина (приложение на ПК находит сервер через 127.0.0.1 и /api/ping)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1.5)
        s.sendto(server.DISCOVERY_MAGIC, ("127.0.0.1", server.DISCOVERY_PORT))
        with self.assertRaises(socket.timeout):
            s.recvfrom(2048)
        s.close()
        ping = json.loads(self.call("/api/ping")[1])
        self.assertTrue(ping["host"] and ping["id"])
        # «другой компьютер»: на Linux весь 127.0.0.0/8 — этот же ПК, но 127.0.0.2 сервер своим не считает
        if sys.platform.startswith("linux") and "127.0.0.2" not in server.own_addresses():
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.bind(("127.0.0.2", 0))
            s.settimeout(3)
            s.sendto(server.DISCOVERY_MAGIC, ("127.0.0.1", server.DISCOVERY_PORT))
            data, _ = s.recvfrom(2048)
            s.close()
            info = json.loads(data)
            self.assertTrue(info["name"].startswith("BookReader ("))
            self.assertEqual(info["id"], ping["id"])   # ответил именно запущенный тестом сервер


if __name__ == "__main__":
    unittest.main(verbosity=2)
