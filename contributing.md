
# contributing

## philosophy

nonon favours small, focused changes over large multi-feature pull requests. every change should be motivated by a concrete use case, include appropriate tests, and leave the codebase in at least as clean a state as it was found.

---

## development setup

```bash
git clone https://github.com/ayteeayar/nonon
cd nonon
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

dev dependencies (from `pyproject.toml`):

- `pytest` + `pytest-asyncio` + `pytest-cov` — test runner
- `mypy` — static type checking
- `ruff` — linter and formatter

---

## development workflow

1. create a branch from `main`
2. make changes
3. add or update tests in `tests/`
4. run checks before committing:

```bash
ruff check .            # lint
mypy .                  # type-check
pytest                  # test suite
```

5. commit and open a pull request

---

## branching strategy

| branch | purpose |
|---|---|
| `main` | stable, production-ready |
| `feat/<name>` | new features |
| `fix/<name>` | bug fixes |
| `chore/<name>` | tooling, deps, docs |
| `refactor/<name>` | internal restructuring with no behaviour change |

branches are merged into `main` via pull request. direct pushes to `main` are not accepted.

---

## commit conventions

use the following prefix format:

```
feat: add /markov persona loadfile command
fix: prevent duplicate webhook creation on fast reconnect
chore: bump py-cord to 2.6.1
docs: update architecture diagram
refactor: extract permission check into resolver method
test: add coverage for csv import dry_run path
```

keep subject lines under 72 characters. use the body for context when the change is non-obvious.

---

## pull request process

1. open a draft pr early if the change is non-trivial and you want early feedback
2. fill in the pr description — what changed, why, and how to test it
3. ensure all checks pass (lint, types, tests)
4. request review from a maintainer
5. address review comments before merging
6. squash or rebase before merging if commit history is noisy

---

## adding a new cog

1. create a new module (e.g. `myfeature/cog.py`) with a `setup(bot)` function
2. add the module string to the `cog_modules` list in `core/bot.py`
3. add any new config fields to the appropriate pydantic model in `core/config.py`
4. if the feature requires new database tables, add a migration file in `database/migrations/`
5. document commands in `docs/usage.md`

---

## adding a database migration

1. create `database/migrations/NNN_description.sql` where `NNN` is the next sequential number
2. end the file with:

```sql
INSERT OR IGNORE INTO schema_migrations (version) VALUES ('NNN');
```

3. test the migration against both a fresh and an existing database
4. document the migration in `docs/` if it changes user-facing behaviour

---

## code style

- line length: 100 (ruff enforced)
- target version: python 3.12
- type annotations: required on all public functions and methods
- logging: always use `structlog` with keyword context — never `print()`
- async: all i/o must be non-blocking; no `time.sleep()` or synchronous file i/o on the event loop
- secrets: never hardcode tokens or credentials; always reference environment variable names in config models
