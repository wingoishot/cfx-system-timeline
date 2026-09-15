#!/usr/bin/env python3
"""Sync Asana tasks into HTML files.

Usage:
    python sync.py          # sync all projects
    python sync.py cfx      # sync CFX-System only
    python sync.py cfu      # sync CFU-System only

The PAT is read from ASANA_PAT env var or .asana_pat file.
"""
import json, os, re, sys, urllib.request, urllib.parse
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))

# ── CFX-System config ────────────────────────────────────────────────

CFX_PROJECT_GID = "1218045518949399"
CFX_SECTION_CONFIG = {
    "OTA Releases":        {"cat": "cat-ota",      "order": 0},
    "Factory Releases":    {"cat": "cat-factory",   "order": 1},
    "In Progress":         {"cat": "cat-progress",  "order": 2},
    "Upcoming Priorities": {"cat": "cat-upcoming",  "order": 3},
    "Done":                {"cat": "cat-done",      "order": 4},
}
CFX_OPT_FIELDS = "name,completed,assignee.name,resource_subtype,due_on,start_on,memberships.section.name,subtasks.name,subtasks.completed,subtasks.assignee.name,subtasks.due_on,subtasks.start_on"

# ── CFU-System config ────────────────────────────────────────────────

CFU_PROJECT_GID = "1206116040205927"
CFU_ASSIGNEE_NAME = "Katie Bruce"
CFU_SECTION_MAP = {
    "Upcoming Launches [To be updated]":             "cat-launches",
    "New Asks / Triage":                              "cat-triage",
    "In Progress / Committed Development - FY26 Q4": "cat-progress",
    "Next - FY27":                                    "cat-next",
    "Backlog":                                         "cat-backlog",
    "Ice Box":                                         "cat-icebox",
    "Done":                                            "cat-done",
}
CFU_SECTION_ORDER = list(CFU_SECTION_MAP.keys())
CFU_OPT_FIELDS = "name,completed,assignee.name,resource_subtype,due_on,start_on,memberships.section.name"

# ── OTA S3 config ────────────────────────────────────────────────────

OTA_S3_PATHS = [
    "production.system-eng-builds/g700/product/user/OTAConfig_v2.json",
    "production.system-eng-builds/g700/commercial/user/OTAConfig_v2.json",
    "production.system-eng-builds/TTR01/product/user/OTAConfig_v2.json",
    "production.system-eng-builds/TTR01/commercial/user/OTAConfig_v2.json",
    "production.system-eng-builds/RB1VQ/product/user/OTAConfig_v2.json",
    "production.system-eng-builds/RB1VQ/commercial/user/OTAConfig_v2.json",
    "production.system-eng-builds/RB1VO/product/user/OTAConfig_v2.json",
    "production.system-eng-builds/RB1VO/commercial/user/OTAConfig_v2.json",
    "production.system-eng-builds/sapphire/product/user/OTAConfig_v2.json",
]

# ── Shared utilities ─────────────────────────────────────────────────

def get_pat():
    pat = os.environ.get("ASANA_PAT", "").strip()
    if pat:
        return pat
    pat_file = os.path.join(BASE, ".asana_pat")
    if os.path.exists(pat_file):
        with open(pat_file) as f:
            return f.read().strip()
    print("No Asana PAT found. Set ASANA_PAT env var or create .asana_pat file.", file=sys.stderr)
    sys.exit(1)

def fetch_all_tasks(pat, project_gid, opt_fields):
    params = urllib.parse.urlencode({
        "completed_since": "2026-01-01T00:00:00Z",
        "opt_fields": opt_fields,
        "limit": "100",
    })
    url = f"https://app.asana.com/api/1.0/projects/{project_gid}/tasks?{params}"
    all_data = []

    while url:
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {pat}",
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read())
        all_data.extend(body.get("data", []))
        nxt = body.get("next_page")
        url = nxt["uri"] if nxt else None
        if url:
            print(f"  Page fetched, {len(all_data)} tasks so far...")

    return all_data

def inject_tasks(html_path, tasks):
    now = datetime.now().isoformat(timespec="seconds")
    inject = (
        f"// @@TASKS_START@@\n"
        f"// Auto-synced from Asana: {now}\n"
        f"var TASKS = {json.dumps(tasks, indent=2)};\n"
        f"// @@TASKS_END@@"
    )
    with open(html_path) as f:
        html = f.read()
    updated = re.sub(
        r"// @@TASKS_START@@.*?// @@TASKS_END@@",
        inject,
        html,
        flags=re.DOTALL,
    )
    with open(html_path, "w") as f:
        f.write(updated)

# ── CFX-System sync ──────────────────────────────────────────────────

def parse_subtasks(subtasks):
    result = []
    for s in (subtasks or []):
        t = {"name": s["name"], "completed": s["completed"],
             "assignee": (s.get("assignee") or {}).get("name")}
        if s.get("due_on"):
            t["due"] = s["due_on"]
        if s.get("start_on"):
            t["start"] = s["start_on"]
        result.append(t)
    return result

def parse_cfx_tasks(data):
    buckets = {}
    for name, cfg in CFX_SECTION_CONFIG.items():
        buckets[name] = {"section": name, "cat": cfg["cat"], "order": cfg["order"], "tasks": []}

    seen_gids = set()
    for item in data:
        if item["gid"] in seen_gids:
            continue
        seen_gids.add(item["gid"])

        if not item.get("name") or not item["name"].strip():
            continue

        section_name = None
        for m in item.get("memberships", []):
            sn = m.get("section", {}).get("name", "")
            if sn in CFX_SECTION_CONFIG:
                section_name = sn
                break
        if not section_name:
            continue

        t = {"gid": item["gid"],
             "name": item["name"].strip(),
             "completed": item["completed"],
             "assignee": (item.get("assignee") or {}).get("name"),
             "resource_subtype": item.get("resource_subtype", "default_task")}
        if item.get("due_on"):
            t["due"] = item["due_on"]
        if item.get("start_on"):
            t["start"] = item["start_on"]
        subtasks = parse_subtasks(item.get("subtasks"))
        if subtasks:
            t["subtasks"] = subtasks
        buckets[section_name]["tasks"].append(t)

    result = sorted(buckets.values(), key=lambda b: b["order"])
    return [{"section": b["section"], "cat": b["cat"], "tasks": b["tasks"]} for b in result]

def strip_ota(data):
    result = {}
    if "fullOTA" in data:
        result["fullOTA"] = [
            {k: o[k] for k in ("version", "percentage", "platforms") if k in o}
            for o in data["fullOTA"]
        ]
    if "incremental" in data:
        result["incremental"] = [
            {k: o[k] for k in ("version", "from", "percentage", "platforms") if k in o}
            for o in data["incremental"]
        ]
    return result

def fetch_ota_cache():
    cache = {}
    for s3path in OTA_S3_PATHS:
        url = f"https://s3.us-east-1.amazonaws.com/{s3path}"
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
            cache[s3path] = strip_ota(data)
            ota = (data.get("fullOTA") or [{}])[0]
            print(f"  {s3path}: {ota.get('version', 'N/A')} @ {ota.get('percentage', 0)}%")
        except Exception as e:
            print(f"  {s3path}: fetch failed ({e})", file=sys.stderr)
    return cache

def sync_ota_cache(cache):
    inject = (
        f"// @@OTA_CACHE_START@@\n"
        f"var OTA_CACHE = {json.dumps(cache)};\n"
        f"// @@OTA_CACHE_END@@"
    )
    html_path = os.path.join(BASE, "dashboard.html")
    with open(html_path) as f:
        html = f.read()
    updated = re.sub(
        r"// @@OTA_CACHE_START@@.*?// @@OTA_CACHE_END@@",
        inject,
        html,
        flags=re.DOTALL,
    )
    with open(html_path, "w") as f:
        f.write(updated)
    print(f"  Wrote {len(cache)} OTA configs into dashboard.html")

def sync_cfx(pat):
    print(f"Fetching CFX-System tasks (project {CFX_PROJECT_GID})...")
    data = fetch_all_tasks(pat, CFX_PROJECT_GID, CFX_OPT_FIELDS)
    print(f"  Fetched {len(data)} total items from Asana")

    tasks = parse_cfx_tasks(data)
    count = sum(len(s["tasks"]) for s in tasks)
    print(f"  Parsed {count} tasks across {len(tasks)} sections")

    html_path = os.path.join(BASE, "index.html")
    inject_tasks(html_path, tasks)

    with open(html_path) as f:
        html = f.read()
    updated = re.sub(r'const ASANA_PAT = "@@ASANA_PAT@@"', f'const ASANA_PAT = "{pat}"', html)
    with open(html_path, "w") as f:
        f.write(updated)

    print(f"Wrote {count} tasks across {len(tasks)} sections into index.html")

    print("Fetching OTA configs from S3...")
    cache = fetch_ota_cache()
    sync_ota_cache(cache)

# ── CFU-System sync ──────────────────────────────────────────────────

def parse_cfu_tasks(all_items):
    buckets = {}
    for name in CFU_SECTION_MAP:
        buckets[name] = {"section": name, "cat": CFU_SECTION_MAP[name], "tasks": []}

    seen_gids = set()
    for item in all_items:
        if item["gid"] in seen_gids:
            continue
        seen_gids.add(item["gid"])

        if not item.get("name") or not item["name"].strip():
            continue

        assignee = item.get("assignee")
        if not assignee or assignee.get("name") != CFU_ASSIGNEE_NAME:
            continue

        mem = None
        for m in (item.get("memberships") or []):
            if m.get("section") and m["section"].get("name") in CFU_SECTION_MAP:
                mem = m
                break
        if not mem:
            continue

        section_name = mem["section"]["name"]
        bucket = buckets[section_name]
        task_name = item["name"].strip()

        if any(existing["name"] == task_name for existing in bucket["tasks"]):
            continue

        t = {
            "gid": item["gid"],
            "name": task_name,
            "completed": item["completed"],
            "assignee": assignee.get("name"),
            "resource_subtype": item.get("resource_subtype", "default_task"),
        }
        if item.get("due_on"):
            t["due"] = item["due_on"]
        if item.get("start_on"):
            t["start"] = item["start_on"]
        bucket["tasks"].append(t)

    return [buckets[s] for s in CFU_SECTION_ORDER if buckets[s]["tasks"]]

def sync_cfu(pat):
    print(f"Fetching CFU-System tasks (project {CFU_PROJECT_GID})...")
    all_items = fetch_all_tasks(pat, CFU_PROJECT_GID, CFU_OPT_FIELDS)
    print(f"  Fetched {len(all_items)} total items from Asana")

    tasks = parse_cfu_tasks(all_items)
    task_count = sum(len(s["tasks"]) for s in tasks)
    print(f"  Parsed {task_count} tasks for {CFU_ASSIGNEE_NAME} across {len(tasks)} sections")

    html_path = os.path.join(BASE, "cfu-system.html")
    inject_tasks(html_path, tasks)
    print(f"Wrote {task_count} tasks into cfu-system.html")

# ── Main ─────────────────────────────────────────────────────────────

def main():
    pat = get_pat()
    target = sys.argv[1].lower() if len(sys.argv) > 1 else "all"

    if target in ("all", "cfx"):
        sync_cfx(pat)
    if target in ("all", "cfu"):
        sync_cfu(pat)

if __name__ == "__main__":
    main()
