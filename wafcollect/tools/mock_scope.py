"""Mock Scope Service để phát triển C1-C3 độc lập khi P1 (Người 3) chưa xong.

    python -m wafcollect.tools.mock_scope --allow shop.example.com,blog.example.com --port 8081

    GET /scope/check?domain=shop.example.com  -> 200 {"domain":"shop.example.com","allowed":true}
"""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


def make_server(allowed: set[str], host: str = "0.0.0.0", port: int = 8081) -> ThreadingHTTPServer:
    class H(BaseHTTPRequestHandler):
        def _send(self, code: int, body: dict) -> None:
            raw = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:  # noqa: N802
            u = urlparse(self.path)
            if u.path == "/healthz":
                return self._send(200, {"status": "ok"})
            if u.path == "/scope/check":
                domain = (parse_qs(u.query).get("domain") or [""])[0]
                return self._send(200, {"domain": domain, "allowed": domain in allowed})
            self._send(404, {"error": "not found"})

        def log_message(self, *a) -> None:  # im lặng
            pass

    return ThreadingHTTPServer((host, port), H)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--allow", default="", help="danh sách domain được phép, cách nhau bằng dấu phẩy")
    ap.add_argument("--port", type=int, default=8081)
    args = ap.parse_args()
    allowed = {d.strip() for d in args.allow.split(",") if d.strip()}
    print(f"mock scope: allowed={sorted(allowed)} port={args.port}", flush=True)
    make_server(allowed, port=args.port).serve_forever()


if __name__ == "__main__":
    main()
