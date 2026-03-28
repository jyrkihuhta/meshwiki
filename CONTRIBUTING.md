# Contributing to MeshWiki

Thank you for your interest in contributing to MeshWiki! This guide will help you get started.

## Getting Started

### Prerequisites

- **Python 3.12+**
- **Rust** (install via [rustup](https://rustup.rs/)) — needed for the graph engine
- **Maturin** — installed automatically by `dev.sh`, or `pip install maturin`
- **Git**

### Development Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/jyrkihuhta/meshwiki.git
   cd meshwiki
   ```

2. **Start the development server (with Rust engine):**
   ```bash
   ./dev.sh
   ```
   This builds the Rust graph engine, installs Python dependencies, and starts the server at http://localhost:8000.

3. **Or run without the Rust engine** (graph features degrade gracefully):
   ```bash
   cd src
   pip install -e ".[dev]"
   uvicorn meshwiki.main:app --reload
   ```

4. **Install dev tools:**
   ```bash
   pip install black ruff isort
   ```

### Running Tests

```bash
# Python tests (204 tests)
cd src
pytest tests/ -v
pytest tests/ --cov=meshwiki    # With coverage

# Rust graph engine tests (70 tests)
cd graph-core
source .venv/bin/activate
PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1 maturin develop
python -m pytest tests/ -v
```

All tests must pass before submitting a PR. CI enforces 80% minimum coverage on Python code.

## How to Contribute

### Reporting Bugs

- Use the [Bug Report](https://github.com/jyrkihuhta/meshwiki/issues/new?template=bug_report.md) issue template
- Include steps to reproduce, expected vs actual behavior, and your environment details
- Check existing issues first to avoid duplicates

### Suggesting Features

- Use the [Feature Request](https://github.com/jyrkihuhta/meshwiki/issues/new?template=feature_request.md) issue template
- Describe the use case and why it would benefit users
- Check the [roadmap](TODO.md) to see if it's already planned

### Submitting Code

1. **Fork the repository** and create a branch from `main`:
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Make your changes** following the coding standards below.

3. **Write tests** for any new functionality.

4. **Run the full test suite** to make sure nothing is broken:
   ```bash
   cd src && pytest tests/ -v
   ```

5. **Format and lint your code:**
   ```bash
   black src/
   isort src/
   ruff check src/
   ```

6. **Commit with a clear message** (see [commit guidelines](#commit-messages)).

7. **Push and open a pull request** against `main`.

### Workflow Guidelines

**Keep features on a single branch:**
- Create a feature branch and keep working on it until the feature is complete
- Only open a PR when the feature is fully implemented, tested, and formatted
- Use `git rebase origin/main` to stay up-to-date during development

**Before opening a PR:**
```bash
# Stay up-to-date with main
git fetch origin main
git rebase origin/main

# Validate before pushing
./scripts/validate-branch.sh
```

**PR Merge Strategy:**
- We use **squash and merge** — your PR becomes one commit on main
- This means you can commit freely without worrying about "perfect history"
- Keep commits focused within a feature branch, but don't worry about rebasing to clean up commits before PR

## Coding Standards

### Python

- **Style:** PEP 8, formatted with `black` (88 char line length)
- **Imports:** Sorted with `isort` (profile: black)
- **Linting:** `ruff` for fast linting
- **Type hints:** Required for all function signatures
- **Docstrings:** Google style for public functions and classes
- **Async:** Use `async/await` for all storage operations
- **Naming:**
  - `snake_case` for functions, variables, modules
  - `PascalCase` for classes
  - `UPPER_CASE` for constants
  - Prefix private methods/attributes with `_`

### Rust (graph-core)

- Follow standard Rust conventions (`cargo fmt`, `cargo clippy`)
- PyO3 bindings in `lib.rs`
- Tests are Python integration tests in `graph-core/tests/`

### Templates (Jinja2 + HTMX)

- Base template: `templates/base.html`
- HTMX partials in `templates/partials/`
- Dark mode via `[data-theme="dark"]` CSS custom properties
- Minimal custom CSS, no framework

### Testing

- **Framework:** pytest + pytest-asyncio + httpx
- **Naming:** `test_<module>.py` files in `tests/`
- **Coverage:** Aim for >80% on new code
- Write unit tests for core logic and integration tests for API routes
- Include edge cases and error conditions

### Commit Messages

- Use imperative mood: "Add feature" not "Added feature"
- Keep the subject line under 72 characters
- Reference issues when applicable: "Fix search pagination (#42)"
- Keep commits focused and atomic

## Project Structure

```
meshwiki/
├── dev.sh                  # Development startup script
├── graph-core/             # Rust graph engine (petgraph + PyO3)
│   ├── src/                # Rust source
│   └── tests/              # Integration tests (70 tests)
├── src/meshwiki/          # Python application
│   ├── main.py             # FastAPI routes
│   ├── core/               # Storage, parser, graph wrapper
│   ├── templates/          # Jinja2 templates
│   ├── static/             # CSS + JS (D3.js, editor)
│   └── tests/              # Python tests (204 tests)
├── docs/                   # Documentation
├── deploy/                 # Kubernetes manifests
└── infra/                  # Terraform infrastructure
```

See [architecture.md](docs/architecture.md) for a detailed system overview.

## Pull Request Process

1. Ensure all tests pass and code is formatted.
2. Update documentation if your change affects behavior (docs, README, CLAUDE.md).
3. Fill out the PR template with a summary, test plan, and any relevant context.
4. A maintainer will review your PR. Address any feedback and push updates.
5. Once approved, your PR will be merged into `main`.

## Development Tips

- The Rust graph engine is **optional** — the app works without it, so you can contribute to the Python side without installing Rust.
- Use `./dev.sh --skip-build` to start the server without rebuilding Rust if you're only working on Python/templates.
- HTMX requests are detected via the `HX-Request` header — routes return partials for HTMX and full pages for regular requests.
- Check `CLAUDE.md` for detailed gotchas and conventions.

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).
