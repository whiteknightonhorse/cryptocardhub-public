#!/usr/bin/env python3
import glob, gzip, re, json, sys
from pathlib import Path
from collections import Counter, defaultdict
from datetime import datetime

sys.path.insert(0, str(Path.home() / "scripts"))
import importlib.util
spec = importlib.util.spec_from_file_location("aeo_aggregate", Path.home() / "scripts" / "aeo-aggregate.py")
aeo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aeo)

LINE_RE = re.compile(r'^(\S+) \S+ \S+ \[([^\]]+)\] "([^"]*)" (\d{3}) (\S+) "([^"]*)" "([^"]*)"')

rows = []
for path in sorted(glob.glob(str(Path.home() / "logs/aeo/raw-hold/2026-W34-W35/access.log*"))):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as f:
        for line in f:
            m = LINE_RE.match(line)
            if not m:
                continue
            ip, ts, req, status, size, ref, ua = m.groups()
            if status not in ("429", "502"):
                continue
            d = datetime.strptime(ts.split()[0], "%d/%b/%Y:%H:%M:%S")
            rows.append({"status": status, "ip": ip, "date": d.date().isoformat(),
                         "time": d.isoformat(), "req": req, "ua": ua})

print("TOTAL 429/502 lines:", len(rows))
by_status = Counter(r["status"] for r in rows)
print("by status:", dict(by_status))

by_date = Counter((r["status"], r["date"]) for r in rows)
print("\n-- by date --")
for (st, d), n in sorted(by_date.items(), key=lambda kv: (kv[0][1], kv[0][0])):
    print(st, d, n)

by_ip = Counter(r["ip"] for r in rows)
print("\n-- top 10 source IPs --")
for ip, n in by_ip.most_common(10):
    print(ip, n)
print("unique IPs total:", len(by_ip))

ranges, ranges_meta = aeo.load_all_bot_ranges()
print("\nranges_meta:", ranges_meta)

named_agent = []
verified_real = []
for r in rows:
    agent = aeo.agent_of(r["ua"])
    if agent:
        r["named_agent"] = agent
        named_agent.append(r)
        if aeo.verify_ip(r["ip"], agent, ranges):
            verified_real.append(r)

print("\nlines whose UA names one of the 3 answer-time agents:", len(named_agent))
print("of those, IP actually verified against vendor range (REAL):", len(verified_real))

print("\n-- REAL (verified) 429/502 rows, full detail --")
for r in sorted(verified_real, key=lambda r: r["time"]):
    print(json.dumps(r, ensure_ascii=False))

# also show breakdown of named_agent (claimed) by agent name, to see forgery mix
print("\n-- claimed-agent-name breakdown (UA substring match only, NOT verified) --")
print(Counter(r["named_agent"] for r in named_agent))

# path shape check: article-shaped vs not, for the claimed set
article_shaped = sum(1 for r in named_agent if aeo.is_article_path(r["req"].split()[1].split("?")[0]) if len(r["req"].split()) > 1)
print("claimed-agent lines on article-shaped path:", article_shaped, "/", len(named_agent))

out = Path.home() / "logs/aeo/status-429-502-breakdown-2026-09-01.json"
out.write_text(json.dumps({
    "total_lines": len(rows),
    "by_status": dict(by_status),
    "unique_source_ips": len(by_ip),
    "top_ips": by_ip.most_common(10),
    "claimed_agent_lines": len(named_agent),
    "claimed_agent_breakdown": dict(Counter(r["named_agent"] for r in named_agent)),
    "verified_real_agent_lines": verified_real,
}, indent=2), encoding="utf-8")
print("\nwrote", out)
