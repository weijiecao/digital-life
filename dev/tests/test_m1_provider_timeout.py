import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from v1_5_consolidator.gateway import OpenAIProvider


class _SlowHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        time.sleep(2)
        try:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{}')
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    def log_message(self, *args):
        pass


class TestProviderTimeout(unittest.TestCase):
    def test_slow_provider_times_out(self):
        server = HTTPServer(("127.0.0.1", 0), _SlowHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            provider = OpenAIProvider(
                "http://127.0.0.1:%d" % server.server_port,
                "mock", "key", timeout=0.2)
            with self.assertRaises(Exception):
                provider.generate(type("P", (), {"prompt": "hello"})())
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
