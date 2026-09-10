#!/usr/bin/env python3
"""Local caching proxy for Asana API. Serves static files and proxies
Asana requests with server-side pagination and TTL-based caching.

Usage:
    python serve.py              # starts on port 8080
    python serve.py 9000         # starts on port 9000

The PAT is read from ASANA_PAT env var or .asana_pat file.
"""
import http.server, json, os, sys, time, urllib.request, urllib.parse, threading, subprocess
from urllib.error import HTTPError

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_TTL = 300  # seconds

# --- PAT ---
def get_pat():
    pat = os.environ.get("ASANA_PAT", "").strip()
    if pat:
        return pat
    pat_file = os.path.join(DIR, ".asana_pat")
    if os.path.exists(pat_file):
        with open(pat_file) as f:
            return f.read().strip()
    return ""

PAT = get_pat()

# --- Cache ---
_cache = {}
_cache_lock = threading.Lock()

def cache_get(key):
    with _cache_lock:
        entry = _cache.get(key)
        if entry and time.time() - entry["ts"] < CACHE_TTL:
            return entry["data"]
    return None

def cache_set(key, data):
    with _cache_lock:
        _cache[key] = {"data": data, "ts": time.time()}

# --- Asana fetch with pagination ---
def asana_fetch_all(path, query_params):
    """Fetch all pages from Asana and return combined data array."""
    base = "https://app.asana.com/api/1.0" + path
    qs = urllib.parse.urlencode(query_params)
    url = base + "?" + qs if qs else base
    all_data = []

    while url:
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {PAT}",
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read())
        all_data.extend(body.get("data", []))
        nxt = body.get("next_page")
        url = nxt["uri"] if nxt else None

    return all_data

def asana_fetch_single(path, query_params):
    """Fetch a single Asana resource (no pagination)."""
    base = "https://app.asana.com/api/1.0" + path
    qs = urllib.parse.urlencode(query_params)
    url = base + "?" + qs if qs else base
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {PAT}",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())

# --- Auto-sync on page serve ---
SYNC_SCRIPTS = {
    "/index.html": os.path.join(DIR, "sync.py"),
    "/":           os.path.join(DIR, "sync.py"),
    "/cfu-system.html": os.path.join(DIR, "sync_cfu.py"),
}
_sync_ts = {}
_sync_lock = threading.Lock()
SYNC_TTL = 300  # seconds between re-syncs per page

def maybe_sync(path):
    script = SYNC_SCRIPTS.get(path)
    if not script or not os.path.exists(script):
        return
    with _sync_lock:
        last = _sync_ts.get(path, 0)
        if time.time() - last < SYNC_TTL:
            return
        _sync_ts[path] = time.time()
    try:
        sys.stderr.write(f"[sync] Running {os.path.basename(script)} for {path}\n")
        subprocess.run(
            [sys.executable, script],
            cwd=DIR, timeout=30, capture_output=True,
        )
    except Exception as e:
        sys.stderr.write(f"[sync] Error: {e}\n")

# --- HTTP Handler ---
class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIR, **kwargs)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)

        # Proxy: /api/asana/projects/{gid}/tasks?...
        if parsed.path.startswith("/api/asana/"):
            self._handle_asana_proxy(parsed)
            return

        # Proxy: /api/s3/<bucket-path>
        if parsed.path.startswith("/api/s3/"):
            self._handle_s3_proxy(parsed)
            return

        # Cache status
        if parsed.path == "/api/cache-status":
            self._handle_cache_status()
            return

        # Auto-sync HTML pages before serving
        serve_path = parsed.path if parsed.path != "/" else "/index.html"
        if serve_path in SYNC_SCRIPTS:
            maybe_sync(serve_path)

        return super().do_GET()

    def _handle_asana_proxy(self, parsed):
        if not PAT:
            self._json_error(500, "No Asana PAT configured")
            return

        asana_path = parsed.path.replace("/api/asana", "", 1)
        params = dict(urllib.parse.parse_qsl(parsed.query))

        cache_key = asana_path + "?" + urllib.parse.urlencode(sorted(params.items()))
        cached = cache_get(cache_key)
        if cached is not None:
            self._json_response(cached, from_cache=True)
            return

        try:
            is_collection = "/tasks" in asana_path and not asana_path.rstrip("/").split("/")[-1].isdigit()
            if is_collection:
                data = asana_fetch_all(asana_path, params)
                result = {"data": data}
            else:
                result = asana_fetch_single(asana_path, params)

            cache_set(cache_key, result)
            self._json_response(result, from_cache=False)
        except HTTPError as e:
            self._json_error(e.code, str(e))
        except Exception as e:
            self._json_error(502, str(e))

    def _handle_s3_proxy(self, parsed):
        s3_path = parsed.path.replace("/api/s3/", "", 1)
        s3_url = "https://s3.us-east-1.amazonaws.com/" + s3_path

        cached = cache_get(s3_url)
        if cached is not None:
            self._json_response(cached, from_cache=True)
            return

        try:
            req = urllib.request.Request(s3_url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
            cache_set(s3_url, data)
            self._json_response(data, from_cache=False)
        except HTTPError as e:
            self._json_error(e.code, str(e))
        except Exception as e:
            self._json_error(502, str(e))

    def _handle_cache_status(self):
        with _cache_lock:
            now = time.time()
            entries = []
            for key, entry in _cache.items():
                age = int(now - entry["ts"])
                size = len(json.dumps(entry["data"]))
                entries.append({"key": key, "age_s": age, "size_bytes": size, "fresh": age < CACHE_TTL})
            self._json_response({"ttl": CACHE_TTL, "entries": entries})

    def _json_response(self, data, from_cache=False):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        if from_cache:
            self.send_header("X-Cache", "HIT")
        self.end_headers()
        self.wfile.write(body)

    def _json_error(self, code, msg):
        body = json.dumps({"error": msg}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        path = args[0].split()[1] if args else ""
        if path.startswith("/api/"):
            sys.stderr.write(f"[{self.log_date_time_string()}] {fmt % args}\n")

if __name__ == "__main__":
    print(f"Asana PAT: {'configured' if PAT else 'MISSING'}")
    print(f"Cache TTL: {CACHE_TTL}s")
    print(f"Serving {DIR} at http://localhost:{PORT}")
    print(f"Asana proxy at http://localhost:{PORT}/api/asana/...")
    http.server.HTTPServer(("", PORT), Handler).serve_forever()
