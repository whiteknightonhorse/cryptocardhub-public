"""redotpay-review states only what RedotPay's help centre says (config/facts/redotpay.yaml)."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src/content/pseo/redotpay-review.md"
DIST = ROOT / "dist/reviews/redotpay-review"
SRC2 = ROOT / "src/content/pseo/is-redotpay-legit.md"
DIST2 = ROOT / "dist/reviews/is-redotpay-legit"
BAD = re.compile(
    r"(?i)non-custod|self-custod|custodial|vault|metamask|ledger|seed phrase|private key"
    r"|\bVisa\b|cashback is|~2|2026-08-13|Canada|Japan|Hong Kong|Australia|South Korea|EEA|LATAM"
)
MUST = ["to your RedotPay account", "including the United States and Singapore", "2026-11-10"]
MUST_NOT = ["self-custody wallet required", "US, Canada", "Japan, Singapore"]
MUST2 = ["to your RedotPay account", "including the United States and Singapore", "2026-11-10", "does not certify"]
MUST_NOT2 = ["never holds", "cannot steal", "All checks pass", "RedotPay is legit.", "RedotPay is legitimate",
             "funds are untouched", "2026-05-19"]
FAQ2 = [
    "Is RedotPay legit?",
    "Does RedotPay hold my crypto, or does it stay in my wallet?",
    "Is my money at risk if RedotPay has a security incident?",
    "Where is the RedotPay card not available?",
    "How much does a RedotPay card cost?",
    "Has DefyCard verified complaints about RedotPay?",
]


def bad_source(text):
    keep = [l for l in text.split("\n") if not l.startswith(("related_clusters:", "images:"))]
    return BAD.findall("\n".join(keep))


def bad_md(text):
    return [m for l in text.split("\n") if "RedotPay" in l for m in BAD.findall(l)]


def bad_surface(text):
    return [s for s in MUST if s not in text] + [s for s in MUST_NOT if s in text]


def bad_surface2(text):
    # schema_article.datePublished is byte-identical by the card's law; it is the publication date, not a claim.
    text = text.replace('"datePublished":"2026-05-19"', "")
    return [s for s in MUST2 if s not in text] + [s for s in MUST_NOT2 if s in text]


def frontmatter(text):
    lines = text.split("\n")
    return {l.split(":", 1)[0]: json.loads(l.split(": ", 1)[1])
            for l in lines[1:lines.index("---", 1)] if l.split(": ", 1)[1:][:1] and l.split(": ", 1)[1][:1] in '[{"'}


def test_source_clean():
    assert bad_source(SRC.read_text(encoding="utf-8")) == []


def test_dist_md_clean():
    assert bad_md((DIST / "index.md").read_text(encoding="utf-8")) == []


def test_dist_surfaces():
    for name in ("index.html", "index.md"):
        assert bad_surface((DIST / name).read_text(encoding="utf-8")) == [], name


def test_faq_equals_schema_faq():
    fm = frontmatter(SRC.read_text(encoding="utf-8"))
    assert [x["q"] for x in fm["faq"]] == [
        "Does RedotPay hold my crypto, or does it stay in my wallet?",
        "Where is the RedotPay card not available?",
        "How much does a RedotPay card cost?",
        "Does RedotPay charge a monthly or annual fee?",
        "Does RedotPay charge a transaction fee?",
    ]
    schema = [(x["name"], x["acceptedAnswer"]["text"]) for x in fm["schema_faq"]["mainEntity"]]
    assert [(x["q"], x["a"]) for x in fm["faq"]] == schema


def test_legit_source_clean():
    assert bad_source(SRC2.read_text(encoding="utf-8")) == []


def test_legit_dist_clean():
    assert bad_md((DIST2 / "index.md").read_text(encoding="utf-8")) == []


def test_legit_dist_surfaces():
    for name in ("index.html", "index.md"):
        assert bad_surface2((DIST2 / name).read_text(encoding="utf-8")) == [], name


def test_legit_faq_equals_schema_faq():
    fm = frontmatter(SRC2.read_text(encoding="utf-8"))
    assert [x["q"] for x in fm["faq"]] == FAQ2
    schema = [(x["name"], x["acceptedAnswer"]["text"]) for x in fm["schema_faq"]["mainEntity"]]
    assert [(x["q"], x["a"]) for x in fm["faq"]] == schema


def test_legit_controls():
    assert bad_source("intro: RedotPay is non-custodial") != []
    assert bad_surface2("All checks pass. RedotPay is legit. to your RedotPay account including the United States and Singapore 2026-11-10 does not certify") == ["All checks pass", "RedotPay is legit."]
    assert bad_surface2("to your RedotPay account including the United States and Singapore 2026-11-10 does not certify") == []


def test_controls():
    assert bad_source("intro: RedotPay is non-custodial") != []
    assert bad_source("related_clusters: [self-custody]\nimages: [Canada]") == []
    assert bad_surface("to your RedotPay account including the United States and Singapore 2026-11-10 Americas: US, Canada") == ["US, Canada"]
    assert bad_md("RedotPay is non-custodial") != []
