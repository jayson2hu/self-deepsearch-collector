"""Loopback-only preview of the collection demo; explicit file allowlist."""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1] / "docs/collection-demo"
FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/dataset.json": ("dataset.json", "application/json; charset=utf-8"),
    "/candidates.jsonl": ("candidates.jsonl", "application/x-ndjson; charset=utf-8"),
}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_HEAD(self):
        self.respond(head=True)

    def do_GET(self):
        self.respond(head=False)

    def respond(self, *, head):
        route = urlsplit(self.path).path
        if route == "/healthz":
            body, content_type = b"ok\n", "text/plain; charset=utf-8"
        elif route == "/robots.txt":
            body, content_type = b"User-agent: *\nDisallow: /\n", "text/plain; charset=utf-8"
        elif route in FILES:
            filename, content_type = FILES[route]
            try:
                body = (ROOT / filename).read_bytes()
            except OSError:
                self.send_error(503, "Build the preview first")
                return
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Robots-Tag", "noindex, nofollow, noarchive")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def log_message(self, *_args):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=13003)
    args = parser.parse_args()
    if not (ROOT / "index.html").is_file():
        parser.error("Run npm run build:collection-demo first")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Collection demo: http://127.0.0.1:{server.server_port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
