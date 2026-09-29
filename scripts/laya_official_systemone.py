"""Serve the official Laya package over the System One protocol the agent already speaks.

The MLX bridge in ``laya_systemone.py`` runs a port of the same model through MLX. This one runs
the upstream package (``pip install laya``), whose Router picks a checkpoint per request and whose
checkpoints are published separately, so the two can be compared on the same request:

    uv pip install --python ~/venvs/laya-official/bin/python laya
    ~/venvs/laya-official/bin/python scripts/laya_official_systemone.py --port 8769

    curl -s localhost:8769/health
    TYPESAFE_BASE_URL=http://127.0.0.1:8769/v1/systemone uv run jev

The request and the response keep the shape the agent sends and expects: a state with the page, the
indexed elements and recent actions, questions of type ``choice``, and answers carrying the choice,
its probabilities and a confidence.
"""

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROUTER = None
MODEL = "multilingual"
STATS = {"requests": 0, "errors": 0}


def answer(body):
    """One System One request, answered by the official Router."""
    started = time.perf_counter()
    result = ROUTER.predict(body["state"], body["questions"], model=MODEL)
    result["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
    STATS["requests"] += 1
    return result


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        print(f"[laya-official] {self.address_string()} {fmt % args}", flush=True)

    def send(self, status, payload, mime="application/json"):
        content = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        if self.path.startswith("/health"):
            self.send(200, {"status": "ok", "service": "laya-official-systemone", "model": MODEL, **STATS})
        else:
            self.send(404, {"error": "Not found"})

    def do_POST(self):
        if not self.path.startswith("/v1/systemone"):
            self.send(404, {"error": "Not found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            self.send(200, answer(body))
        except Exception as error:  # a bad request must not take the service down
            STATS["errors"] += 1
            self.send(400, {"error": {"type": type(error).__name__, "message": str(error)}})


def main():
    global ROUTER, MODEL
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8769)
    parser.add_argument("--model", default="multilingual", help="checkpoint to name, or 'router' to let it decide")
    parser.add_argument("--no-preload", action="store_true", help="load checkpoints on first use instead")
    args = parser.parse_args()

    from laya import Router

    MODEL = args.model
    ROUTER = Router(preload=not args.no_preload)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"laya-official System One on http://{args.host}:{args.port} (model={MODEL})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
