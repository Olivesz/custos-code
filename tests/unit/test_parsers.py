from receipts.parsers import (
    BIN_MARKER,
    PARSERS,
    RC_MARKER,
    RunnerResult,
    first_token,
    is_known_runner_token,
    is_trusted_runner_path,
    parse,
    parse_cargo,
    parse_go_test,
    parse_jest,
    parse_pytest,
    parse_vitest,
    strip_and_parse_trailer,
    wrap_command_for_resolution,
)

# --- E2: per-runner output parsers ---


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


# --- E5: runner-binary resolution and wrapper-shadowing detection ---


def test_first_token_plain() -> None:
    assert first_token("pytest -q") == "pytest"


def test_first_token_strips_cd_prefix() -> None:
    assert first_token("cd sub/dir && pytest -q") == "pytest"


def test_first_token_strips_env_assignment() -> None:
    assert first_token("CI=true pytest -q") == "pytest"


def test_first_token_strips_known_invoker() -> None:
    assert first_token("uv run pytest -q") == "pytest"
    assert first_token("npx jest") == "jest"
    assert first_token("poetry run pytest") == "pytest"


def test_first_token_keeps_relative_and_absolute_paths() -> None:
    assert first_token("./pytest -q") == "./pytest"
    assert first_token("/usr/bin/pytest -q") == "/usr/bin/pytest"


def test_first_token_empty_command() -> None:
    assert first_token("") is None
    assert first_token("   ") is None


def test_is_known_runner_token_matches_on_basename() -> None:
    assert is_known_runner_token("pytest")
    assert is_known_runner_token("./pytest")
    assert is_known_runner_token("/usr/bin/pytest")
    assert not is_known_runner_token("ls")
    assert not is_known_runner_token("./run_all_the_things.sh")


def test_wrap_command_for_resolution_known_runner() -> None:
    wrapped = wrap_command_for_resolution("pytest -q")
    assert wrapped is not None
    assert "pytest -q" in wrapped
    assert BIN_MARKER in wrapped
    assert RC_MARKER in wrapped
    assert "command -v pytest" in wrapped


def test_wrap_command_for_resolution_ignores_non_runner() -> None:
    assert wrap_command_for_resolution("ls -la") is None
    assert wrap_command_for_resolution("git status") is None


def test_wrap_command_for_resolution_refuses_unsafe_shapes() -> None:
    assert wrap_command_for_resolution("pytest <<EOF\nfoo\nEOF") is None
    assert wrap_command_for_resolution("pytest -q &") is None
    assert wrap_command_for_resolution("pytest -q & echo done") is None


def test_strip_and_parse_trailer_roundtrip() -> None:
    output = f"12 passed\n{BIN_MARKER}/usr/bin/pytest\n{RC_MARKER}0\n"
    clean, bin_path, rc = strip_and_parse_trailer(output)
    assert clean == "12 passed"
    assert bin_path == "/usr/bin/pytest"
    assert rc == 0


def test_strip_and_parse_trailer_no_markers() -> None:
    clean, bin_path, rc = strip_and_parse_trailer("collected 0 items")
    assert clean == "collected 0 items"
    assert bin_path is None
    assert rc is None


def test_strip_and_parse_trailer_resolution_failed() -> None:
    output = f"{BIN_MARKER}\n{RC_MARKER}127\n"
    clean, bin_path, rc = strip_and_parse_trailer(output)
    assert clean == ""
    assert bin_path is None  # empty command -v output means "not found"
    assert rc == 127


def test_strip_and_parse_trailer_malformed_rc() -> None:
    output = f"{BIN_MARKER}/usr/bin/pytest\n{RC_MARKER}not-a-number\n"
    _, bin_path, rc = strip_and_parse_trailer(output)
    assert bin_path == "/usr/bin/pytest"
    assert rc is None


def test_is_trusted_runner_path_resolution_failed() -> None:
    assert is_trusted_runner_path(None, "/repo") is False


def test_is_trusted_runner_path_outside_repo_tree(tmp_path) -> None:  # type: ignore[no-untyped-def]
    repo_root = tmp_path / "repo"
    outside_bin = tmp_path / "usr" / "bin" / "pytest"
    assert is_trusted_runner_path(str(outside_bin), str(repo_root)) is True


def test_is_trusted_runner_path_inside_repo_untrusted(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # The adversarial case: an agent-authored ./pytest shadowing the real one.
    repo_root = tmp_path / "repo"
    fake_pytest = repo_root / "pytest"
    assert is_trusted_runner_path(str(fake_pytest), str(repo_root)) is False


def test_is_trusted_runner_path_venv_allowlisted(tmp_path) -> None:  # type: ignore[no-untyped-def]
    repo_root = tmp_path / "repo"
    venv_pytest = repo_root / ".venv" / "bin" / "pytest"
    assert is_trusted_runner_path(str(venv_pytest), str(repo_root)) is True


def test_is_trusted_runner_path_node_modules_bin_allowlisted(tmp_path) -> None:  # type: ignore[no-untyped-def]
    repo_root = tmp_path / "repo"
    jest_bin = repo_root / "node_modules" / ".bin" / "jest"
    assert is_trusted_runner_path(str(jest_bin), str(repo_root)) is True


def test_is_trusted_runner_path_documented_override(tmp_path) -> None:  # type: ignore[no-untyped-def]
    repo_root = tmp_path / "repo"
    script = repo_root / "scripts" / "test.sh"
    assert is_trusted_runner_path(str(script), str(repo_root)) is False
    assert is_trusted_runner_path(str(script), str(repo_root), documented_runners=frozenset({"test.sh"})) is True
