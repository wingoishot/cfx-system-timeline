#!/usr/bin/env python3
"""Sync CFU-System tasks from Asana into cfu-system.html as embedded data.

Usage:
    python sync_cfu.py              # fetches and embeds tasks
    python sync_cfu.py --live       # also refreshes live (same as default)

The PAT is read from ASANA_PAT env var or .asana_pat file.
"""
import json, os, re, sys, urllib.request, urllib.parse
from datetime import datetime

PROJECT_GID = "1206116040205927"
ASSIGNEE_NAME = "Katie Bruce"

SECTION_MAP = {
    "Upcoming Launches [To be updated]":             "cat-launches",
    "New Asks / Triage":                              "cat-triage",
    "In Progress / Committed Development - FY26 Q4": "cat-progress",
    "Next - FY27":                                    "cat-next",
    "Backlog":                                         "cat-backlog",
    "Ice Box":                                         "cat-icebox",
    "Done":                                            "cat-done",
}
SECTION_ORDER = list(SECTION_MAP.keys())

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
        "opt_fields": "name,completed,assignee.name,resource_subtype,due_on,start_on,memberships.section.name",
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

def parse_tasks(all_items):
    buckets = {}
    for name in SECTION_MAP:
        buckets[name] = {"section": name, "cat": SECTION_MAP[name], "tasks": []}

    seen_gids = set()
    for item in all_items:
        if item["gid"] in seen_gids:
            continue
        seen_gids.add(item["gid"])

        if not item.get("name") or not item["name"].strip():
            continue

        assignee = item.get("assignee")
        if not assignee or assignee.get("name") != ASSIGNEE_NAME:
            continue

        mem = None
        for m in (item.get("memberships") or []):
            if m.get("section") and m["section"].get("name") in SECTION_MAP:
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

    return [buckets[s] for s in SECTION_ORDER if buckets[s]["tasks"]]

def main():
    pat = get_pat()
    print(f"Fetching CFU-System tasks (project {PROJECT_GID})...")
    all_items = fetch_all_tasks(pat)
    print(f"  Fetched {len(all_items)} total items from Asana")

    tasks = parse_tasks(all_items)
    task_count = sum(len(s["tasks"]) for s in tasks)
    print(f"  Parsed {task_count} tasks for {ASSIGNEE_NAME} across {len(tasks)} sections")

    now = datetime.now().isoformat(timespec="seconds")
    inject = (
        f"// @@TASKS_START@@\n"
        f"// Auto-synced from Asana: {now}\n"
        f"var TASKS = {json.dumps(tasks, indent=2)};\n"
        f"// @@TASKS_END@@"
    )

    html_path = os.path.join(BASE, "cfu-system.html")
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

    print(f"Wrote {task_count} tasks into cfu-system.html")

if __name__ == "__main__":
    main()
