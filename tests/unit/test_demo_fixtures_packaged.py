"""Every scenario `demo` offers must ship in the wheel.

`demo` is the first thing someone runs after `pip install custos-code`, and it reads its fixtures
from the installed package. A scenario whose fixture lives only in `eval/` works perfectly from a
checkout and fails for every real user -- which is exactly what happened when the default scenario
changed to `failing-suite` and nobody repackaged.

The test is cheap and the failure it prevents is the worst first impression the product can make.
"""
from __future__ import annotations

from importlib import resources

import pytest

from custos_code import cli


def _scenarios() -> dict[str, str]:
    """The same mapping `demo` uses, read from the source so it cannot drift from this test."""
    import inspect
    import re
    src = inspect.getsource(cli.demo)
    block = re.search(r"picks = \{(.*?)\}", src, re.S)
    assert block, "could not find the scenario table in cli.demo"
    return dict(re.findall(r'"([\w-]+)":\s*"([\w-]+)"', block.group(1)))


def test_the_scenario_table_is_not_empty() -> None:
    """Guards the guard: a regex that matched nothing would make every check below vacuous."""
    assert len(_scenarios()) >= 4


@pytest.mark.parametrize("scenario,fixture", sorted(_scenarios().items()))
def test_each_demo_scenario_ships_its_fixture(scenario: str, fixture: str) -> None:
    packaged = resources.files("custos_code.demo_fixtures").joinpath(f"{fixture}.jsonl")
    assert packaged.is_file(), (
        f"--scenario {scenario} reads {fixture}.jsonl, which is not in the package. "
        f"Copy it into src/custos_code/demo_fixtures/."
    )
