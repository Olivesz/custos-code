"""A hands-on tour of what this ships, on a repo built in front of you.

Every line below runs against a real git work tree created in a temp directory, with a real
architecture diagram and real commands. Nothing is replayed and nothing is mocked. Each check
prints what it expected and what it got, so a wrong answer is visible rather than narrated.

Run:  .venv/bin/python eval/walkthrough.py
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from custos_code import arch, scope, watchdog  # noqa: E402

DIAGRAM = """# Architecture

```mermaid
flowchart LR
  API[Api] --> SVC[Services]
  SVC --> STORE[Store]
  BILLING[Billing]
```
"""

FILES = {
    "api/__init__.py": "def handler():\n    return 1\n",
    "services/__init__.py": "def run():\n    return 1\n",
    "store/__init__.py": "DB = {}\n",
    "billing/__init__.py": "RATE = 0.2\n",
    "docs/ARCH.md": DIAGRAM,
}

PASS = "\033[32m ok \033[0m" if sys.stdout.isatty() else " ok "
FAIL = "\033[31mFAIL\033[0m" if sys.stdout.isatty() else "FAIL"
_failures = 0


def check(label: str, got: object, want: object, note: str = "") -> None:
    global _failures
    ok = got == want
    _failures += not ok
    print(f"  [{PASS if ok else FAIL}] {label}")
    print(f"         expected {want!r}, got {got!r}" + (f"  — {note}" if note else ""))


def section(n: int, title: str, why: str) -> None:
    print(f"\n\033[1m{n}. {title}\033[0m" if sys.stdout.isatty() else f"\n{n}. {title}")
    print(f"   {why}\n")


def build(root: pathlib.Path) -> None:
    for rel, body in FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "init"]):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, env=env)


def main() -> int:
    root = pathlib.Path(tempfile.mkdtemp(prefix="custos-tour-"))
    build(root)
    print(f"built a real git repo at {root}")
    print("  api/ -> services/ -> store/ are connected in docs/ARCH.md; billing/ is not")

    # ---------------------------------------------------------------- 1. the diagram
    section(1, "It reads the architecture out of a Mermaid diagram",
            "No model. It parses docs/*.md and matches component labels to real directories.")
    a = arch.load(str(root))
    print(f"   parsed {len(a.components)} components, {len(a.edges)} edges from {a.sources}")
    for c in sorted(a.components.values(), key=lambda c: c.id):
        print(f"     {c.id:8} {c.label:10} -> {', '.join(c.paths) or '(nothing in this repo)'}")
    check("Api resolves to api/", a.component_for("api/__init__.py").label, "Api")       # type: ignore[union-attr]
    check("an unlisted file belongs to no component", a.component_for("README.md"), None)

    # ---------------------------------------------------------------- 2. crossings
    section(2, "It knows which components the diagram says may touch",
            "A change spanning two components with no declared edge is a crossing.")
    connected = arch.crossings(a, ["api/__init__.py", "services/__init__.py"])
    crossing = arch.crossings(a, ["api/__init__.py", "billing/__init__.py"])
    check("Api + Services (edge declared) is not a crossing", len(connected), 0)
    check("Api + Billing (no edge) is a crossing", len(crossing), 1,
          crossing[0].a_label + " <-> " + crossing[0].b_label if crossing else "")

    # ---------------------------------------------------------------- 3. scope
    section(3, "Scope bands a command by blast radius",
            "The first two were the bugs: a pipe laundered a write, and a read was called RED.")
    g = scope.Grant.for_session(str(root))
    cases = [
        ("cat api/__init__.py | tee /Users/x/.zshrc", "yellow", "was GREEN: pipes laundered writes"),
        ("ls > /Users/x/.zshrc",                      "yellow", "redirect makes any command a write"),
        ("cat ~/.ssh/config 2>/dev/null",             "green",  "was RED: 2> is not a write"),
        ("pytest -q 2>&1",                            "green",  "nor is 2>&1"),
        ("git push --force origin main",              "red",    "irreversible, wherever it points"),
        ("rm -rf /tmp/scratch/build",                 "green",  "agents clean up after themselves"),
        ("rm -rf /tmp",                               "red",    "a scratch ROOT is not disposable"),
    ]
    for cmd, want, why in cases:
        got = scope.classify("Bash", {"command": cmd}, g).band.value
        check(f"{cmd[:44]:46} {why}", got, want)

    # ---------------------------------------------------------------- 4. watchdog
    section(4, "The watchdog decides, and only a measured detector may deny",
            "Scope reads the filesystem, so RED can refuse. A diagram can only ever ask.")
    fs = watchdog.Observation(detector="scope", severity="deny", rule="git-force-push",
                              detail="irreversible", may_deny=True)
    doc = watchdog.Observation(detector="architecture", severity="ask", rule="undeclared-boundary",
                               detail="no edge", may_deny=False)
    check("nothing observed -> allow", watchdog.decide([], unattended=False).decision, "allow")
    check("filesystem evidence can deny", watchdog.decide([fs], unattended=False).decision, "deny")
    check("a diagram can only ask", watchdog.decide([doc], unattended=False).decision, "ask")
    check("unattended, a diagram STILL only asks",
          watchdog.decide([doc], unattended=True).decision, "ask",
          "a stale diagram must not halt a run nobody is watching")
    check("unattended, filesystem evidence escalates",
          watchdog.decide([watchdog.Observation(detector="scope", severity="ask", rule="r",
                                                detail="d")], unattended=True).decision, "deny")
    check("worst wins, in its own words",
          "git-force-push" in watchdog.decide([doc, fs], unattended=False).reason, True)

    print(f"\n{'-' * 76}")
    if _failures:
        print(f"{_failures} check(s) FAILED — do not ship this")
    else:
        print("every check passed")
    print(f"{'-' * 76}")
    print("\nTwo more, which need the CLI and are worth running by hand:\n")
    print(f"  .venv/bin/custos-code arch --repo {root}")
    print(f"  .venv/bin/custos-code arch --repo {root} \\\n"
          f"      --touched 'api/__init__.py,billing/__init__.py'")
    print("  .venv/bin/python eval/scoreboard.py           # the real hooks, end to end, no key")
    print("  .venv/bin/custos-code demo                    # one trap, caught deterministically\n")
    print(f"(the repo is left at {root} so you can poke at it; rm -rf it when done)")
    return 1 if _failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
