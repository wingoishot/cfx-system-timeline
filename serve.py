#!/usr/bin/env python3
import http.server, os, subprocess, sys

PORT = 8080
DIR = os.path.dirname(os.path.abspath(__file__))
SYNC = os.path.join(DIR, "sync.py")

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIR, **kwargs)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            subprocess.run([sys.executable, SYNC], cwd=DIR, capture_output=True)
        return super().do_GET()

if __name__ == "__main__":
    subprocess.run([sys.executable, SYNC], cwd=DIR)
    print(f"Serving at http://localhost:{PORT}")
    http.server.HTTPServer(("", PORT), Handler).serve_forever()
