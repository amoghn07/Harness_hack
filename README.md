# Repo Health Monitor & Auto-Fixer

An agent that watches GitHub repos, detects stale issues / outdated deps,
auto-drafts PRs, and posts weekly health reports — no human in the loop.

## Status

- ✅ **Phase 1 — Ingest & store**
- ✅ **Phase 2 — Score & detect** (health score 0–100, scheduled, Bedrock-gated)
- ✅ **Phase 3 — Act & publish** (auto-PRs, stale-issue close, weekly report)

## Phase 1: ingest & store

Pulls a repo's issues, open-PR count, dependency manifests, and recent CI runs,
then writes them to a queryable store that becomes the agent's **memory**. Every
later phase reads from this store via SQL aggregation instead of hitting GitHub
live.

It runs **out of the box with zero dependencies** — the default backends are a
deterministic mock GitHub connector and a stdlib `sqlite3` store.

```bash
# Ingest a repo's snapshot into the local store
python -m repohealth ingest --repo acme/widget

# Read back the stored health signals
python -m repohealth show --repo acme/widget
```

### Schema (mirrors the spec)

- `events(repo, event_type, timestamp, payload)` — append-only firehose
- `issues(repo, id, state, age_days, labels, …)`
- `deps(repo, name, current_ver, latest_ver, outdated, ecosystem, source_file)`
- `ci_runs(repo, branch, status, timestamp, workflow)`

Ingest is **idempotent** per repo: derived tables are replaced on re-run.

## Phase 2: score & detect

A scheduled agent runs four SQL aggregations over the stored memory and folds
them into a single **0–100 health score**. When the score drops below a
threshold (default 60), it collects the specific offenders and escalates to AWS
Bedrock for a remediation plan — so inference cost stays ≈0 on healthy repos.

```bash
# Just the score breakdown (reads the existing snapshot)
python -m repohealth score --repo acme/widget

# Full cycle the scheduled agent runs: fetch -> score -> decide -> act
python -m repohealth run --repo acme/widget
python -m repohealth run --repo acme/widget --no-fetch   # score what's stored
python -m repohealth run --repo acme/widget --no-act     # analyze, don't act
```

### Signals & weights

| Signal                       | Weight | Aggregation                              |
| ---------------------------- | ------ | ---------------------------------------- |
| Stale issues (>90d open)     |  30%   | stale ÷ open issues                      |
| Outdated dependencies        |  30%   | outdated ÷ total deps                    |
| CI red rate (last 30 runs)   |  25%   | AVG(status = 'failure')                  |
| Commit / activity inactivity |  15%   | days since last activity, ramped to 30d  |

Each signal maps to a 0–1 *badness*; `score = round(100 · (1 − Σ wᵢ·badnessᵢ))`.
A perfectly healthy repo scores 100. The SQL is written to run unchanged on both
SQLite and ClickHouse (no dialect-specific date math — recency is computed in
Python from `MAX(timestamp)`).

### Orchestration (Guild AI)

`orchestrator.run_cycle()` is the `fetch → score → decide → act` sequence Guild
AI drives on a 24h cron (Render). Two gates keep cost and side effects in check:
Bedrock (`bedrock.py`) only fires when `score < threshold` (*decide*), and the
real actions only run when escalated *and* not a dry run (*act*). The score is
recorded to history every cycle regardless, so the report's trend keeps filling
in even on healthy repos.

## Phase 3: act & publish

When the gate trips and Bedrock has a plan, Composio executes three real actions
— behind one `Actuator` interface, so the mock exercises the whole path offline:

1. **Auto-draft a bump PR** per outdated dep — branch `bot/bump-{package}-
   {version}`, body carries a changelog diff summary
   ([`changelog_summary`](repohealth/actions.py)).
2. **Close stale issues** — comments *"Closing as stale after 90 days; reopen if
   still relevant"*, applies the `stale` label, then closes.
3. **Publish the weekly report** — posts a Markdown summary (score, a trend
   sparkline pulled from the stored score history, and links to the drafted PRs)
   to a GitHub Discussion or a Notion page.

```bash
# Render just the report markdown from the stored snapshot + history
python -m repohealth report --repo acme/widget
```

The trend chart reads the append-only `scores` table, written by `run_cycle`
every cycle. `report.py` renders a dependency-free unicode sparkline that shows
in both GitHub Discussions and Notion.

## Architecture

The pipeline depends only on small interfaces, so mock → real is a config flip:

| Concern            | Interface           | Mock (default)         | Real (stubbed)            |
| ------------------ | ------------------- | ---------------------- | ------------------------- |
| GitHub data        | `GitHubConnector`   | `MockGitHubConnector`  | `ComposioGitHubConnector` |
| Storage / memory   | `Storage`           | `SQLiteStorage`        | `ClickHouseStorage`       |
| Latest versions    | `VersionRegistry`   | `MockVersionRegistry`  | `HttpVersionRegistry`     |
| Remediation        | `BedrockAnalyzerBase` | `MockBedrockAnalyzer` | `BedrockAnalyzer`        |
| Act & publish      | `Actuator`          | `MockActuator`         | `ComposioActuator`        |

The Composio / ClickHouse / Bedrock classes are guided stubs — the exact
actions, DDL, and request bodies are written inline; wiring them is mechanical
once credentials exist. `pip install -r requirements.txt` is only needed for the
real backends.

## Configuration & secrets

Everything is env-driven with safe defaults — **nothing set = full mock stack**.
Copy the template and fill in only what you switch to real:

```bash
cp .env.example .env
```

| Backend selector       | Values                | Credentials needed         |
| ---------------------- | --------------------- | -------------------------- |
| `REPOHEALTH_GITHUB`    | `mock` \| `composio`  | `COMPOSIO_API_KEY`         |
| `REPOHEALTH_STORE`     | `sqlite` \| `clickhouse` | `CLICKHOUSE_*`          |
| `REPOHEALTH_REGISTRY`  | `mock` \| `http`      | none (hits npm/PyPI)       |
| `REPOHEALTH_INFERENCE` | `mock` \| `bedrock`   | `AWS_*` + `BEDROCK_MODEL_ID` |
| `REPOHEALTH_ACTIONS`   | `mock` \| `composio`  | `COMPOSIO_API_KEY`         |

`REPOHEALTH_SCORE_THRESHOLD` (default 60) sets the Bedrock escalation gate.
`REPOHEALTH_REPORT_TARGET` (`github` \| `notion`) picks where the weekly report
publishes — Notion adds `NOTION_API_KEY` / `NOTION_PARENT_PAGE_ID`. See
[`.env.example`](.env.example) for every key and [`render.yaml`](render.yaml)
for the scheduled-agent deploy (secrets live in the Render dashboard, never in
git).

## Tests

```bash
python -m pip install pytest
python -m pytest tests/ -q
```

## Layout

```
repohealth/
    __main__.py        CLI (connect, ingest, show, score, run, report)
    config.py          env-driven backend selection + credentials
    models.py          Event, Issue, Dep, CiRun
    ingest.py          Phase 1 pipeline + backend factories
    parsers.py         package.json / requirements.txt parsing
    registry.py        latest-version lookup (mock + http)
    scoring.py         Phase 2: four signals -> 0-100 health score
    detect.py          Phase 2: collect the specific offenders
    bedrock.py         Phase 2: remediation analysis (mock + Bedrock)
    actions.py         Phase 3: Actuator — bump PRs, close issues, publish (mock + Composio)
    report.py          Phase 3: weekly Markdown report + trend sparkline
    orchestrator.py    fetch -> score -> decide -> act cycle (all phases)
    connect.py         Composio GitHub OAuth link / status
    connectors/        GitHubConnector: base, mock, composio stub
    storage/           Storage: base, sqlite, clickhouse stub (+ scores history)
tests/
    test_ingest.py     Phase 1 end-to-end + unit
    test_scoring.py    Phase 2 scoring, detection, orchestration
    test_actions.py    Phase 3 actions, report, act-and-publish cycle
.env.example           all credentials, documented
render.yaml            scheduled-agent (cron) deploy blueprint
```
