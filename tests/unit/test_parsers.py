from receipts.parsers import (
    BIN_MARKER,
    RC_MARKER,
    first_token,
    is_known_runner_token,
    is_trusted_runner_path,
    strip_and_parse_trailer,
    wrap_command_for_resolution,
)


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
