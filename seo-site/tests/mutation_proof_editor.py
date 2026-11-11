"""CCH-SEO-34 mutation proofs. Not collected by pytest (no test_ prefix); run:
  video-generator/venv/bin/python seo-site/tests/mutation_proof_editor.py
Each mutation rewrites the BUILT review page the way the named source mutation
would, asserts the NAMED test goes RED (and only by that test), then restores
the page from a backup copy."""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "dist/reviews/binance-card-review/index.html"
BYLINE = re.compile(r'<p class="byline">.*?</p>', re.S)


def red(name):
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-rf", "tests/test_editor_and_policy.py"],
                       cwd=ROOT, capture_output=True, text=True)
    return [ln for ln in r.stdout.splitlines() if ln.startswith("FAILED") and name in ln]


def m1_editor_null_no_byline_no_editor_ld(t):
    t = BYLINE.sub("", t)
    return re.sub(r'"editor":\{[^{}]*(\{[^{}]*\})?[^{}]*\},?', "", t)


def m2_label_back_to_reviewed(t):
    return t.replace("Editor: ", "By ", 1).replace("· Updated", "· Reviewed", 1)


def m3_author_set_to_the_person(t):
    return t.replace('"author":{"@type":"Organization"', '"author":{"@type":"Person"', 1)


MUT = [(m1_editor_null_no_byline_no_editor_ld, "test_byline_is_editor_and_nothing_says_reviewed"),
       (m2_label_back_to_reviewed, "test_byline_is_editor_and_nothing_says_reviewed"),
       (m3_author_set_to_the_person, "test_article_jsonld_editor_person_author_not_person")]

orig = PAGE.read_text()
assert not any(red(n) for _, n in MUT[:1]), "baseline not green"
ok = True
try:
    for fn, test in MUT:
        mutated = fn(orig)
        assert mutated != orig, f"{fn.__name__} changed nothing"
        PAGE.write_text(mutated)
        hit = red(test)
        print(("RED  " if hit else "GREEN!"), fn.__name__, "->", test)
        ok &= bool(hit)
finally:
    PAGE.write_text(orig)
sys.exit(0 if ok else 1)
