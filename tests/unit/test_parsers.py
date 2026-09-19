from receipts.parsers import (
    PARSERS,
    RunnerResult,
    parse,
    parse_cargo,
    parse_go_test,
    parse_jest,
    parse_pytest,
    parse_vitest,
)


def test_parse_pytest_normal_run() -> None:
    stdout = (
        "============================= test session starts ==============================\n"
        "collected 12 items\n\nFFFF........\n\n"
        "===================== 1 failed, 11 passed in 0.42s ======================\n"
    )
    assert parse_pytest(stdout, 1) == RunnerResult(
        runner="pytest", passed=11, failed=1, errors=0, collected=12, skipped=0
    )


def test_parse_pytest_collected_zero_trap() -> None:
    """`collected 0 items` with exit 0 must not read as a clean pass (DESIGN.md T1)."""
    stdout = (
        "============================= test session starts ==============================\n"
        "collected 0 items\n\n"
        "============================== no tests ran in 0.01s ==============================\n"
    )
    result = parse_pytest(stdout, 0)
    assert result is not None
    assert result.collected == 0
    assert result.passed == 0
    assert result.failed == 0


def test_parse_pytest_returns_none_for_non_pytest_output() -> None:
    assert parse_pytest("Tests:  1 failed, 4 passed, 5 total\n", 1) is None


def test_parse_jest() -> None:
    stdout = "Test Suites: 1 failed, 1 total\nTests:       1 failed, 4 passed, 5 total\n"
    assert parse_jest(stdout, 1) == RunnerResult(runner="jest", passed=4, failed=1, errors=0, collected=5, skipped=0)


def test_parse_jest_returns_none_without_a_tests_line() -> None:
    assert parse_jest("no summary here\n", 1) is None


def test_parse_vitest() -> None:
    stdout = " Test Files  1 failed | 1 (2)\n      Tests  1 failed | 9 passed (10)\n"
    assert parse_vitest(stdout, 1) == RunnerResult(
        runner="vitest", passed=9, failed=1, errors=0, collected=10, skipped=0
    )


def test_parse_vitest_returns_none_without_a_tests_line() -> None:
    assert parse_vitest("no summary here\n", 1) is None


def test_parse_go_test_verbose() -> None:
    stdout = "=== RUN   TestFoo\n--- PASS: TestFoo (0.00s)\n=== RUN   TestBar\n--- FAIL: TestBar (0.00s)\nFAIL\n"
    assert parse_go_test(stdout, 1) == RunnerResult(
        runner="go test", passed=1, failed=1, errors=0, collected=2, skipped=0
    )


def test_parse_go_test_plain_package_summary_has_no_per_test_count() -> None:
    stdout = "ok  \tmypkg\t0.005s\n"
    assert parse_go_test(stdout, 0) == RunnerResult(
        runner="go test", passed=1, failed=0, errors=0, collected=None, skipped=0
    )


def test_parse_go_test_returns_none_for_unrelated_output() -> None:
    assert parse_go_test("nothing to see here\n", 0) is None


def test_parse_cargo_sums_multiple_test_targets() -> None:
    stdout = (
        "test result: FAILED. 1 passed; 1 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.01s\n"
        "test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s\n"
    )
    assert parse_cargo(stdout, 101) == RunnerResult(
        runner="cargo", passed=2, failed=1, errors=0, collected=3, skipped=0
    )


def test_parse_cargo_returns_none_without_a_result_line() -> None:
    assert parse_cargo("running 1 test\ntest a ... ok\n", 0) is None


def test_parse_dispatch_finds_the_matching_runner() -> None:
    stdout = (
        "============================= test session starts ==============================\n"
        "collected 1 item\n\n.\n\n===================== 1 passed in 0.01s ======================\n"
    )
    result = parse(stdout, 0)
    assert result is not None
    assert result.runner == "pytest"


def test_parse_dispatch_tries_pytest_first() -> None:
    assert PARSERS[0] is parse_pytest


def test_parse_dispatch_returns_none_when_no_parser_matches() -> None:
    assert parse("unrecognised output format\n", 1) is None
