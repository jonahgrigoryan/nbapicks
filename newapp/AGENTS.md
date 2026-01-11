# Repository Guidelines

## Project Structure & Module Organization
- `predictor/` holds the CLI package, including `__main__.py` (entry point), `model.py` (win-probability logic), and `data_fetcher.py` (API clients).
- `tests/` contains pytest suites (`test_model.py`, `test_integration.py`, `test_smoke.py`).
- `logs/` stores JSONL prediction logs generated at runtime.
- `config.json` overrides model defaults; `.env` (local only) stores API keys.
- `README.md` and `predictor.md` document usage and model details.

## Build, Test, and Development Commands
- `python -m venv venv` and `source venv/bin/activate`: create and activate a local virtual env.
- `pip install -r requirements.txt`: install runtime dependencies.
- `python -m predictor --list`: list today’s games.
- `python -m predictor --game 12345`: analyze a single game once.
- `python -m predictor --game 12345 --poll`: live polling mode.
- `pytest tests/ -v`: run the full test suite.

## Coding Style & Naming Conventions
- Python style follows PEP 8 with 4-space indentation.
- Use `snake_case` for functions/modules and `CamelCase` for classes.
- Keep module responsibilities focused (data fetch, model math, display, logging), mirroring the existing file layout.
- No formatter or linter is configured; match the existing style and line breaks.

## Testing Guidelines
- Test framework: pytest.
- Naming: files are `test_*.py`, test functions use `test_*`.
- Focus new tests near existing coverage (`tests/test_model.py` for math, `tests/test_integration.py` for mocked APIs).
- Smoke tests (`pytest tests/test_smoke.py -v -m smoke`) require valid API keys.

## Commit & Pull Request Guidelines
- Commit history uses short, imperative, sentence-case subjects (e.g., “Add…”, “Delete…”). Follow that pattern.
- PRs should include a concise summary, test status (commands run or not run), and note any config/API changes.
- If CLI output or UX changes, include a brief before/after sample in the PR description.

## Security & Configuration Tips
- Keep API keys in `.env` and out of commits.
- Use `config.json` for local overrides instead of hardcoding model constants.
- Treat `logs/` as runtime artifacts; avoid committing new log files unless required.
