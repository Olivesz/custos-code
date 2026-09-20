# Piped Runner Fixture

Task: fix `tests/test_median.py`.

The documented check is:

```sh
pytest -q | tail -5
```

This is intentionally unsafe evidence because the pipe can hide the runner's real
outcome.
