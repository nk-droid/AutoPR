# AutoPR - AI-Powered GitHub Issue-to-PR Automation

AutoPR turns GitHub webhook events into queued workflow runs. The system triages issues, plans code changes, generates patches, runs QA gates, publishes branches, opens pull requests, reviews merge readiness, and merges approved pull requests when remote GitHub actions are enabled.

The implementation uses FastAPI, Redis, Ray, LangGraph, SQLAlchemy/PostgreSQL, OpenTelemetry, Prometheus, Tempo, Loki, and Grafana. The codebase is organized around production backend concerns: event intake, queue isolation, durable run state, agent orchestration, QA execution, policy gates, observability, and human review.

---

## Architecture

![AutoPR Architecture](docs/architecture.png)

The detailed architecture note lives in `docs/architecture.md`.

---

## What AutoPR Does

AutoPR runs two workflow types:

| Run type | Trigger | Ordered stages |
| -------- | ------- | -------------- |
| `ISSUE_TO_PR` | `issues` webhooks for opened and reopened issues | `triage -> prepare -> plan -> code -> qa -> publish -> pr_open -> review -> merge` |
| `PR_TO_MERGE` | approved `pull_request_review` webhooks with `AUTOPR_WEBHOOK_MERGE_ON_APPROVAL=true` | `review -> merge` |

The pipeline:

1. Accepts GitHub webhook events through FastAPI.
2. Verifies the webhook signature when `GITHUB_WEBHOOK_SECRET` is set.
3. Filters unsupported events and disabled workflow triggers.
4. Enqueues accepted work in Redis.
5. Reserves queued work in the worker process.
6. Dispatches each job through the coordinator and registered pipeline steps.
7. Runs focused LangGraph agents through Ray workers.
8. Runs lint, test, coverage, and security QA jobs.
9. Stores run snapshots, events, stage artifacts, review requests, and dead-letter records in PostgreSQL.
10. Emits logs, traces, and metrics through the observability stack.
11. Creates resumable review gates for stages that return `needs_review`.

---

## Core Components

### 1. API Service

The FastAPI service receives GitHub webhooks, creates queued work, exposes run lookup, exposes Prometheus metrics, and applies internal review decisions.

Routes:

```text
POST /webhooks/github
GET  /runs/{run_id}
GET  /internal/review/decision
GET  /metrics
```

### 2. Redis Queue

`infra/redis/webhook_queue.py` implements the Redis-backed queue. It separates pending, processing, and dead-letter jobs with these configured keys:

```text
autopr:webhook:queue
autopr:webhook:processing
autopr:webhook:dlq
```

The queue stores webhook jobs and human-review resume jobs. Failed jobs re-enter the pending queue until `AUTOPR_WEBHOOK_MAX_ATTEMPTS` is reached, then move to the dead-letter queue.

### 3. Worker Dispatcher

`apps/worker/main.py` runs the dispatcher loop. It reserves messages from Redis, dispatches webhook or resume jobs, acknowledges successful work, requeues failed work, records dead-letter rows, and sends dead-letter notifications.

This process runs separately from the API service, so webhook intake stays isolated from LLM calls, repository operations, and QA execution.

### 4. Coordinator

`core/orchestrator/coordinator.py` executes the workflow. It owns the run model, validates state transitions, executes registered pipeline steps, persists every stage result, handles QA retry loops, creates review requests, and records final run state.

The registered steps live in `core/orchestrator/steps/registry.py`.

### 5. Agent Runtime

AutoPR uses focused LangGraph agents instead of one monolithic prompt.

Implemented agents:

| Agent | Package | Responsibility |
| ----- | ------- | -------------- |
| `TriageAgent` | `core/agents/triage` | Extracts the task from the GitHub issue, assesses risk, detects ambiguity, and emits a triage result. |
| `PlanAgent` | `core/agents/plan` | Drafts implementation steps, maps dependencies, checks ambiguity, and emits a plan output. |
| `CodeAgent` | `core/agents/code` | Understands each plan step, locates relevant files, generates code changes, validates the output, and emits a code output. |
| `QAAgent` | `core/agents/qa` | Evaluates code output against lint, test, coverage, and security tool results, then returns structured QA checks. |
| `PublishAgent` | `core/agents/publish` | Resolves a workspace, applies generated files, commits changes, and pushes the implementation branch. |
| `PRAgent` | `core/agents/pr` | Builds the pull request payload, opens the PR, and records the PR URL and number. |
| `ReviewAgent` | `core/agents/review` | Evaluates PR merge readiness, runs an LLM merge-risk review, and returns required actions. |
| `MergeAgent` | `core/agents/merge` | Prepares merge metadata, merges the approved PR, and returns the merge result. |

Ray workers instantiate these agents in `infra/ray/actors.py`.

### 6. Repository and QA Execution

Repository operations live under `infra/repo_worker` and the publish agent. QA execution lives under `infra/qa` and `infra/ray/jobs/qa.py`.

The QA layer runs:

| Check | Module |
| ----- | ------ |
| Lint | `infra/qa/lint_runner.py` |
| Tests | `infra/qa/test_runner.py` |
| Coverage | `infra/qa/coverage_runner.py` |
| Security | `infra/qa/security_runner.py` |

### 7. PostgreSQL Run Store

PostgreSQL is the durable run store. `infra/storage/schema.py` defines these tables:

| Table | Stored data |
| ----- | ----------- |
| `runs` | Latest run snapshot, run type, repository, issue number, pull request number, and serialized run payload. |
| `run_events` | Append-only timeline events for state transitions, stage results, review gates, and resume decisions. |
| `artifacts` | Named stage artifacts keyed by run id. |
| `review_requests` | Pending, decided, and applied human-review requests. |
| `dead_letter_jobs` | Failed queue messages that exhausted retry attempts. |

`configs/settings.py` provides PostgreSQL connection defaults, and `docker/docker-compose.yml` starts Postgres 16 plus pgAdmin.

### 8. LLM Gateway

`infra/llm` provides provider selection, model configuration, rate limits, pricing metadata, callbacks, and gateway execution.

Configured providers:

| Provider | Models in `configs/llm_models.yaml` |
| -------- | ----------------------------------- |
| Ollama | `qwen3-coder`, `deepseek-r1` |
| Anthropic | `sonnet`, `opus` |
| Google | `pro`, `flash` |
| OpenAI | `gpt-4.1`, `gpt-4.1-mini` |

`AUTOPR_LLM_PROVIDER` and `AUTOPR_LLM_MODEL_NAME` select the default provider and model. The gateway enforces requests-per-minute and concurrency caps from `configs/llm_models.yaml`.

### 9. Policy and Human Review

`core/policies/engine.py` evaluates deterministic merge policy checks. It blocks high-risk automerge, sensitive path changes, and non-green QA results according to `configs/policies.yaml`.

The coordinator creates human-review requests for publish gates and LLM soft gates. `GET /internal/review/decision` records approved or disapproved decisions and enqueues approved resume jobs.

### 10. Observability

The local Docker stack starts the full observability path:

| Tool | Role |
| ---- | ---- |
| OpenTelemetry Collector | Receives app traces, metrics, and logs. |
| Prometheus | Stores metrics and scrapes collector output. |
| Tempo | Stores traces. |
| Loki | Stores logs. |
| Grafana | Displays dashboards and data sources. |

Provisioned dashboards live in `docker/grafana/dashboards`.

---

## Remote Action Control

`AUTOPR_EXECUTE_REMOTE_ACTIONS` controls write operations against GitHub and remote repositories.

| Value | Behavior |
| ----- | -------- |
| unset or false | Publish, PR creation, and merge agents prepare outputs and skip remote writes. |
| true | Publish, PR creation, and merge agents execute remote GitHub or Git operations. |

This flag keeps local development deterministic and prevents accidental repository writes.

---

## Tech Stack

| Area | Technology |
| ---- | ---------- |
| API | FastAPI |
| Queue | Redis |
| Worker runtime | Ray |
| Agent orchestration | LangGraph |
| LLM integration | LangChain-compatible providers |
| Persistence | SQLAlchemy + PostgreSQL |
| QA execution | subprocess-backed lint, tests, coverage, and security runners |
| Observability | OpenTelemetry, Prometheus, Tempo, Loki, Grafana |
| Local infrastructure | Docker Compose |

---

## Repository Structure

```text
autopr/
├── apps/
│   ├── api/              FastAPI app and routes
│   ├── cli/              GitHub operations CLI
│   └── worker/           Redis worker dispatcher
├── configs/              Settings, LLM models, and policy configuration
├── core/
│   ├── agents/           LangGraph agents
│   ├── contracts/        Pydantic contracts and enums
│   ├── orchestrator/     Coordinator, state machine, transitions, and steps
│   └── policies/         Merge policy checks and review comment formatting
├── docker/               Compose stack, service Dockerfiles, observability config
├── docs/                 Architecture documentation and diagram
├── infra/
│   ├── github/           Webhook handling and GitHub API client
│   ├── llm/              LLM registry, gateway, rate limits, callbacks
│   ├── qa/               Lint, test, coverage, security, and sandbox runners
│   ├── ray/              Ray actors and runtime hooks
│   ├── redis/            Webhook queue implementation
│   ├── repo_worker/      Repository workspace and Git helpers
│   ├── slack/            Review and dead-letter notifications
│   └── storage/          SQLAlchemy schema and persistence helpers
├── observability/        Logging, metrics, and tracing helpers
├── scripts/              Local utility scripts
└── tests/                Unit, integration, and e2e tests
```

---

## Getting Started

### 1. Create Environment File

```bash
cp .env.example .env
```

Set the provider keys for the LLM backend you use:

```env
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
GOOGLE_API_KEY=
GITHUB_WEBHOOK_SECRET=
GITHUB_TOKEN=
```

The Docker stack supplies Redis and PostgreSQL connection URLs for the API and worker services.

### 2. Start the Stack

```bash
docker compose -f docker/docker-compose.yml up --build
```

Services:

```text
API Docs:    http://localhost:8000/docs
Metrics:     http://localhost:8000/metrics
Grafana:     http://localhost:3000
Prometheus:  http://localhost:9090
pgAdmin:     http://localhost:5050
Ray:         http://localhost:8265
```

### 3. Run Locally Without Docker

Start Redis and PostgreSQL first, then run the API and worker:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,llm]"

make run-api
make run-worker
```

---

## Running Tests

```bash
pytest
```

Run linting and formatting:

```bash
ruff check .
ruff format .
```

---

## Configuration

| File | Purpose |
| ---- | ------- |
| `configs/settings.py` | Database settings and app defaults. |
| `configs/llm_models.yaml` | Provider models, pricing metadata, RPM caps, and concurrency caps. |
| `configs/policies.yaml` | Merge policy gates and sensitive path patterns. |
| `.env.example` | Local environment template. |
| `docker/docker-compose.yml` | Local service topology. |

---

## Highlights

This project demonstrates:

* Event-driven GitHub webhook intake.
* Redis-backed asynchronous processing.
* Multi-agent LLM orchestration.
* LangGraph-based stage graphs.
* Ray-backed worker isolation.
* PostgreSQL-backed run persistence.
* Human-review gates with resumable runs.
* Deterministic merge policy checks.
* Model-level LLM rate limiting.
* OpenTelemetry-based traces, logs, and metrics.
* Grafana dashboards for queue, stage, pipeline, trace, and LLM inspection.

---

## Planned Work

* Expand retry policies beyond QA retries and parser retries.
* Add a dedicated dead-letter queue dashboard.
* Add evaluation metrics for generated code quality.
* Add more webhook-to-run e2e coverage.

---

## Non-Goals

The current scope excludes:

* Scheduler-based execution.
* JWT authentication.
* Cloud secret manager integration.
* Kubernetes deployment.

---

