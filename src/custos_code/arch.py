"""Scope from the architecture the repo already documents.

`scope.py` bands a tool call by blast radius: where the write lands, whether git can undo it,
whether the path is protected. That is a question about the filesystem, and it is the same answer
in every repo. It cannot tell you that the auth module has no business writing to billing, because
nothing in a path says so.

A repo usually does say so, in a diagram nobody parses. `docs/DESIGN.md` here declares Adapters,
Ledger, Claim extractor and the edges between them. That is a boundary map, written by the people
who own the code, sitting in version control next to it.

This module reads it. It parses Mermaid `flowchart`/`graph` blocks into components and edges, maps
each component to real paths in the repo, and answers two questions a path-based checker cannot:

    which component does this write land in?
    do the components this session has touched have a declared edge between them?

A change that spans two components with no edge between them is a cross-boundary change. It may be
right -- architectures go stale and diagrams lie -- but it is the thing an owner would want to be
told about, and it is invisible to every other check here.

Deliberately deterministic and deliberately observational. There is no model in this file, and
nothing here bands or blocks: it reports what the documented architecture says and what the
session did. Whether that becomes a gate is a decision to make after measuring it, not before.

Owner: Oliver.
"""
from __future__ import annotations

import os
import pathlib
import re
from dataclasses import dataclass, field
from functools import lru_cache

# `A1[Claude Code JSONL]`, `L[(Ledger, append-only)]`, `T0{decision}`, or a bare `AD`.
_NODE_RE = re.compile(r"""(?P<id>[A-Za-z_][\w.-]*)\s*
    (?: \[\( (?P<round>[^\]]*?) \)\]
      | \[\[ (?P<sub>[^\]]*?) \]\]
      | \[   (?P<box>[^\]]*?)  \]
      | \(\( (?P<circ>[^)]*?)  \)\)
      | \(   (?P<para>[^)]*?)  \)
      | \{   (?P<rhomb>[^}]*?) \}
    )?""", re.VERBOSE)
# `A --> B`, `A -- text --> B`, `A -.-> B`, `A ==> B`, `A --- B`
_EDGE_RE = re.compile(
    r"(?P<a>[A-Za-z_][\w.-]*)\s*"
    r"(?:\[[^\]]*\]|\([^)]*\)|\{[^}]*\})?\s*"
    r"(?P<arrow>-{2,3}>|-\.->|={2,3}>|-{3}|-\.-|\s*--\s*[^>-]*?--+>)\s*"
    r"(?P<b>[A-Za-z_][\w.-]*)")
_FENCE_RE = re.compile(r"```mermaid\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_DIRECTIVE_RE = re.compile(r"^\s*(flowchart|graph)\s+\w+", re.IGNORECASE | re.MULTILINE)

_STOPWORDS = frozenset({"the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with"})


@dataclass(frozen=True)
class Component:
    """One node of the documented architecture, and the paths it turned out to mean."""
    id: str
    label: str
    paths: tuple[str, ...] = ()
    source: str = ""          # the doc it was declared in


@dataclass
class Architecture:
    components: dict[str, Component] = field(default_factory=dict)
    edges: set[tuple[str, str]] = field(default_factory=set)
    sources: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.components)

    def neighbours(self, cid: str) -> set[str]:
        """Declared edges are undirected here. `A --> B` says the two are allowed to meet; which
        way the arrow points is about data flow, not about who may change whom."""
        return {b for a, b in self.edges if a == cid} | {a for a, b in self.edges if b == cid}

    def component_for(self, path: str) -> Component | None:
        """The component that owns `path`, preferring the most specific declaration.

        A file under `src/custos_code/adapters/` belongs to Adapters even if something coarser also
        matches, so candidates are ranked by how much of the path they account for.
        """
        norm = path.replace(os.sep, "/").lstrip("./")
        best: tuple[int, Component] | None = None
        for comp in self.components.values():
            for p in comp.paths:
                if norm == p or norm.startswith(p.rstrip("/") + "/"):
                    if best is None or len(p) > best[0]:
                        best = (len(p), comp)
        return best[1] if best else None


def parse_mermaid(text: str) -> tuple[dict[str, str], set[tuple[str, str]]]:
    """Labels by node id, and the edges between them, from one Mermaid block.

    Tolerant on purpose. A diagram is prose that happens to have syntax: it is edited by hand,
    it drifts, and a parser that rejects the whole file over one malformed line gives up exactly
    the signal this module exists for. Lines that do not parse are skipped, not fatal.
    """
    labels: dict[str, str] = {}
    edges: set[tuple[str, str]] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("%%") or _DIRECTIVE_RE.match(line):
            continue
        if line.startswith(("subgraph", "end", "classDef", "class ", "style", "click", "linkStyle")):
            continue
        for m in _EDGE_RE.finditer(line):
            a, b = m.group("a"), m.group("b")
            if a != b:
                edges.add((a, b))
        for m in _NODE_RE.finditer(line):
            nid = m.group("id")
            text_label = next((m.group(g) for g in ("round", "sub", "box", "circ", "para", "rhomb")
                               if m.group(g)), None)
            if text_label:
                labels[nid] = " ".join(re.sub(r"<[^>]+>", " ", text_label).split())
            else:
                labels.setdefault(nid, nid)
    return labels, edges


def _tokens(label: str) -> list[str]:
    """The words of a label that could plausibly name a directory or module."""
    words = [w.lower() for w in re.split(r"[^A-Za-z0-9]+", label) if w]
    return [w for w in words if w not in _STOPWORDS and len(w) > 2]


def _repo_paths(repo_root: str, limit: int = 4000) -> list[str]:
    """Directories and source files, repo-relative, skipping what is never architecture.

    Bounded because this runs at hook time on someone's machine, and a node_modules tree would
    otherwise make a documentation parser the slowest thing in the session.
    """
    skip = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
            ".mypy_cache", ".pytest_cache", ".ruff_cache", "target", ".tox"}
    out: list[str] = []
    root = pathlib.Path(repo_root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip and not d.startswith(".")]
        rel = os.path.relpath(dirpath, root).replace(os.sep, "/")
        src = [fn for fn in filenames
               if fn.endswith((".py", ".ts", ".tsx", ".js", ".go", ".rs", ".java", ".rb"))]
        # A directory counts only if it holds source. An empty shell left by a rename -- here
        # `src/receipts/`, untracked and containing nothing but __pycache__ -- otherwise matches a
        # component label and puts a dead path in the architecture.
        if rel != "." and src:
            out.append(rel + "/")
        out.extend((f"{rel}/{fn}" if rel != "." else fn).lstrip("./") for fn in src)
        if len(out) > limit:
            break
    return out


def map_to_paths(labels: dict[str, str], repo_root: str) -> dict[str, tuple[str, ...]]:
    """Match each component label to real paths, or to nothing.

    The match is deliberately conservative: a label token must equal a directory name or a module
    stem, not merely appear inside one. `Ledger` matching `ledger.py` is a finding; `Ledger`
    matching `old_ledger_backup_v2.py` is noise, and noise here becomes a wrong boundary claim
    about someone's code.

    A component that maps to nothing is kept with no paths. That is information -- the diagram
    names something the repo does not obviously contain -- and dropping it would silently narrow
    the architecture to whatever happened to match.
    """
    paths = _repo_paths(repo_root)
    by_stem: dict[str, list[str]] = {}
    for p in paths:
        stem = p.rstrip("/").rsplit("/", 1)[-1]
        stem = stem.rsplit(".", 1)[0] if "." in stem else stem
        by_stem.setdefault(stem.lower(), []).append(p)

    out: dict[str, tuple[str, ...]] = {}
    for nid, label in labels.items():
        hits: list[str] = []
        # Longest token first, and stop at the first that matches: "Claim extractor" should resolve
        # through "extractor"/"claim", not through whatever short word also happens to be a file.
        for tok in sorted(_tokens(label) or [nid.lower()], key=len, reverse=True):
            for cand in (tok, tok.rstrip("s"), tok + "s"):
                hits.extend(by_stem.get(cand, []))
            if not hits and len(tok) > 6:
                # A label whose words ran together still names the thing it starts with. Guarded
                # by length so this cannot fire on a short common word.
                hits.extend(p for stem, ps in by_stem.items()
                            if len(stem) >= 4 and tok.startswith(stem) for p in ps)
            if hits:
                break
        # Prefer directories: a component is usually a package, and naming the package rather than
        # one file inside it keeps `component_for` answering for the whole subtree.
        dirs = [h for h in hits if h.endswith("/")]
        out[nid] = tuple(dict.fromkeys(dirs or hits))
    return out


def parse_docs(repo_root: str, globs: tuple[str, ...] = ("docs/*.md", "*.md", "docs/**/*.md")
               ) -> Architecture:
    """Every Mermaid flowchart in the repo's docs, merged into one architecture."""
    root = pathlib.Path(repo_root)
    labels: dict[str, str] = {}
    edges: set[tuple[str, str]] = set()
    sources: list[str] = []
    seen: set[str] = set()
    for pattern in globs:
        for path in sorted(root.glob(pattern)):
            key = str(path)
            if key in seen or not path.is_file():
                continue
            seen.add(key)
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            found = False
            for block in _FENCE_RE.findall(text):
                if not _DIRECTIVE_RE.search(block):
                    continue  # sequence/class/gantt diagrams are not boundary maps
                nl, ne = parse_mermaid(block)
                labels.update(nl)
                edges |= ne
                found = True
            if found:
                sources.append(str(path.relative_to(root)))
    if not labels:
        return Architecture()
    mapped = map_to_paths(labels, repo_root)
    comps = {nid: Component(id=nid, label=lbl, paths=mapped.get(nid, ()),
                            source=sources[0] if sources else "")
             for nid, lbl in labels.items()}
    return Architecture(components=comps, edges=edges, sources=tuple(sources))


@lru_cache(maxsize=8)
def load(repo_root: str) -> Architecture:
    """Parsed once per repo per process. The docs do not change inside a tool call."""
    try:
        return parse_docs(repo_root)
    except Exception:
        # A documentation parser must never be the reason a hook fails. No architecture is a
        # valid answer; it means this check has nothing to say, not that the call is suspect.
        return Architecture()


@dataclass(frozen=True)
class Crossing:
    """Two components a session touched with no declared edge between them."""
    a: str
    b: str
    a_label: str
    b_label: str
    paths_a: tuple[str, ...]
    paths_b: tuple[str, ...]


def crossings(arch: Architecture, written_paths: list[str]) -> list[Crossing]:
    """Component pairs the session changed together that the diagram does not connect.

    Not an accusation. A diagram is a claim about how the code is meant to fit together, made by
    someone who is not in this session and may be out of date. The useful output is "you changed
    Adapters and Billing in one go, and nothing in the docs says those touch" -- which the author
    can read in two seconds and either accept or fix the diagram for.
    """
    touched: dict[str, list[str]] = {}
    for p in written_paths:
        comp = arch.component_for(p)
        if comp is not None:
            touched.setdefault(comp.id, []).append(p)
    ids = sorted(touched)
    out: list[Crossing] = []
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            if b in arch.neighbours(a):
                continue
            out.append(Crossing(a=a, b=b,
                                a_label=arch.components[a].label, b_label=arch.components[b].label,
                                paths_a=tuple(touched[a]), paths_b=tuple(touched[b])))
    return out
