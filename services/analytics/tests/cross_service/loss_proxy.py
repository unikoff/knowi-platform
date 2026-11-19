"""Drop exactly one successful transfer reply after its upstream HTTP commit."""
import http.client
from http.server import BaseHTTPRequestHandler, HTTPServer
import os
import socket

PORT = int(os.environ["CROSS_SERVICE_PROXY_PORT"])
UPSTREAM = int(os.environ.get("CROSS_SERVICE_PROXY_UPSTREAM_PORT", "18152"))
MODE = os.environ.get("CROSS_SERVICE_PROXY_MODE", "transfer")


class Proxy(BaseHTTPRequestHandler):
    dropped = False

    def log_message(self, *args):
        pass

    def forward(self):
        if MODE == "transfer" and not self.path.startswith("/internal/core-transfer/v1/"):
            self.send_error(404); return
        size = int(self.headers.get("Content-Length", "0"))
        if not 0 <= size <= (2 * 1024 * 1024 if MODE == "catalog" else 8192):
            self.send_error(413); return
        body = self.rfile.read(size)
        conn = http.client.HTTPConnection("127.0.0.1", UPSTREAM, timeout=3)
        try:
            headers = {key: self.headers[key] for key in ("Authorization", "Content-Type") if key in self.headers}
            conn.request(self.command, self.path, body=body, headers=headers)
            result = conn.getresponse()
            data = result.read(8193)
            if len(data) > 8192:
                self.send_error(502); return
            target = self.path == "/internal/analytics/catalog/v1" if MODE == "catalog" else True
            if self.command == "POST" and target and result.status == 200 and not type(self).dropped:
                type(self).dropped = True
                marker = os.environ.get("CROSS_SERVICE_PROXY_MARKER")
                if marker:
                    with open(marker, "x", encoding="ascii") as output:
                        output.write("upstream_committed_reply_dropped")
                self.close_connection = True
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                return
            self.send_response(result.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        finally:
            conn.close()

    do_GET = forward
    do_POST = forward


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", PORT), Proxy).serve_forever(poll_interval=.1)
