# code-review-kit

Configurable multi-persona code review CLI (`crk`). Python 3.12+, built with `uv`.

## Stack

- **Language**: Python 3.12+
- **Package manager**: `uv` (build backend: `uv_build`)
- **Test runner**: `pytest` (`tests/` dir), plus `pytest-xdist`, `pytest-forked`, `pytest-mock`
- **Type checking**: `pyrefly`
- **Lint/format**: `ruff` (line-length 120)
- **Mutation testing**: `mutmut`, scoped to `src/code_review/review_planner/`

## Conventions

- Source lives under `src/code_review/`; entry points are `crk` and `code-review` (`code_review.cli:main`).
- Ruff and pyrefly are run via `uvx` (not installed as project dev dependencies).

## Commands

```bash
uv run pytest -q tests        # tests
uvx ruff check                # lint
uvx ruff format --check       # format check
uvx pyrefly check             # type check
uvx bandit -r src -ll         # security
uvx mutmut run                # mutation testing
```

## Activated agent templates

- `python-quality` — type hints, exception handling, modern idioms, dataclass/Pydantic patterns
