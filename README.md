# AutoPR

An autonomous **issue-to-merge** system. A GitHub issue comes in; a pipeline of coordinated agents triages it, plans the work, writes the code, runs quality gates, opens a pull request, reviews it, and merges it — with policy gates that pull a human in when the risk is too high.

```bash
make run-api      # FastAPI intake on :8000
make run-worker   # queue worker that drains and dispatches runs
```

---

## Architecture

![AutoPR architecture](docs/architecture.png)

A run flows top-down through five layers:

```
GitHub webhook  /  manual API call  /  CLI
        │
        ▼
API & Intake          ← FastAPI: verify, filter, enqueue
        │
        ▼
Async Job Queue       ← Redis list + processing list + dead-letter queue
        │
        ▼
Orchestrator          ← Coordinator advances a validated StateMachine
        │
        ├── per stage: PipelineStep → Ray actor → Agent (LangGraph)
        │
        ▼
Data & Persistence    ← SQLite: runs, run events, stage artifacts
```

**Layer resolution:** the API only *parses and enqueues* — it never runs a pipeline inline. The worker reserves a job from Redis, hands it to a `Coordinator`, and the coordinator walks the registered `PipelineStep`s for that run type. Each step dispatches its agent as a Ray remote actor, collects a typed `StageResult`, persists it, and asks the policy layer whether the run may transition.

---

## Pipeline

A run has one of two types, each a fixed sequence of stages:

| Run type | Stages |
|----------|--------|
| `ISSUE_TO_PR` | `triage → plan → code → qa → publish → pr_open` |
| `PR_TO_MERGE` | `review → merge` |