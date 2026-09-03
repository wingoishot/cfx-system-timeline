#!/usr/bin/env python3
import json, os, urllib.request, sys
from datetime import datetime

PROJECT_GID = "1218045518949399"
SECTION_CONFIG = {
    "OTA Releases":        {"cat": "cat-ota",      "order": 0},
    "Factory Releases":    {"cat": "cat-factory",   "order": 1},
    "In Progress":         {"cat": "cat-progress",  "order": 2},
    "Upcoming Priorities": {"cat": "cat-upcoming",  "order": 3},
    "Done":                {"cat": "cat-done",      "order": 4},
}

def get_pat():
    pat = os.environ.get("ASANA_PAT", "").strip()
    if pat:
        return pat
    pat_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".asana_pat")
    if os.path.exists(pat_file):
        with open(pat_file) as f:
            return f.read().strip()
    print("No Asana PAT found. Set ASANA_PAT env var or create .asana_pat file.", file=sys.stderr)
    sys.exit(1)

def fetch_tasks(pat):
    url = (f"https://app.asana.com/api/1.0/projects/{PROJECT_GID}/tasks"
           f"?opt_pretty&opt_expand=(this%7Csubtasks%2B)")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {pat}"})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())["data"]

def parse_subtasks(subtasks):
    result = []
    for s in (subtasks or []):
        t = {"name": s["name"], "completed": s["completed"],
             "assignee": (s.get("assignee") or {}).get("name")}
        if s.get("due_on"):
            t["due"] = s["due_on"]
        if s.get("start_on"):
            t["start"] = s["start_on"]
        children = parse_subtasks(s.get("subtasks"))
        if children:
            t["subtasks"] = children
        result.append(t)
    return result

def parse_tasks(data):
    buckets = {}
    for name, cfg in SECTION_CONFIG.items():
        buckets[name] = {"section": name, "cat": cfg["cat"], "order": cfg["order"], "tasks": []}
    for item in data:
        section_name = None
        for m in item.get("memberships", []):
            sn = m.get("section", {}).get("name", "")
            if sn in SECTION_CONFIG:
                section_name = sn
                break
        if not section_name:
            continue
        t = {"name": item["name"], "completed": item["completed"],
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

def main():
    pat = get_pat()
    print("Fetching from Asana...")
    data = fetch_tasks(pat)
    tasks = parse_tasks(data)
    base = os.path.dirname(os.path.abspath(__file__))
    now = datetime.now().isoformat(timespec="seconds")
    inject = f"// @@TASKS_START@@\n// Auto-synced from Asana: {now}\nvar TASKS = {json.dumps(tasks, indent=2)};\n// @@TASKS_END@@"
    html_path = os.path.join(base, "index.html")
    with open(html_path) as f:
        html = f.read()
    import re
    updated = re.sub(r"// @@TASKS_START@@.*?// @@TASKS_END@@", inject, html, flags=re.DOTALL)
    with open(html_path, "w") as f:
        f.write(updated)
    count = sum(len(s["tasks"]) for s in tasks)
    print(f"Wrote {count} tasks across {len(tasks)} sections into index.html")

if __name__ == "__main__":
    main()
