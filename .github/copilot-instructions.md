# Copilot instructions for Practice_Repo

## What this repo is
A small Python tool that reads GitHub Actions workflow runs through the GitHub REST API and prints success, failure, and duration summaries. The repo also holds practice work for containers (Dockerfile and a GHCR workflow), Terraform (`infra/`), and Kubernetes (`k8s/`). It is a learning project, not production code.

## Stack and commands
- Python 3.14. Dependencies are managed with uv (`pyproject.toml` and `uv.lock`). Use `uv add` and `uv sync`, not pip.
- The main script is `workflow_run_stats.py`. Run it with `uv run python workflow_run_stats.py` after `GITHUB_TOKEN` is set in the environment.
- Tests, when present, use pytest and live in `tests/`. Run them with `uv run pytest`.
- HTTP calls use `requests` with a retry session. Keep timeouts and `raise_for_status()` on every request.

## Code style
- Write small functions that do one job. Prefer passing values in and returning results over module-level global variables.
- Handle HTTP errors explicitly. Do not swallow exceptions.
- Add type hints and a short docstring to new functions.
- Do not change the printed output or the `summary.csv` layout unless the task asks for it.
- Tests must not make network calls. Mock the HTTP session.

## Security rules
- Never write a token, password, or key into any file, test, or comment. `GITHUB_TOKEN` comes from the environment only.
- Do not read or print `.env`.
- Do not add a dependency without saying why it is needed.
- In GitHub Actions workflows, pin third party actions to a full commit SHA and keep `permissions:` as narrow as possible.
- Do not edit `.github/workflows/`, `Dockerfile`, `infra/`, or `k8s/` unless the task says to. Never run `terraform apply`, `terraform destroy`, or `kubectl delete`.

## Pull requests
- One purpose per pull request. Say what changed, why, and what was deliberately left out.
- Do not suggest changes to branch rules or repository settings.
