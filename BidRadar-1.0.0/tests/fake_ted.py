import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

NOTICES = [
    {"publication-number": "123456-2024", "notice-title": {"pol": "Budowa kanalizacji sanitarnej w Czernikowie"},
     "buyer-name": {"pol": ["Gmina Czernikowo"]}, "buyer-identifier": ["PL 879-26-32-155"], "publication-date": "2024-03-05+01:00",
     "notice-type": "can-standard", "classification-cpv": ["45232400"], "total-value": "12 500 000.00", "winner-name": {"pol": ["Hydro-Bud Sp. z o.o."]},
     "links": {"html": {"POL": "https://ted.europa.eu/pl/notice/-/detail/123456-2024"}}},
    {"publication-number": "222222-2025", "notice-title": {"pol": "Przebudowa oczyszczalni ścieków"},
     "buyer-name": {"pol": ["Gmina Oława"]}, "buyer-identifier": ["PL 0000000000"], "publication-date": "2025-01-10",
     "notice-type": "cn-standard", "classification-cpv": ["45232420"], "links": {}},
    {"publication-number": "333333-2025", "notice-title": {"eng": "Water supply network"}, "buyer-name": {"pol": ["Miasto Gdańsk"]},
     "publication-date": "2025-05-01", "notice-type": "cn-standard"},
]
SEEN = []


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SEEN.append(body)
        if "buyer-city" in body.get("fields", []):
            self.send_response(400); self.end_headers(); self.wfile.write(b'{"message":"unknown field"}'); return
        tok = body.get("iterationNextToken")
        page = NOTICES[:2] if not tok else NOTICES[2:]
        out = {"notices": page, "totalNoticeCount": len(NOTICES), "iterationNextToken": None if tok else "t1"}
        b = json.dumps(out).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b)))
        self.end_headers(); self.wfile.write(b)


def start(port=0):
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
