"""CCH-SEO-32 mutation proofs. Not collected by pytest (no test_ prefix); run:
  video-generator/venv/bin/python seo-site/tests/mutation_proof_video_canonical.py
Each mutation rewrites the BUILT dist (or the sitemap script) the way the
named source mutation would, asserts test_video_canonical.py goes RED, then
restores the dist from a backup copy."""
import glob
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST, BAK = ROOT / "dist", Path("/tmp/dist-bak32")
SCRIPT = ROOT.parent / "scripts" / "build-video-sitemap.py"
VIDEOS = [json.load(open(f)) for f in glob.glob(str(ROOT / "src/content/videos/*.json"))]
NOINDEX = '<meta name="robots" content="noindex, follow">'


def run_tests():
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-rf", "tests/test_video_canonical.py"],
                       cwd=ROOT, capture_output=True, text=True)
    return r.returncode, [ln for ln in r.stdout.splitlines() if ln.startswith("FAILED")]


def rewrite(fn):
    for p in glob.glob(str(DIST / "videos/*/index.html")):
        t = open(p).read()
        open(p, "w").write(fn(p, t))


def m1_canonical_to_every_video_with_target():
    tgt = {v["id"]: v["target_pseo_slug"] for v in VIDEOS if v.get("target_pseo_slug")}

    def f(p, t):
        i = p.split("/")[-2]
        if i in tgt:
            t = re.sub(r'(<link rel="canonical" href=")[^"]+',
                       r"\g<1>https://defycard.com/%s/" % tgt[i], t).replace(NOINDEX, "")
        return t
    rewrite(f)


def m2_noindex_branch_removed():
    rewrite(lambda p, t: t.replace(NOINDEX, ""))


def m3_sitemap_filter_removed():
    s = (DIST / "sitemap-0.xml").read_text()
    leak = f'<url><loc>https://defycard.com/videos/{VIDEOS[0]["id"]}/</loc></url></urlset>'
    (DIST / "sitemap-0.xml").write_text(s.replace("</urlset>", leak))


def m4_video_sitemap_emits_video_url_again():
    src = SCRIPT.read_text()
    SCRIPT.write_text(src.replace("_video_url_block(canon, v)",
                                  '_video_url_block(f"{SITE}/videos/{v[\'id\']}/", v)'))
    try:
        subprocess.run([sys.executable, str(SCRIPT)], check=True, capture_output=True)
    finally:
        SCRIPT.write_text(src)


if BAK.exists():
    shutil.rmtree(BAK)  # fresh copy every run: a stale backup would restore an old dist into the deploy tree
shutil.copytree(DIST, BAK)
assert run_tests()[0] == 0, "baseline must be green"
for m in (m1_canonical_to_every_video_with_target, m2_noindex_branch_removed,
          m3_sitemap_filter_removed, m4_video_sitemap_emits_video_url_again):
    m()
    rc, failed = run_tests()
    print(m.__name__, "RED" if rc else "GREEN(!)", failed[:2])
    shutil.rmtree(DIST)
    shutil.copytree(BAK, DIST)
assert run_tests()[0] == 0, "restore must be green"
