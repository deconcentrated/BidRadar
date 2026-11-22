import base64
import warnings
import http.cookiejar
import json
import urllib.parse
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

warnings.simplefilter("ignore", ResourceWarning)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.environ["RM_OFFLINE"] = "1"
os.environ["no_proxy"] = os.environ["NO_PROXY"] = "127.0.0.1,localhost"

import rm_auth as auth
import rm_benchmark as bench
import rm_config as cfg
import rm_docs as docs
import rm_ingest as ing
import rm_report as report
import rm_ted as ted

PROJ = [("RM-2026-002", 2, "Budowa sieci wodociągowej w Zagrodnie", "Gmina Zagrodno", "6941551040", "dolnośląskie", "Zagrodno",
         "KPOD.03.15-IW.13-0004/25", "Numer projektu KPO"),
        ("RM-2026-005", 5, "Budowa sieci kanalizacji sanitarnej w miejscowości Zławieś Wielka i Zławieś Mała wraz z modernizacją stacji "
         "uzdatniania wody w m. Pędzewo", "Gmina Zławieś Wielka", "9562283430", "kujawsko-pomorskie", "Zławieś Wielka",
         "KPOD.03.15-IW.14-0032/25", "Numer projektu KPO")]


def seed(dbpath):
    db = ing.Db(dbpath)
    for r in PROJ:
        db.c.execute("INSERT OR REPLACE INTO projekty(id,lp,nazwa,beneficjent,nip,wojewodztwo,miejscowosc,id_publiczny,rodzaj_id,pobrano) "
                     "VALUES(?,?,?,?,?,?,?,?,?,'2026-11-21')", r)
    db.c.execute("INSERT INTO powiazania VALUES('RM-2026-005','ocds-test-1','dopasowanie rozmyte (NIP + nazwa)',0.95,'niepewne','NIP zgodny','x')")
    db.c.commit()
    return db


class TestAuth(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(":memory:")
        auth.setup(self.c)

    def test_password_and_session(self):
        auth.add_user(self.c, "Jan.Kowalski", "bardzo-tajne-1", "zatwierdzajacy", "Jan Kowalski")
        self.assertIsNone(auth.check_password(self.c, "jan.kowalski", "zle-haslo"))
        u = auth.check_password(self.c, "JAN.KOWALSKI", "bardzo-tajne-1")
        self.assertEqual(u["nazwa"], "Jan Kowalski")
        tok = auth.new_session(self.c, u["login"])
        self.assertEqual(auth.session_user(self.c, tok)["rola"], "zatwierdzajacy")
        stored = self.c.execute("SELECT token FROM sesje").fetchone()[0]
        self.assertNotEqual(stored, tok, "w bazie ma być tylko skrót tokenu")
        auth.end_session(self.c, tok)
        self.assertIsNone(auth.session_user(self.c, tok))

    def test_hash_is_not_plaintext_and_short_password_rejected(self):
        auth.add_user(self.c, "a", "12345678")
        self.assertNotIn("12345678", str(self.c.execute("SELECT * FROM uzytkownicy").fetchall()))
        with self.assertRaises(ValueError):
            auth.add_user(self.c, "b", "krotkie")

    def test_roles(self):
        self.assertTrue(auth.allowed({"rola": "zatwierdzajacy"}, "analityk"))
        self.assertFalse(auth.allowed({"rola": "analityk"}, "zatwierdzajacy"))
        self.assertFalse(auth.allowed(None, "analityk"))
        self.assertTrue(auth.allowed(None, None))

    def test_rate_limit(self):
        auth.add_user(self.c, "x", "12345678")
        for _ in range(8):
            auth.check_password(self.c, "x", "zle", "1.2.3.4")
        self.assertTrue(auth.too_many_failures(self.c, "x", "1.2.3.4"))


class TestConfig(unittest.TestCase):
    def test_subsector_rules_from_file_match_builtin(self):
        names = ["Budowa oczyszczalni ścieków", "Kanalizacja deszczowa i zbiornik retencyjny", "Modernizacja SUW w Pędzewie",
                 "Budowa kanalizacji sanitarnej z tłocznią", "Rozbudowa sieci wodociągowej", "Przebudowa drogi gminnej"]
        expected = ["Oczyszczalnia ścieków", "Kanalizacja deszczowa i retencja", "Uzdatnianie i zaopatrzenie w wodę",
                    "Kanalizacja sanitarna", "Sieć wodociągowa", "Inne obiekty wod-kan"]
        self.assertEqual([bench.subsector(n) for n in names], expected)

    def test_weights_normalized_and_validated(self):
        c = sqlite3.connect(":memory:")
        self.assertAlmostEqual(sum(cfg.weights(c).values()), 1.0)
        cfg.put(c, "wagi", {"tekst": 2, "wartosc": 2, "region": 0, "formula": 0}, "test")
        self.assertAlmostEqual(cfg.weights(c)["tekst"], .5)
        with self.assertRaises(ValueError):
            cfg.validate_weights({"tekst": 0, "wartosc": 0, "region": 0, "formula": 0})
        with self.assertRaises(ValueError):
            cfg.validate_weights({"tekst": "abc"})


class TestReport(unittest.TestCase):
    def test_markdown_to_html(self):
        h = report.md_to_html("# Tytuł\n\n| A | B |\n|---|---|\n| **1** | [x](https://e.pl) |\n\n- punkt<br>dalej\n\n<script>alert(1)</script>")
        self.assertIn("<h1>Tytuł</h1>", h)
        self.assertIn("<td><b>1</b></td>", h)
        self.assertIn('<a href="https://e.pl">x</a>', h)
        self.assertIn("<br>", h)
        self.assertNotIn("<script>", h)


class TestTed(unittest.TestCase):
    def test_fetch_and_link(self):
        import fake_ted
        srv = fake_ted.start()
        os.environ["RM_TED_URL"] = f"http://127.0.0.1:{srv.server_address[1]}/v3/notices/search"
        try:
            with tempfile.TemporaryDirectory() as d:
                db = ing.Db(Path(d) / "t.db")
                db.put("projekty", {"id": "RM-1", "lp": 1, "nazwa": "Kanalizacja", "beneficjent": "Gmina Czernikowo", "nip": "8792632155",
                                    "wojewodztwo": "kujawsko-pomorskie", "miejscowosc": "Czernikowo", "id_publiczny": "x", "rodzaj_id": "BZP",
                                    "adnotacja": None, "pobrano": "x"})
                db.c.commit()
                r = ted.run(db, say=lambda *a: None)
                self.assertTrue(r["ok"])
                self.assertEqual(r["ogloszenia"], 3)
                row = db.c.execute("SELECT projekt, pewnosc, status, wartosc FROM ted WHERE numer='123456-2024'").fetchone()
                self.assertEqual(row, ("RM-1", .95, "poprawne", 12500000.0))
                self.assertIn("buyer-country=POL", fake_ted.SEEN[0]["query"])
                self.assertEqual(len(ted.for_project(db, "RM-1")), 1)
                self.assertEqual(ted.stats(db)["zapytania"], 2)
        finally:
            srv.shutdown()
            os.environ.pop("RM_TED_URL", None)

    def test_offline_does_not_crash(self):
        os.environ["RM_TED_URL"] = "http://127.0.0.1:9/v3/notices/search"
        try:
            with tempfile.TemporaryDirectory() as d:
                db = ing.Db(Path(d) / "t.db")
                r = ted.run(db, say=lambda *a: None)
                self.assertFalse(r["ok"])
                self.assertIsNotNone(ted.stats(db)["blad"])
        finally:
            os.environ.pop("RM_TED_URL", None)


class TestMistral(unittest.TestCase):

    def setUp(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        self.seen, self.plan = [], []
        test = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                test.seen.append((self.path, self.headers.get("Authorization"), json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
                code = test.plan.pop(0) if test.plan else 200
                b = json.dumps({"choices": [{"message": {"content": '{"ok": true, "tekst": "Wyjaśnienie", "cytat": null}'}}]} if code == 200
                               else {"message": "err"}).encode()
                self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b)))
                self.end_headers(); self.wfile.write(b)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.env = {k: os.environ.get(k) for k in ("LLM_PROVIDER", "MISTRAL_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY")}
        os.environ.update({"LLM_PROVIDER": "mistral", "MISTRAL_API_KEY": "klucz-testowy",
                           "LLM_BASE_URL": f"http://127.0.0.1:{self.srv.server_address[1]}/v1"})
        os.environ.pop("LLM_MODEL", None)
        os.environ.pop("LLM_API_KEY", None)

    def tearDown(self):
        self.srv.shutdown()
        for k, v in self.env.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

    def test_request_format(self):
        import rm_llm as llm
        c = llm.config()
        self.assertEqual((c["provider"], c["model"], c["where"]), ("mistral", "mistral-small-latest", "lokalnie – dane nie opuszczają komputera"))
        out = llm.explain({"t": "Kanalizacja", "n": "Gmina X", "events": []})
        self.assertEqual(out["tekst"], "Wyjaśnienie")
        path, auth_h, body = self.seen[0]
        self.assertEqual(path, "/v1/chat/completions")
        self.assertEqual(auth_h, "Bearer klucz-testowy")
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertEqual((body["temperature"], body["random_seed"]), (0, 42))

    def test_retry_on_429_and_clear_401(self):
        import rm_llm as llm
        self.plan[:] = [429, 200]
        self.assertIn("ok", llm.chat("s", "u"))
        self.assertEqual(len(self.seen), 2)
        self.plan[:] = [401]
        with self.assertRaises(RuntimeError) as e:
            llm.chat("s", "u")
        self.assertIn("MISTRAL_API_KEY", str(e.exception))


class TestDocuments(unittest.TestCase):
    def test_reanalysis_confirms_and_closes(self):
        with tempfile.TemporaryDirectory() as d:
            db = seed(Path(d) / "t.db")
            db.c.executescript(docs.SCHEMA)
            first = docs.process(db, ROOT / "dokumenty" / "Risk_Miner_protokol_koniecznosci_zlawies.docx", say=lambda *a: None)
            f2 = Path(d) / "odbior.txt"
            f2.write_text("Zadanie: „Budowa sieci kanalizacji sanitarnej w miejscowości Zławieś Wielka i Zławieś Mała wraz z modernizacją "
                          "stacji uzdatniania wody w m. Pędzewo”\nUmowa nr 272.4.27.1.0.2025 z dnia 14.02.2025 r.\nWykonawca: WIMAX Sp. z o.o.\n"
                          "Zamawiający: Gmina Zławieś Wielka\nPROTOKÓŁ ODBIORU KOŃCOWEGO. Przedmiot umowy wykonano w całości.", encoding="utf-8")
            second = docs.process(db, f2, say=lambda *a: None)
            lst = {x["id"]: x for x in docs.listing(db)}
            self.assertEqual(lst[first["id"]]["projekt"], "RM-2026-005")
            st = {(r["rodzaj"], r["element"]): r["status"] for r in lst[second["id"]]["roznice"]}
            self.assertEqual(st[("fakt", "numer umowy")], "potwierdzony")
            self.assertEqual(st[("ryzyko", "R07")], "zamknięte")


class TestServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import server
        cls.server = server
        cls.tmp = tempfile.mkdtemp()
        seed(Path(cls.tmp) / "s.db").c.close()
        os.environ["RM_DEMO_PASSWORD"] = "test-haslo-123"

        class A:
            db = str(Path(cls.tmp) / "s.db"); out = str(Path(cls.tmp) / "out"); url = "http://127.0.0.1:9"; xlsx = "brak.xlsx"; host = "127.0.0.1"
        cls.app = server.App(A())
        auth.bootstrap(cls.app.db.c, Path(cls.tmp))
        cls.srv = server.ThreadingHTTPServer(("127.0.0.1", 0), server.handler(cls.app))
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def req(self, path, data=None, opener=None, headers=None):
        body = json.dumps(data).encode() if data is not None else None
        r = urllib.request.Request(self.base + path, data=body, headers={"Content-Type": "application/json", **(headers or {})})
        o = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with o.open(r, timeout=20) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if resp.headers.get_content_type() == "application/json" else raw)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def login(self, who="ekspert"):
        jar = http.cookiejar.CookieJar()
        o = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(jar))
        code, j = self.req("/api/login", {"login": who, "haslo": "test-haslo-123"}, o)
        self.assertEqual(code, 200, j)
        return o

    def test_pages(self):
        for p, marker in (("/", b"BidRadar"), ("/app", b'id="addon-start"'), ("/login", b"Zaloguj"), ("/healthz", None)):
            code, body = self.req(p)
            self.assertEqual(code, 200, p)
            if marker:
                self.assertIn(marker, body)

    def test_static_logo_and_titles(self):
        for path in ("/static/logo.png", "/static/favicon.png", "/favicon.ico"):
            code, body = self.req(path)
            self.assertEqual(code, 200, path)
            self.assertTrue(body.startswith(b"\x89PNG"), path)
        self.assertEqual(self.req("/static/../server.py")[0], 404)
        for name in ("landing.html", "login.html", "bidradar_ui.html"):
            s = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn("<title>BidRadar — ", s)
            self.assertNotIn("data:image/png;base64", s)

    def test_read_without_login(self):
        for p in ("/api/projects", "/api/queue", "/api/sources", "/api/settings", "/api/monitor", "/api/ted", "/api/decisions", "/api/docs"):
            self.assertEqual(self.req(p)[0], 200, p)
        code, j = self.req("/api/export/db")
        self.assertEqual(code, 200)
        self.assertIn("projekty", j)
        self.assertNotIn("uzytkownicy", j)

    def test_links_report(self):
        code, rows = self.req("/api/export/links?fmt=json")
        self.assertEqual(code, 200)
        self.assertTrue(any(r["projekt"] == "RM-2026-005" and r["zrodlo"] == "BZP" for r in rows))
        code, raw = self.req("/api/export/links")
        self.assertEqual(code, 200)
        self.assertIn("podstawa", raw.decode("utf-8-sig").splitlines()[0])

    def test_write_requires_login_and_role(self):
        d = {"akcja": "Zatwierdzono", "co": "x", "typ": "kolejka"}
        self.assertEqual(self.req("/api/decision", d)[0], 401)
        self.assertEqual(self.req("/api/decision", d, self.login("analityk"))[0], 403)
        o = self.login()
        code, j = self.req("/api/decision", d, o)
        self.assertEqual((code, j["ekspert"]), (200, "Ekspert zatwierdzający"))
        self.assertEqual(self.req("/api/decision", d, o, {"Origin": "http://zla-strona.example"})[0], 403)

    def test_bad_json_and_bad_password(self):
        r = urllib.request.Request(self.base + "/api/analyze", data=b"{zly", headers={"Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.build_opener(urllib.request.ProxyHandler({})).open(r)
        self.assertEqual(e.exception.code, 400)
        self.assertEqual(self.req("/api/login", {"login": "ekspert", "haslo": "zle"})[0], 401)

    def test_link_decision_updates_status(self):
        o = self.login()
        self.req("/api/decision", {"akcja": "Zatwierdzono", "co": "dopasowanie", "typ": "kolejka", "projekt": "RM-2026-005", "ocid": "ocds-test-1"}, o)
        st = self.app.db.c.execute("SELECT status FROM powiazania WHERE projekt='RM-2026-005'").fetchone()[0]
        self.assertEqual(st, "zatwierdzone")

    def test_settings_roundtrip(self):
        o = self.login()
        code, _ = self.req("/api/settings", {"klucz": "wagi", "wartosc": {"tekst": .7, "wartosc": .1, "region": .1, "formula": .1}}, o)
        self.assertEqual(code, 200)
        self.assertEqual(self.req("/api/settings")[1]["wagi"]["tekst"], .7)
        self.assertEqual(self.req("/api/settings", {"klucz": "katalog", "wartosc": [{"c": "zly"}]}, o)[0], 400)
        code, _ = self.req("/api/settings", {"klucz": "katalog", "wartosc": [{"c": "R16", "n": "Nowa kategoria", "e": "—", "on": True}], "cicho": True}, o)
        self.assertEqual(code, 200)
        self.assertEqual(self.req("/api/settings")[1]["katalog"][0]["c"], "R16")

    def test_monitor_creates_alert_and_queue_item(self):
        app, c = self.app, self.app.db.c

        def fake_refresh(with_ted=True):
            c.execute("INSERT OR REPLACE INTO ogloszenia VALUES('2026/BZP 00999999/01','AgreementUpdateNotice','ocds-test-1','2026-11-20',"
                      "'x','https://ezamowienia.gov.pl/x','now')")
            c.execute("INSERT INTO zmiany VALUES('ocds-test-1','2026/BZP 00999999/01','art. 455 ust. 1 pkt 4 Pzp','zmiana terminu')")
            c.commit()
            return {"ok": True}
        orig, app.refresh = app.refresh, fake_refresh
        try:
            r = app.run_monitor()
        finally:
            app.refresh = orig
        self.assertEqual(r["nowe"], 1)
        a = c.execute("SELECT projekt, istotne, status FROM alerty").fetchone()
        self.assertEqual(a, ("RM-2026-005", 1, "nowy"))
        q = [x for x in self.req("/api/queue")[1] if x.get("typ") == "alert"]
        self.assertTrue(q)
        o = self.login()
        self.req("/api/decision", {"akcja": "Zatwierdzono", "co": "alert", "typ": "alert", "kategoria": str(q[0]["alert"])}, o)
        self.assertEqual(c.execute("SELECT status FROM alerty WHERE id=?", (q[0]["alert"],)).fetchone()[0], "przejrzany")

    def test_upload_requires_login_and_reports_diff(self):
        f = ROOT / "dokumenty" / "Risk_Miner_protokol_koniecznosci_zlawies.docx"
        body = {"name": f.name, "b64": base64.b64encode(f.read_bytes()).decode()}
        self.assertEqual(self.req("/api/docs/upload", body)[0], 401)
        code, j = self.req("/api/docs/upload", body, self.login("analityk"))
        self.assertEqual(code, 200)
        self.assertTrue(j["ok"])
        self.assertEqual(j["dokument"]["projekt"], "RM-2026-005")
        self.assertIn("roznice", j["dokument"])


    def test_openapi_and_api_page(self):
        code, j = self.req("/api/openapi.json")
        self.assertEqual(code, 200)
        self.assertEqual(j["openapi"][:3], "3.0")
        self.assertIn("post", j["paths"]["/api/decision/batch"])
        self.assertIn("security", j["paths"]["/api/decision"]["post"])
        code, html = self.req("/api")
        self.assertEqual(code, 200)
        self.assertIn(b"/api/openapi.json", html)

    def test_batch_approve_only_confident_with_role(self):
        c = self.app.db.c
        c.execute("INSERT INTO powiazania VALUES('RM-2026-002','ocds-test-2','dopasowanie rozmyte (NIP + nazwa)',0.96,'niepewne','NIP zgodny','x')")
        c.execute("INSERT INTO powiazania VALUES('RM-2026-002','ocds-test-3','dopasowanie rozmyte (NIP + nazwa)',0.70,'niepewne','słabszy kandydat','x')")
        c.commit()
        self.assertEqual(self.req("/api/decision/batch", {})[0], 401)
        self.assertEqual(self.req("/api/decision/batch", {}, self.login("analityk"))[0], 403)
        o = self.login()
        self.assertEqual(self.req("/api/decision/batch", {"min_pewnosc": 3}, o)[0], 400)
        code, j = self.req("/api/decision/batch", {"min_pewnosc": .9, "podglad": True}, o)
        self.assertTrue(any(x["ocid"] == "ocds-test-2" for x in j["polaczenia"]))
        self.assertEqual(c.execute("SELECT status FROM powiazania WHERE ocid='ocds-test-2'").fetchone()[0], "niepewne", "podgląd niczego nie zmienia")
        code, j = self.req("/api/decision/batch", {"min_pewnosc": .9}, o)
        self.assertEqual(code, 200)
        self.assertIn("ocds-test-2", [x["ocid"] for x in j["polaczenia"]])
        self.assertEqual(c.execute("SELECT status FROM powiazania WHERE ocid='ocds-test-2'").fetchone()[0], "zatwierdzone")
        self.assertEqual(c.execute("SELECT status FROM powiazania WHERE ocid='ocds-test-3'").fetchone()[0], "niepewne")
        d = c.execute("SELECT ekspert,komentarz FROM decyzje WHERE ocid='ocds-test-2'").fetchone()
        self.assertEqual(d[0], "Ekspert zatwierdzający")
        self.assertIn("zbiorcze", d[1])

    def test_analyses_history_and_ai_test(self):
        code, j = self.req("/api/analyses")
        self.assertEqual(code, 200)
        self.assertIn("analizy", j)
        self.assertEqual(self.req("/api/analyses?id=999999")[0], 404)
        self.assertEqual(self.req("/api/ai/test", {})[0], 401)
        code, j = self.req("/api/ai/test", {}, self.login("analityk"))
        self.assertEqual(code, 200)
        self.assertIn("ok", j)

    def test_record_export_docx(self):
        import zipfile, io
        code, raw = self.req("/api/export/record?id=RM-2026-005&fmt=docx")
        self.assertEqual(code, 200)
        z = zipfile.ZipFile(io.BytesIO(raw))
        self.assertIn("word/document.xml", z.namelist())
        self.assertIn("RM-2026-005", z.read("word/document.xml").decode())


    def _seed_event(self):
        c = self.app.db.c
        c.execute("INSERT OR REPLACE INTO ogloszenia VALUES('2025/BZP 00000009/01','ContractPerformingNotice','ocds-test-1','2025-06-01','x','https://e.pl/9','x')")
        c.execute("DELETE FROM umowy WHERE ocid='ocds-test-1'")
        c.execute("INSERT INTO umowy(ocid,ogloszenie,czesc,data_zawarcia,wartosc,wyplacono,termin_wykonania,w_terminie,termin_planowany) "
                  "VALUES('ocds-test-1','2025/BZP 00000009/01',1,'2024-01-01',1000000,1100000,'2025-05-01','nie','2025-03-01')")
        c.execute("DELETE FROM zdarzenia WHERE projekt='RM-2026-005'")
        c.execute("INSERT INTO zdarzenia(projekt,ocid,kod,opis,ogloszenie,pole,cytat) VALUES('RM-2026-005','ocds-test-1','R06','Umowa wykonana po terminie',"
                  "'2025/BZP 00000009/01','5.3','nie')")
        c.commit()
        return "RM-2026-005|2025/BZP 00000009/01|R06|5.3"

    def test_event_edit_with_role_validation_and_history(self):
        k = self._seed_event()
        e = next(x for x in self.req("/api/projects")[1] if x["rid"] == "RM-2026-005")["events"][0]
        self.assertEqual(e["skutek_dni"]["wartosc"], 61)
        self.assertEqual(e["skutek_zl"]["wartosc"], 100000.0)
        self.assertEqual(self.req("/api/event", {"klucz": k, "pola": {"dzialanie": "x"}})[0], 401)
        o = self.login()
        self.assertEqual(self.req("/api/event", {"klucz": k, "pola": {"skutecznosc": "świetne"}}, o)[0], 400)
        self.assertEqual(self.req("/api/event", {"klucz": k, "pola": {"skutek_dni": "abc"}}, o)[0], 400)
        code, j = self.req("/api/event", {"klucz": k, "pola": {"dzialanie": "kary umowne", "skutecznosc": "częściowo skuteczne",
                                                              "przyczyna": "kolizja z siecią gazową", "status": "zatwierdzone"}, "komentarz": "protokół"}, o)
        self.assertEqual(code, 200, j)
        self.assertEqual(j["zdarzenie"]["skutecznosc"]["wartosc"], "częściowo skuteczne")
        self.assertTrue(j["zdarzenie"]["skutecznosc"]["zrodlo"].startswith("ekspert"))
        self.assertEqual(self.app.db.c.execute("SELECT status FROM zdarzenia WHERE projekt='RM-2026-005'").fetchone()[0], "zatwierdzone")
        d = self.app.db.c.execute("SELECT akcja,co,ekspert FROM decyzje WHERE typ='zdarzenie' ORDER BY rowid DESC").fetchone()
        self.assertEqual(d[0], "Zatwierdzono zdarzenie")
        self.assertIn("kary umowne", d[1])
        c = self.app.db.c
        c.execute("DELETE FROM zdarzenia WHERE projekt='RM-2026-005'")
        c.execute("INSERT INTO zdarzenia(projekt,ocid,kod,opis,ogloszenie,pole,cytat) VALUES('RM-2026-005','ocds-test-1','R06','Umowa wykonana po terminie',"
                  "'2025/BZP 00000009/01','5.3','nie')")
        ing.apply_event_fixes(self.app.db, "RM-2026-005")
        c.commit()
        e = ing.events_detail(self.app.db, "RM-2026-005")[0]
        self.assertEqual((e["dzialanie"]["wartosc"], e["status"]), ("kary umowne", "zatwierdzone"))
        code, raw = self.req("/api/export/record?id=RM-2026-005&fmt=md")
        self.assertEqual(code, 200, raw)
        self.assertIn("kary umowne", raw.decode())

    def test_project_edit_validates_before_saving(self):
        o = self.login()
        before = self.app.db.c.execute("SELECT adnotacja FROM projekty WHERE id='RM-2026-002'").fetchone()[0]
        code, j = self.req("/api/project", {"id": "RM-2026-002", "pola": {"adnotacja": "nowa", "nip": "123"}}, o)
        self.assertEqual(code, 400)
        self.assertEqual(self.app.db.c.execute("SELECT adnotacja FROM projekty WHERE id='RM-2026-002'").fetchone()[0], before, "bez połowicznych zmian")
        code, j = self.req("/api/project", {"id": "RM-2026-002", "pola": {"adnotacja": "sprawdzone w BIP"}, "komentarz": "t"}, o)
        self.assertEqual((code, j["zmiany"]), (200, 1))
        rec = ing.apply_project_fix(self.app.db, "RM-2026-002", {"Adnotacja instytucji": "z arkusza", "Nazwa odbiorcy": "Gmina Zagrodno"})
        self.assertEqual(rec["Adnotacja instytucji"], "sprawdzone w BIP", "poprawka ma pierwszeństwo przed arkuszem")
        self.assertEqual(self.req("/api/project", {"id": "RM-2026-002", "pola": {"adnotacja": "sprawdzone w BIP 2"}}, o)[0], 200)

    def test_link_change_needs_bzp_and_role(self):
        self.assertEqual(self.req("/api/link/change", {"projekt": "RM-2026-002", "cel": "2023/BZP 00129088/01"})[0], 401)
        o = self.login()
        code, j = self.req("/api/link/change", {"projekt": "RM-2026-002", "cel": "2023/BZP 00129088/01"}, o)
        self.assertEqual(code, 503)
        self.assertIn("BZP", j["blad"])

    def test_subsector_rules_edit_and_check(self):
        f = cfg.DIR / "podsektory.json"
        orig = f.read_text(encoding="utf-8")
        try:
            o = self.login()
            bad = {"klucz": "podsektory", "wartosc": {"reguly": [{"nazwa": "X", "wzorce": ["(zly"]}]}}
            self.assertEqual(self.req("/api/settings", bad, o)[0], 400)
            rules = json.loads(orig)["reguly"] + [{"nazwa": "Drogi", "wzorce": ["drog", "jezdni"]}]
            code, j = self.req("/api/settings", {"klucz": "podsektory", "wartosc": {"reguly": rules}}, o)
            self.assertEqual(code, 200, j)
            self.assertEqual(self.req("/api/subsector?nazwa=" + urllib.parse.quote("Przebudowa drogi gminnej"))[1]["podsektor"], "Drogi")
            self.assertIn("sektor", json.loads(f.read_text(encoding="utf-8")), "pozostałe pola pliku zostają")
        finally:
            f.write_text(orig, encoding="utf-8")
            cfg.reload()


class TestPages(unittest.TestCase):
    def test_landing_and_login_are_self_contained(self):
        for name in ("landing.html", "login.html"):
            s = (ROOT / name).read_text(encoding="utf-8")
            self.assertNotIn("{{", s)
            self.assertNotIn("https://fonts.googleapis", s)
        self.assertIn('expert: "/login?next=/app"', (ROOT / "landing.html").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestDocx(unittest.TestCase):
    def test_markdown_to_valid_docx(self):
        import zipfile
        import xml.dom.minidom
        import rm_docx
        tmp = Path(tempfile.mkdtemp())
        f = rm_docx.md_to_docx("# Tytuł & <test>\n\n## Sekcja\n\n- punkt **ważny**\n\n| A | B |\n|---|---|\n| 1 \\| 2 | [BZP](https://ezamowienia.gov.pl/a?b=1&c=2) |\n\n> cytat",
                               tmp / "t.docx", "Test")
        z = zipfile.ZipFile(f)
        for n in z.namelist():
            if n.endswith(".xml") or n.endswith(".rels"):
                xml.dom.minidom.parseString(z.read(n))
        doc = z.read("word/document.xml").decode()
        self.assertIn("Tytuł &amp; &lt;test&gt;", doc)
        self.assertIn("<w:tbl>", doc)
        self.assertIn("1 | 2", doc)
        self.assertIn('TargetMode="External"', z.read("word/_rels/document.xml.rels").decode())
        shutil.rmtree(tmp, ignore_errors=True)


class TestLlmPrivacy(unittest.TestCase):
    def test_mask_roundtrip_and_policy(self):
        import rm_llm as llm
        src = "Wójt: jan.nowak@gmina.pl, tel. 600 100 200, PESEL 85010112345, NIP 794-17-37-421. Koszt 12 345 678 zł, 30 miesięcy, 12,5 km."
        m, back = llm.mask(src)
        for v in ("jan.nowak@gmina.pl", "600 100 200", "85010112345", "794-17-37-421"):
            self.assertNotIn(v, m)
        for v in ("12 345 678 zł", "30 miesięcy", "12,5 km"):
            self.assertIn(v, m, "kwoty i parametry techniczne zostają")
        self.assertEqual(llm.unmask(m, back), src)
        old = os.environ.pop("RM_LLM_MASKA", None)
        try:
            self.assertTrue(llm.must_mask({"where": "API zewnętrzne"}))
            self.assertFalse(llm.must_mask({"where": "UE"}))
            os.environ["RM_LLM_MASKA"] = "1"
            self.assertTrue(llm.must_mask({"where": "UE"}))
        finally:
            os.environ.pop("RM_LLM_MASKA", None)
            if old is not None:
                os.environ["RM_LLM_MASKA"] = old


class TestGoldStandard(unittest.TestCase):
    def test_gold_examples_first_and_scored(self):
        import rm_invest as inv
        tmp = Path(tempfile.mkdtemp())
        db = seed(tmp / "g.db")
        db.c.execute("INSERT INTO zdarzenia VALUES('RM-2026-002','o1','R07','wzrost kosztu o 18%','N1','5.5','cyt','propozycja – do zatwierdzenia')")
        db.c.execute("INSERT INTO zdarzenia VALUES('RM-2026-005','o2','R07','wzrost kosztu o 9%','N2','5.5','cyt','propozycja – do zatwierdzenia')")
        db.c.execute("INSERT INTO decyzje VALUES('2026-11-21','Oznaczono jako wzorcowy','x','kolejka','RM-2026-005',NULL,NULL,NULL,'e')")
        db.c.commit()
        x = inv.experience(db, ["R07"], "Kanalizacja sanitarna")
        self.assertEqual(x["przyklady"][0]["projekt"], "RM-2026-005")
        self.assertTrue(x["przyklady"][0]["wzorcowy"])
        self.assertEqual(x["n_wzorcowych"], 1)
        md = "\n".join(inv.exp_md(x))
        self.assertIn("**wzorcowy**", md)
        self.assertEqual(inv.experience(db, [], None)["przyklady"], [])
        db.c.close()
        shutil.rmtree(tmp, ignore_errors=True)
