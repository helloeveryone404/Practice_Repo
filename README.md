# Practice_Repo

A small Python tool, plus the container, pipeline, infrastructure, and Kubernetes work I built around it to practice. This is practice work, not production.

## What the tool does

`workflow_run_stats.py` reads the workflow runs of a public repository (`actions/runner`) through the GitHub REST API. It counts successes and failures per workflow file, compares how long successful and failed runs take, and writes `summary.csv`. It needs a `GITHUB_TOKEN` environment variable. A token with no permissions is enough for public data.

Known limits:
- GitHub returns at most 1,000 results per filtered query, so on a busy repo the 90 day window is cut short. The tool prints a warning when this happens. The fix is to split the date range into chunks.
- Durations come from `updated_at`, which is only an approximate end time. Runs that were closed in bulk show very long durations.

## What is where

| Path | What it is |
|---|---|
| `workflow_run_stats.py`, `pyproject.toml`, `uv.lock` | The tool and its locked dependencies (managed with `uv`) |
| `Dockerfile` | Container image: Python slim, dependencies from the lock file, runs as a non-root user |
| `.github/workflows/push_to_GHCR.yml` | Builds the image, scans it with Trivy, and pushes it to GHCR on pushes to `main` and manual runs |
| `.github/copilot-instructions.md` | Example custom instructions for GitHub Copilot |
| `infra/terraform/` | Terraform (Docker provider) that runs one nginx container |
| `k8s/` | Kubernetes manifests for a local kind cluster: a Job, a CronJob, and an nginx Deployment with a Service |
| `docs/ai-guardrails.md` | An example draft of guardrails for AI-assisted development |

## Run the tool

```
uv sync
export GITHUB_TOKEN=<your token>
uv run python workflow_run_stats.py
```

In Docker:

```
docker build -t practice-repo:0.1.0 .
mkdir -p out
docker run --rm -e GITHUB_TOKEN --user "$(id -u):$(id -g)" -v "$PWD/out:/data" practice-repo:0.1.0
```

## Terraform

Needs Docker running locally.

```
cd infra/terraform
terraform init
terraform plan -out=tfplan
terraform apply tfplan
terraform destroy
```

State and plan files are ignored by git. `.terraform.lock.hcl` is committed so the provider version stays pinned.

## Kubernetes

Uses a local [kind](https://kind.sigs.k8s.io/) cluster. Load the image with `kind load docker-image`, create the token Secret yourself (it is never stored in a file), and apply the manifests in `k8s/`:

```
kubectl create secret generic github-token --from-literal=GITHUB_TOKEN="$GITHUB_TOKEN"
kubectl apply -f k8s/job.yaml
kubectl apply -f k8s/cronjob.yaml
kubectl apply -f k8s/nginx.yaml
```

The Job and CronJob run as a numeric non-root user, do not mount a service account token, and have CPU, memory, and ephemeral-storage settings. SonarCloud flagged the missing token and storage settings on the pull request, and I fixed them before merging. The nginx Deployment uses a rolling update with `maxUnavailable: 0` and `maxSurge: 1`, and I practiced a failed release (a tag that does not exist) and a rollback with `kubectl rollout undo`.

## About this repo

I built it to practice containers, GitHub Actions, Terraform, and Kubernetes. Some steps were done with an AI coding assistant under written limits: every command was approved by me first, and it was kept to one folder with no access to secrets.
