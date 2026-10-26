#!/usr/bin/env python3
import glob, gzip, re, json
from pathlib import Path

LINE_RE = re.compile(r'^(\S+) \S+ \S+ \[([^\]]+)\] "([^"]*)" (\d{3}) (\S+) "([^"]*)" "([^"]*)"')

rows = []
count = {"429": 0, "502": 0}
total_lines = 0
unmatched = 0
for path in sorted(glob.glob(str(Path.home() / "logs/aeo/raw-hold/2026-W34-W35/access.log*"))):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as f:
        for line in f:
            total_lines += 1
            m = LINE_RE.match(line)
            if not m:
                unmatched += 1
                continue
            ip, ts, req, status, size, ref, ua = m.groups()
            if status in ("429", "502"):
                count[status] += 1
                rows.append({"status": status, "ip": ip, "ts": ts, "request": req, "ua": ua, "file": Path(path).name})

print("total_lines", total_lines, "unmatched", unmatched)
print(json.dumps(count))
print("TOTAL", len(rows))
out = Path.home() / "logs/aeo/status-429-502-2026-09-01.json"
out.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
print("wrote", out)
for r in rows:
    print(r["status"], r["ip"], r["ts"], r["request"], "||", r["ua"])
