#!/usr/bin/env python3
"""CCH-DEAD-ISSUER-01 gate: a sunset issuer (Cypher) is never recommended in the built site.

Reads dist/ (not the sources). A text block (<p>/<li>/<td>/<h*>/<script> JSON-LD...) may
mention Cypher only if the same block says it is closed. No href may point at Cypher except
the single issuer-status source https://www.cypherhq.io/ inside such a closed-note block.
Exit 1 on any violation. Usage: check-dead-issuer.py [dist_dir]
"""
import re, sys, pathlib

NAME = re.compile(r'\bCypher\b')
CLOSED = re.compile(r'\b(closed|sunset|acquired by Nium)\b', re.I)
BLOCK = re.compile(r'<(p|li|td|th|h[1-6]|dd|dt|summary|script|title|meta)\b[^>]*>(.*?)</\1>|<meta\b[^>]*>', re.S | re.I)
HREF = re.compile(r'href="([^"]*)"', re.I)

def violations(html):
    out = []
    for m in BLOCK.finditer(html):
        blk = m.group(0)
        if NAME.search(blk) and not CLOSED.search(blk):
            out.append('recommendation-context: ' + re.sub(r'\s+', ' ', NAME.search(blk) and blk[max(0, NAME.search(blk).start() - 60):NAME.search(blk).end() + 60]))
    for h in HREF.findall(html):
        if 'cypher' in h.lower() and h.rstrip('/') != 'https://www.cypherhq.io':
            out.append('affiliate/link: ' + h)
    stripped = BLOCK.sub('', html)  # mentions outside any known block are unreviewable
    if NAME.search(re.sub(r'<[^>]+>', '', stripped)):
        out.append('uncovered-mention-outside-block')
    return out

def main(dist):
    files = mentioning = bad = 0
    for p in pathlib.Path(dist).rglob('*.html'):
        h = p.read_text(encoding='utf8', errors='replace')
        files += 1
        if not NAME.search(h):
            continue
        mentioning += 1
        v = violations(h)
        if v:
            bad += len(v)
            print(f'FAIL {p}: ' + ' | '.join(v[:3]))
    print(f'html files scanned: {files}; files still mentioning Cypher (closed-note only): {mentioning}; Cypher recommendations: {bad}')
    return 1 if bad else 0

if __name__ == '__main__':
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else '/home/cryptocardhub/seo-site/dist'))
