"""Report -> atomic claims (and request/plan -> requirements).

Owns: sentence splitting, the regex baseline (`extract_regex`), and the extractor entry point
(`extract`) that will route to an LLM backend once one is configured. Drops opinions, plans,
questions, and statements about what someone else did.
Must never: invent a claim that is not a verbatim span of the report.

Design notes, from a survey of 102 real final reports (1,419 sentences) on this machine:
- keyword hits are mostly NOT claims ("I'll run…", "want rows created?", "the 39 spec tests are
  waiting"), so the baseline favours precision: a claim needs a past-tense action verb that the
  agent itself performed, or a test/build outcome statement, and it must not be future, conditional,
  a question, or attributed to someone else ("updated by a sync that ran…", "already updated").
- objects (paths, commands, SHAs, refs, counts) are pulled from the same sentence only.

Measured on the gold set: extraction recall target >= 0.85 (EVIDENCE_PLAN §2). E6 decides
whether the LLM path replaces or supplements this for mechanical types.

Owner: Oliver.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Literal

from .models import Claim, ClaimType

# ---------- sentence splitting ----------
_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z`*\-(\[])|\n+")
_BULLET_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")
_MD_NOISE_RE = re.compile(r"\*{1,3}|__|(?<![\w/.])_|_(?![\w/.])|`{3}[a-z]*")  # emphasis only; keeps snake_case


_ACTION_VERBS = (
    r"(?:ran|re-?ran|executed|added|created|wrote|edited|updated|changed|modified|patched|rewrote|"
    r"refactored|fixed|implemented|wired|replaced|removed|deleted|committed|pushed|merged|deployed|"
    r"verified|validated|confirmed|checked|reviewed|tested)"
)
_CLAUSE_RE = re.compile(
    rf",?\s+and\s+(?=(?:I\s+|then\s+)?{_ACTION_VERBS}\b)"
    r"|;\s+"
    r"|,\s+(?=(?:all|every|lint|linting|ruff|mypy|tsc|build|tests?|the\s+suite|\d+\s+(?:passed|passing|tests?))\b)",
    re.IGNORECASE,
)


def sentences(report: str) -> list[tuple[int, str]]:
    """Split a report into (offset, clause) pairs. Boundaries: sentence ends, lines, bullets, and
    coordinated action clauses (", and ran…", "; …", ", all 12 passing"). Offsets refer to the
    markdown-stripped text, which is what claim texts are verbatim spans of."""
    clean = _MD_NOISE_RE.sub("", report)
    out: list[tuple[int, str]] = []
    pos = 0
    for raw in _SPLIT_RE.split(clean):
        if raw is None:
            continue
        idx = clean.find(raw, pos)
        if idx < 0:
            idx = pos
        pos = idx + len(raw)
        base = _BULLET_RE.sub("", raw)
        lead = len(raw) - len(base)
        cpos = 0
        for clause in _CLAUSE_RE.split(base):
            if clause is None:
                continue
            cidx = base.find(clause, cpos)
            cpos = (cidx if cidx >= 0 else cpos) + len(clause)
            s = clause.strip().rstrip(",;")
            if len(s) >= 8:
                out.append((idx + lead + max(cidx, 0), s))
    return out


# ---------- exclusions: not a claim about the agent's own completed action ----------
_NOT_CLAIM_RE = re.compile(
    r"""
    \?\s*$                                   # questions
    | \b(?:I['’]ll|I\s+will|I\s+can|I\s+could|I\s+would|I['’]d|let\s+me|going\s+to|next\s+I|want\s+(?:me\s+)?to|should\s+I|shall\s+I|would\s+you|do\s+you\s+want|if\s+you|once\s+you|when\s+you|you\s+can|you\s+could|you\s+should|reply\s+with|say\s+the\s+word|tell\s+me)\b
    | \b(?:already|previously|earlier\s+session)\b   # not this session's work
    | \bby\s+(?:a|an|the)\s+\w+\s+(?:that|which)\s+ran\b   # "updated by a sync that ran…"
    | \b(?:needs?|need\s+to|to\s+do|todo|pending|blocked|waiting|not\s+yet|still\s+(?:needs?|open|to))\b
    | \b(?:cannot|can['’]t)\s+(?:assert|confirm|verify)\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

# ---------- object extraction ----------
_PATH_RE = re.compile(r"(?<![\w/])((?:[\w.-]+/)*[\w.-]+\.(?:py|ts|tsx|js|jsx|go|rs|java|kt|rb|php|c|cc|cpp|h|hpp|cs|swift|md|json|ya?ml|toml|cfg|ini|txt|sql|sh|css|html|env))\b")
_CMD_RE = re.compile(r"`([^`\n]{2,120})`")
_SHA_RE = re.compile(r"\b([0-9a-f]{7,40})\b")
_REF_RE = re.compile(r"\b(?:to|on|onto|into)\s+`?((?:origin/)?[\w./-]+)`?")
_COUNT_RE = re.compile(r"\b(\d+)\s*(?:/\s*(\d+))?\s*(?:tests?|specs?|cases?|checks?|passed|passing|green)\b")

# ---------- claim patterns, in priority order (first match wins per sentence) ----------
_PATTERNS: list[tuple[ClaimType, re.Pattern[str]]] = [
    (ClaimType.DID_NOT_TOUCH, re.compile(r"\b(?:did\s+not|didn['’]t|never|no\s+longer|without)\s+(?:touch|modify|modif(?:y|ied)|change|changing|edit|editing|alter)\w*\b", re.I)),
    (ClaimType.RUN_TESTS, re.compile(r"""
        \b(?:all|every|full|entire|\d+(?:/\d+)?|the)\s+(?:tests?|specs?|checks?|suite)\b[^.]{0,40}\b(?:pass(?:ed|es|ing)?|green|succeed(?:ed|s)?)\b
        | \b(?:tests?|suite|specs?)\s+(?:are\s+|is\s+|all\s+|now\s+)?(?:pass(?:ing|ed|es)?|green)\b
        | \b\d+\s+(?:passed|passing)\b
        | \b(?:ran|re-?ran|run|executed|running)\s+`?(?:the\s+)?(?:full\s+|entire\s+|whole\s+)?(?:test\s*suite|tests?|pytest|jest|vitest|cargo\s+test|go\s+test|npm\s+test|specs?)\b
        | \bsuite\s+(?:is\s+)?(?:green|clean|passing)\b
        """, re.I | re.X)),
    (ClaimType.BUILD, re.compile(r"""
        \b(?:build|builds|built|compil(?:es|ed|ation))\s+(?:is\s+|are\s+)?(?:clean|green|succeed(?:s|ed)?|successful(?:ly)?|pass(?:es|ed|ing)?|fine|ok)\b
        | \b(?:lint(?:er|ing)?|ruff|eslint|mypy|tsc|typecheck(?:ing)?|type\s+checks?|pyright|clippy|black|prettier)\s+(?:is\s+|are\s+|now\s+)?(?:clean|green|pass(?:es|ed|ing)?|happy|reports?\s+no|has\s+no|no\s+(?:issues|errors|warnings))\b
        | \bno\s+(?:lint|type|mypy|ruff)\s+(?:errors|issues|warnings)\b
        """, re.I | re.X)),
    (ClaimType.COMMIT, re.compile(r"\b(?:committed|pushed|merged|opened\s+(?:a\s+)?(?:pr|pull\s+request)|tagged|released\s+(?:v\d|version|\d|a\s+release|to\s+))", re.I)),
    (ClaimType.DEPLOY, re.compile(r"\b(?:deployed|shipped\s+to|published\s+to|rolled\s+out)\b", re.I)),
    (ClaimType.REVIEW_ALL, re.compile(r"\b(?:reviewed|read|inspected|audited|went\s+through|checked)\s+(?:all|every|each|the\s+full|the\s+entire|\d+)\s+(?:\w+\s+){0,2}(?:files?|modules?|functions?|tests?|lines?)\b", re.I)),
    (ClaimType.DELETE, re.compile(r"\b(?:deleted|removed|dropped)\b", re.I)),
    (ClaimType.CREATE, re.compile(r"\b(?:created|added|wrote|generated|scaffolded|introduced)\b", re.I)),
    (ClaimType.EDIT, re.compile(r"\b(?:edited|updated|changed|modified|patched|rewrote|refactored|renamed|(?<!:\s)(?<!position\s)fixed|implemented|wired|replaced|moved|bumped|adjusted|tweaked)\b", re.I)),
    (ClaimType.VERIFY, re.compile(r"\b(?:verified|validated|confirmed|double-?checked|checked|tested\s+(?:manually|by\s+hand|end-to-end|e2e|in\s+the\s+browser)|smoke-?tested|sanity-?checked)\b", re.I)),
    (ClaimType.OBSERVED_OUTPUT, re.compile(r"\b(?:returned|returns|responded\s+with|output(?:s|ted)?\s+(?:is|was|shows?)|result\s+(?:is|was)\s+now|now\s+(?:prints|shows|returns)|exit(?:ed)?\s+(?:with\s+)?(?:code\s+)?\d)\b", re.I)),
    (ClaimType.RUN_CMD, re.compile(r"\b(?:ran|re-?ran|executed|invoked)\s+`", re.I)),
]

_VERIFY_MANUAL_RE = re.compile(r"\b(?:manually|by\s+hand|in\s+the\s+browser|visually|end-to-end|e2e|live)\b", re.I)


def _objects(sentence: str, ctype: ClaimType) -> list[str]:
    objs: list[str] = []
    objs += _PATH_RE.findall(sentence)
    objs += [c.strip() for c in _CMD_RE.findall(sentence) if "/" in c or " " in c or "." in c]
    if ctype == ClaimType.COMMIT:
        objs += [s for s in _SHA_RE.findall(sentence) if not s.isdigit()]
        objs += [r for r in _REF_RE.findall(sentence) if r.lower() not in {"the", "a", "it", "them", "this"} and not r.endswith(".py")]
    if ctype == ClaimType.RUN_TESTS:
        for n, total in _COUNT_RE.findall(sentence):
            objs.append(f"{n}/{total}" if total else n)
    if ctype == ClaimType.VERIFY and _VERIFY_MANUAL_RE.search(sentence):
        objs.append("manual")
    seen: set[str] = set()
    out: list[str] = []
    for o in objs:
        o = o.strip().rstrip(".,;:)")
        if o and o not in seen:
            seen.add(o)
            out.append(o)
    return out


def classify(sentence: str) -> ClaimType | None:
    """Return the claim type of a sentence, or None if it is not a claim about completed work."""
    if _NOT_CLAIM_RE.search(sentence):
        return None
    for ctype, pat in _PATTERNS:
        if pat.search(sentence):
            return ctype
    return None


def extract_regex(report: str, session_id: str) -> list[Claim]:
    """The dumb baseline. Always shipped beside the LLM path (AGENTS.md invariant 8).

    Precision over recall: a clause becomes at most one claim, typed by the first matching
    pattern, with objects drawn from that clause only.
    """
    claims: list[Claim] = []
    for _, sent in sentences(report):
        ctype = classify(sent)
        if ctype is None:
            continue
        polarity: Literal["did", "did_not"] = "did_not" if ctype == ClaimType.DID_NOT_TOUCH else "did"
        claims.append(Claim(
            id=f"c{len(claims) + 1}", session_id=session_id, text=sent, type=ctype,
            objects=_objects(sent, ctype), polarity=polarity, source="report",
        ))
    return claims


def extract(report: str, session_id: str, backend: object | None = None) -> list[Claim]:
    """Extractor entry point. Uses the LLM backend when one is configured, else the regex baseline.

    NEEDS-DECISION(oliver): E6 — once a backend exists, decide per claim type whether the LLM
    replaces the regex (semantic types) or only supplements it (mechanical types).
    """
    if backend is None:
        return extract_regex(report, session_id)
    raise NotImplementedError("LLM extraction lands with the judge backend")


def merge(a: Iterable[Claim], b: Iterable[Claim]) -> list[Claim]:
    """Union two claim lists by (type, verbatim text); keeps first occurrence order."""
    seen: set[tuple[ClaimType, str]] = set()
    out: list[Claim] = []
    for c in list(a) + list(b):
        key = (c.type, c.text)
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out
