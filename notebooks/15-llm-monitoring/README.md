# 15 · LLM monitoring with Evidently

Notebook: [15-llm-monitoring.ipynb](15-llm-monitoring.ipynb) · Code: `src/marginalia/monitoring/`, `docker/monitoring/`

## What it does

Every hour, a job builds an Evidently report on the last 24 hours of answered questions and compares
them with the week before:

- **Outcomes:** the share refused, from the cache, or failed.
- **Quality:** checked as supported by the grader, citing the book, saying the book does not answer.
- **Cost:** tokens per answer, generation speed, time to answer, searches.
- **Text:** answer and question length.
- **Drift:** which of these moved against the reference week.

Each report is stored as a run in the Evidently UI. Its project dashboard charts the runs over time.

## Why?

Prometheus (step 14) answers "how many, how fast, right now". LLM monitoring asks about the answers
themselves, and whether they are changing. A model update, a new prompt or a new kind of reader can
shift quality without a single error:

- more "the book does not say";
- fewer citations;
- longer answers;
- more tokens per answer.

Drift detection compares distributions, not just averages, and says which columns moved. Evidently
gives these reports and their dashboards ready-made, and keeps every report to look back at.

## For what

- Spotting quality drift after a change (model, prompts, retrieval) on real traffic.
- A weekly view of answer behaviour, next to the offline evaluations (step 11).
- Inspecting one report in detail: distributions, the drift tests, text statistics.

## Simple explanation

A quality inspector who, every hour, takes yesterday's answers and puts them next to last week's.
Do they still cite the book as often? Are they longer? Do they cost more? Did the share of refusals
change? The inspector files a report each time; a wall chart shows how the answers to these questions
evolve.

## Examples

| Dashboard panel | Series |
|---|---|
| Outcomes | refused, from the cache, failed (shares) |
| Answer quality | checked as supported, cite the book, say the book does not answer (shares) |
| Tokens per answer | mean, most, fewest |
| Generation speed | tokens per second, mean and slowest |
| Seconds to answer | mean, slowest |
| Searches and answer length | mean searches, mean answer characters |
| Drifted columns | how many columns moved against the reference week |

A test shows the drift check catching tokens per answer moving from about 2,000 to 8,000.

![The project dashboard in the Evidently UI](../../docs/screenshots/evidently-dashboard.png)
*The project dashboard in the Evidently UI.*

![One hourly report](../../docs/screenshots/evidently-report.png)
*One hourly report.*

## Technical

- **Data:** `data.fetch(url, since, until)` reads the questions from `reporting.questions` through the
  read-only `marginalia_reporting` login. `data.to_frame(rows)` makes one row per question, with
  naive UTC times and the outcome and verdict as categories.
- **Report:** `report.build(current, reference)` uses Evidently 0.7.23's new API (`Report`, `Dataset`,
  `DataDefinition`):
  - 0/1 flag columns for the shares (refused, cached, failed, supported, cites the book, says it is
    not in the book), averaged with `MeanValue`;
  - `MeanValue`, `MaxValue`, `MinValue` and `QuantileValue` for tokens, speed, seconds, searches and
    answer length;
  - `TextEvals` with the `TextLength`, `SentenceCount` and `WordCount` descriptors;
  - `DataDriftPreset` against the reference, when it has at least 10 rows.
- **Publishing:** `report.project(workspace)` creates the project "MarginaliaRAG answers" and saves its
  dashboard: `counter_panel` and `line_plot_panel` with `PanelMetric` on the metric types. Runs go to
  the Evidently UI service through `RemoteWorkspace` with a write secret (`CP_EVIDENTLY_SECRET`), or
  to a local workspace folder.
- **Docker:** `evidently` runs the UI (127.0.0.1:8085) and `monitor` the hourly job. Both use the
  `marginalia-monitoring:dev` image, which is separate because Evidently needs pandas < 3 and brings
  scipy and plotly. `DO_NOT_TRACK=1` turns Evidently's telemetry off.
- **No LLM judge here:** Evidently's LLM-judge descriptors use OpenAI by default. The grader's verdict
  already is this system's judge, and DeepEval (step 11) covers offline judging.

## Tech examples

```bash
pip install -e ".[monitoring]"
python -m marginalia.monitoring --once                          # into the Evidently UI
python -m marginalia.monitoring --once --workspace ./evidently-workspace
evidently ui --workspace ./evidently-workspace                  # look at a local workspace
```

```python
from marginalia.monitoring import data, report
snapshot = report.build(data.to_frame(current_rows), data.to_frame(reference_rows))
snapshot        # renders in a notebook
```

## How it is connected to other steps

- **Reads:** the questions the API and worker record (step 12), including the guard and cache outcomes
  (step 10), the grader's verdict and the tokens (step 09).
- **Offline:** complements the golden-set evaluation (step 11) with real traffic.
- **Shares a source with Grafana:** the reporting view behind the token panels (step 14).
- **The admin page:** links to it (**LLM monitoring**).
