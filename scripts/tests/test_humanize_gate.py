"""CCH-HUM-02: humanize_gate pins. Thresholds are LITERALS here, never read from the module."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import humanize_gate as hg  # noqa: E402


def kinds(text):
    return sorted(f.kind for f in hg.lint(text))


def test_literals():
    assert hg.THRESHOLD == 4.0
    assert hg.EM_DASH_PER_1000 == 3.0
    assert len(hg.VOICE_SPEC) <= 700


def test_one_of_each_tell():
    text = "\n".join([
        "This is a seamless card.",
        "In conclusion we pay fees.",
        "The fee is very low.",
        "It is not just a card but a wallet.",
        "Fees— are listed here.",
        "Plain sentence one. Next plain two. Other plain three.",
    ])
    # emdash density is 1 per ~30 words here, far over 3/1000
    assert kinds(text) == ["contrast", "emdash", "intensifier", "lexicon", "phrase"]


def test_opener():
    text = "The card is red. The card is blue. The card is green."
    assert kinds(text) == ["opener"]


def test_clean():
    assert hg.lint("The card costs $50. Cashback is 3 percent. Spend limits vary by tier.") == []
    assert hg.score("The card costs $50.") == 0.0


def test_heading_skipped():
    assert hg.lint("## The landscape of fees\n\nFees apply.") == []
    assert [f.kind for f in hg.lint("The landscape of fees.")] == ["lexicon"]


def test_ftc_sentence_skipped():
    s = "DefyCard publishes affiliate-linked reviews; we may earn a commission when you actually sign up."
    assert hg.lint(s) == []


def test_callout_label_skipped_sentence_not():
    assert hg.lint("**Signal:** Fees apply.") == []
    assert [f.kind for f in hg.lint("**Risk:** It is crucial to check.")] == ["lexicon"]


def test_json_escaped_emdash_counts():
    assert [f.kind for f in hg.lint("Fees\\u2014apply to all.")] == ["emdash"]


def test_payload_fields():
    p = {"title": "landscape", "description": "robust", "intro_paragraph": "a", "faq": [{"q": "q", "a": "ans"}],
         "bottom_line": ["b"], "body_markdown": "c"}
    assert hg.payload_fields(p) == ["a", "c", "b", "ans"]


# --- CCH-HUM-03: copy-edit call + invariants (stubbed, no network) ---------
import copy  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402

FILLER = " ".join(["The card is red.", "A fee is low."] * 80)   # ~480 clean words


def _payload(*dashed):
    body = "\n".join(dashed)
    return {"title": "T — keep", "description": "D — keep", "intro_paragraph": FILLER, "body_markdown": body}


def _stub(edit):
    """call() stub: parses the numbered user message, answers {id: edit(sentence)}."""
    calls = []

    def call(system, user, model=None):
        calls.append((system, user))
        out = {}
        for line in user.split("\n"):
            m = re.match(r"(\d+) \[[^\]]*\] (.*)$", line)
            out[m.group(1)] = edit(m.group(2))
        return json.dumps(out), {"in": 1}, 0.01
    call.calls = calls
    return call


def _clean(s):
    return s.replace(" — ", ", ")


def _run(edit, *sentences):
    p = _payload(*sentences)
    return p, hg.humanize(p, call=_stub(edit))


S_FILL = ["Fees are low — billed yearly.", "Limits are set — by tier.", "Support is open — all week."]


def test_a_dropped_placeholder_rejected():
    bad = "[Get it](<<AFFILIATE_URL>>) — costs less."
    p, r = _run(lambda s: "Get it, costs less." if "AFFILIATE" in s else _clean(s), bad, *S_FILL)
    assert r.rejected == 1 and r.accepted == 3 and r.refused_reason is None
    assert bad in r.payload["body_markdown"]


def test_b_changed_money_rejected():
    bad = "The fee is $50 — billed yearly."
    p, r = _run(lambda s: "The fee is $100, billed yearly." if "$50" in s else _clean(s), bad, *S_FILL)
    assert r.rejected == 1 and r.accepted == 3
    assert bad in r.payload["body_markdown"] and "$100" not in r.payload["body_markdown"]


def test_c_dropped_link_rejected():
    bad = "See [the terms](https://example.com/terms) — they apply."
    p, r = _run(lambda s: "See the terms, they apply." if "terms" in s else _clean(s), bad, *S_FILL)
    assert r.rejected == 1 and r.accepted == 3
    assert bad in r.payload["body_markdown"]


def test_d_clean_rewrite_accepted():
    p, r = _run(_clean, *S_FILL)
    assert r.rewritten and r.accepted == 3 and r.rejected == 0
    assert r.flags_after < r.flags_before and r.refused_reason is None
    assert "Fees are low, billed yearly." in r.payload["body_markdown"]
    assert "—" in p["body_markdown"]                       # input never mutated
    assert r.payload["title"] == p["title"] and r.payload["description"] == p["description"]
    assert r.cost_usd == 0.01


def test_e_garbage_refused_payload_unchanged():
    p = _payload(*S_FILL)
    before = copy.deepcopy(p)
    r = hg.humanize(p, call=lambda *a: ("not json at all", {}, 0.0))
    assert r.accepted == 0 and r.refused_reason.startswith("G37:score_after:")
    assert r.payload == before and p == before and not r.rewritten


def test_f_under_threshold_no_call():
    stub = _stub(_clean)
    p = {"intro_paragraph": "The card costs $50. Cashback is 3 percent."}
    r = hg.humanize(p, call=stub)
    assert len(stub.calls) == 0 and not r.rewritten and r.payload == p


def test_system_prompt_is_voice_spec_plus_editor():
    stub = _stub(_clean)
    hg.humanize(_payload(*S_FILL), call=stub)
    assert len(stub.calls) == 1 and stub.calls[0][0].startswith(hg.VOICE_SPEC)
    assert "copy editor, not an author" in stub.calls[0][0]
