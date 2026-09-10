#!/usr/bin/env python3
"""Sync CFX-System tasks from Asana into index.html as embedded data.

Usage:
    python sync.py              # fetches and embeds tasks

The PAT is read from ASANA_PAT env var or .asana_pat file.
"""
import json, os, re, sys, urllib.request, urllib.parse
from datetime import datetime

PROJECT_GID = "1218045518949399"
SECTION_CONFIG = {
    "OTA Releases":        {"cat": "cat-ota",      "order": 0},
    "Factory Releases":    {"cat": "cat-factory",   "order": 1},
    "In Progress":         {"cat": "cat-progress",  "order": 2},
    "Upcoming Priorities": {"cat": "cat-upcoming",  "order": 3},
    "Done":                {"cat": "cat-done",      "order": 4},
}

BASE = os.path.dirname(os.path.abspath(__file__))

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

def fetch_all_tasks(pat):
    params = urllib.parse.urlencode({
        "completed_since": "2026-01-01T00:00:00Z",
        "opt_fields": "name,completed,assignee.name,resource_subtype,due_on,start_on,memberships.section.name,subtasks.name,subtasks.completed,subtasks.assignee.name,subtasks.due_on,subtasks.start_on",
        "limit": "100",
    })
    url = f"https://app.asana.com/api/1.0/projects/{PROJECT_GID}/tasks?{params}"
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

def parse_tasks(data):
    buckets = {}
    for name, cfg in SECTION_CONFIG.items():
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
            if sn in SECTION_CONFIG:
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

OTA_S3_PATHS = [
    "production.system-eng-builds/g700/product/user/OTAConfig_v2.json",
    "production.system-eng-builds/g700/commercial/user/OTAConfig_v2.json",
    "production.system-eng-builds/TTR01/product/user/OTAConfig_v2.json",
    "production.system-eng-builds/TTR01/commercial/user/OTAConfig_v2.json",
    "production.system-eng-builds/RB1VQ/product/user/OTAConfig_v2.json",
    "production.system-eng-builds/RB1VQ/commercial/user/OTAConfig_v2.json",
    "production.system-eng-builds/RB1VO/product/user/OTAConfig_v2.json",
    "production.system-eng-builds/RB1VO/commercial/user/OTAConfig_v2.json",
]

def fetch_ota_cache():
    cache = {}
    for s3path in OTA_S3_PATHS:
        url = f"https://s3.us-east-1.amazonaws.com/{s3path}"
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
            cache[s3path] = data
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

def main():
    pat = get_pat()
    print(f"Fetching CFX-System tasks (project {PROJECT_GID})...")
    data = fetch_all_tasks(pat)
    print(f"  Fetched {len(data)} total items from Asana")

    tasks = parse_tasks(data)
    count = sum(len(s["tasks"]) for s in tasks)
    print(f"  Parsed {count} tasks across {len(tasks)} sections")

    now = datetime.now().isoformat(timespec="seconds")
    inject = (
        f"// @@TASKS_START@@\n"
        f"// Auto-synced from Asana: {now}\n"
        f"var TASKS = {json.dumps(tasks, indent=2)};\n"
        f"// @@TASKS_END@@"
    )

    html_path = os.path.join(BASE, "index.html")
    with open(html_path) as f:
        html = f.read()

    updated = re.sub(
        r"// @@TASKS_START@@.*?// @@TASKS_END@@",
        inject,
        html,
        flags=re.DOTALL,
    )
    updated = re.sub(r'const ASANA_PAT = "@@ASANA_PAT@@"', f'const ASANA_PAT = "{pat}"', updated)

    with open(html_path, "w") as f:
        f.write(updated)

    print(f"Wrote {count} tasks across {len(tasks)} sections into index.html")

    print("Fetching OTA configs from S3...")
    cache = fetch_ota_cache()
    sync_ota_cache(cache)

if __name__ == "__main__":
    main()
