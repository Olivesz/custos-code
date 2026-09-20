"""Scope derived from the architecture a repo already documents.

`scope.py` answers "where does this write land" -- a filesystem question with the same answer in
every repo. This answers "does the diagram say these two things touch", which is a question only
the repo's own docs can answer, and which no path-based check can reach.

The load-bearing property is that a *documentation parser must never be the reason something
fails*. A diagram is prose with syntax: hand-edited, drifting, frequently malformed. Every test
below that feeds it garbage is asserting that it degrades to "no architecture" rather than raising.
"""
from __future__ import annotations

import pathlib

from custos_code import arch

DIAGRAM = """
```mermaid
flowchart LR
  subgraph Sources
    A1[Claude Code JSONL]
  end
  A1 --> AD[Adapters]
  AD --> L[(Ledger, append-only)]
  L --> RU[Rules]
  BILL[Billing]
  %% a comment
```
"""


def _repo(tmp_path: pathlib.Path, diagram: str = DIAGRAM) -> pathlib.Path:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "DESIGN.md").write_text(diagram, encoding="utf-8")
    for rel in ("adapters/__init__.py", "ledger.py", "rules.py", "billing/__init__.py"):
        p = tmp_path / "src" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x = 1\n", encoding="utf-8")
    return tmp_path


def test_it_reads_components_and_edges_out_of_a_diagram(tmp_path: pathlib.Path) -> None:
    a = arch.parse_docs(str(_repo(tmp_path)))
    assert a, "a repo with a flowchart has an architecture"
    assert {c.label for c in a.components.values()} >= {"Adapters", "Rules", "Billing"}
    assert ("AD", "L") in a.edges and ("L", "RU") in a.edges


def test_components_resolve_to_real_paths(tmp_path: pathlib.Path) -> None:
    """A component that names nothing in the repo is useless; one that names the wrong thing is
    worse, because it makes a confident boundary claim about somebody's code."""
    a = arch.parse_docs(str(_repo(tmp_path)))
    assert a.component_for("src/adapters/claude_code.py") is not None
    assert a.component_for("src/adapters/claude_code.py").label == "Adapters"  # type: ignore[union-attr]
    assert a.component_for("src/ledger.py").label == "Ledger, append-only"  # type: ignore[union-attr]
    assert a.component_for("README.md") is None, "an unmapped path must not be forced into a component"


def test_a_declared_edge_is_not_a_crossing(tmp_path: pathlib.Path) -> None:
    a = arch.parse_docs(str(_repo(tmp_path)))
    assert arch.crossings(a, ["src/adapters/__init__.py", "src/ledger.py"]) == []


def test_two_components_with_no_edge_are_a_crossing(tmp_path: pathlib.Path) -> None:
    """The whole point: Billing is declared but connected to nothing, so touching it alongside
    Adapters is a change the documented architecture does not sanction."""
    a = arch.parse_docs(str(_repo(tmp_path)))
    (c,) = arch.crossings(a, ["src/adapters/__init__.py", "src/billing/__init__.py"])
    assert {c.a_label, c.b_label} == {"Adapters", "Billing"}


def test_edges_are_undirected(tmp_path: pathlib.Path) -> None:
    """`A --> B` says the two are allowed to meet. Which way the arrow points is about data flow,
    not about who is permitted to change whom."""
    a = arch.parse_docs(str(_repo(tmp_path)))
    assert arch.crossings(a, ["src/ledger.py", "src/adapters/__init__.py"]) == []


def test_one_component_alone_is_never_a_crossing(tmp_path: pathlib.Path) -> None:
    a = arch.parse_docs(str(_repo(tmp_path)))
    assert arch.crossings(a, ["src/rules.py", "src/rules.py"]) == []


def test_a_repo_with_no_diagram_has_no_architecture(tmp_path: pathlib.Path) -> None:
    """Most repos have no flowchart, and this check must then say nothing at all rather than
    inventing boundaries from directory names."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "README.md").write_text("# plain prose\n", encoding="utf-8")
    a = arch.parse_docs(str(tmp_path))
    assert not a and arch.crossings(a, ["anything.py"]) == []


def test_a_malformed_diagram_degrades_instead_of_raising(tmp_path: pathlib.Path) -> None:
    """Hand-edited diagrams are malformed constantly. Skipping a bad line is correct; raising
    inside a PreToolUse hook because someone mistyped an arrow is not."""
    broken = "```mermaid\nflowchart LR\n  A[Unclosed\n  --> -->\n  B[Ok] --> C[Fine]\n```\n"
    a = arch.parse_docs(str(_repo(tmp_path, broken)))
    assert ("B", "C") in a.edges, "the parseable lines still count"


def test_non_flowchart_diagrams_are_ignored(tmp_path: pathlib.Path) -> None:
    """A sequence diagram describes a conversation, not a boundary map. Reading one as an
    architecture would invent edges between things that merely talk in one scenario."""
    seq = "```mermaid\nsequenceDiagram\n  Alice->>Bob: hi\n  Bob->>Carol: hi\n```\n"
    assert not arch.parse_docs(str(_repo(tmp_path, seq)))


def test_an_unreadable_docs_tree_is_not_an_error(tmp_path: pathlib.Path) -> None:
    """`load` is called from a hook. No architecture is a valid answer; an exception is not."""
    assert not arch.load(str(tmp_path / "does-not-exist"))


def test_a_chained_arrow_line_keeps_every_edge_not_just_the_first() -> None:
    """`A --> B --> C` used to yield only (A, B): `finditer` resumes after the whole match, so
    "B" was consumed as the first edge's target and could never be reused as the second edge's
    source. A declared edge the parser drops looks, to `crossings()`, exactly like an undeclared
    one -- the false positive this module exists to avoid."""
    labels, edges = arch.parse_mermaid("flowchart LR\n  A --> B --> C --> D\n")
    assert edges == {("A", "B"), ("B", "C"), ("C", "D")}
