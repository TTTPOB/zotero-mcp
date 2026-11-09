"""Forward container port 23129 to Zotero's loopback-only local API.

Mozilla's httpd.js rejects requests whose Host header carries a port other than the
listener's, so a plain TCP forwarder is not enough: the Host header is rewritten.
"""

from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = ("127.0.0.1", 23119)


class Relay(BaseHTTPRequestHandler):
    def relay(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        headers = dict(self.headers)
        headers["Host"] = f"{UPSTREAM[0]}:{UPSTREAM[1]}"
        upstream = HTTPConnection(*UPSTREAM, timeout=120)
        try:
            upstream.request(self.command, self.path, body, headers)
            response = upstream.getresponse()
            payload = response.read()
            self.send_response(response.status)
            for name, value in response.getheaders():
                if name.lower() not in ("transfer-encoding", "connection", "content-length"):
                    self.send_header(name, value)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)
        except OSError as exc:
            # Expected until Zotero starts listening.
            self.send_error(502, str(exc))
        finally:
            upstream.close()

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_HEAD = relay


ThreadingHTTPServer(("0.0.0.0", 23129), Relay).serve_forever()
