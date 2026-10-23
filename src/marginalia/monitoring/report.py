"""The Evidently report on recent answers, and the project dashboard that charts its runs.

Shares (refused, from the cache, supported...) are 0/1 flag columns averaged with MeanValue, so
every panel reads one number per run. Drift compares the current window with the reference week
column by column (numbers, categories, answer length) and counts the columns that moved.
"""

import re

from marginalia.monitoring.data import CATEGORICAL, NUMERICAL, TEXT

PROJECT = 'MarginaliaRAG answers'
MIN_REFERENCE = 10               # fewer reference questions than this: no drift section
_CITES = re.compile(r'\[[^\]]*\b(?:Chapter|Part|Book|Prologue|Epilogue)\b')
_NOT_IN_BOOK = re.compile(r"(?i)\b(?:does|do|did)(?: not|n't) (?:say|mention|specify|describe|answer)\b"
                          r"|\bnot (?:mentioned|stated|found)\b")
FLAGS = ['refused', 'cached', 'failed', 'supported', 'cites_book', 'not_in_book']
SHARES = {'refused': 'refused', 'cached': 'from the cache', 'failed': 'failed', 'supported': 'checked as supported',
          'cites_book': 'cite the book', 'not_in_book': 'say the book does not answer'}


def add_flags(frame):
    """0/1 columns for the shares; NaN where a flag does not apply (no answer to check)."""
    import numpy as np
    answered = frame['outcome'].isin(['agent', 'cache'])
    frame['refused'] = (frame['outcome'] == 'refused').astype(float)
    frame['cached'] = (frame['outcome'] == 'cache').astype(float)
    frame['failed'] = (frame['outcome'] == 'failed').astype(float)
    frame['supported'] = frame['verdict'].map({'supported': 1.0, 'unsupported': 0.0}).astype(float)
    frame['cites_book'] = np.where(answered, frame['answer'].str.contains(_CITES).astype(float), np.nan)
    frame['not_in_book'] = np.where(answered, frame['answer'].str.contains(_NOT_IN_BOOK).astype(float), np.nan)
    return frame


def _definition():
    from evidently import DataDefinition
    return DataDefinition(text_columns=TEXT, numerical_columns=NUMERICAL + FLAGS,
                          categorical_columns=CATEGORICAL, timestamp='created_at')


def _descriptors():
    from evidently.descriptors import SentenceCount, TextLength, WordCount
    return [TextLength('answer', alias='answer_length'), SentenceCount('answer', alias='answer_sentences'),
            WordCount('question', alias='question_words')]


def dataset(frame):
    from evidently import Dataset
    return Dataset.from_pandas(add_flags(frame.copy()), data_definition=_definition(), descriptors=_descriptors())


def build(current, reference=None):
    """A snapshot of `current` (a frame from data.to_frame), with drift against `reference` when it
    has enough rows."""
    from evidently import Report
    from evidently.metrics import MaxValue, MeanValue, MinValue, QuantileValue, RowCount
    from evidently.presets import DataDriftPreset, TextEvals

    metrics = [RowCount(), TextEvals(columns=['answer_length', 'answer_sentences', 'question_words'])]
    metrics += [MeanValue(column=c) for c in FLAGS]
    for c in ('total_tokens', 'tokens_per_second', 'seconds', 'searches', 'answer_length'):
        metrics += [MeanValue(column=c), MaxValue(column=c), MinValue(column=c)]
    metrics += [QuantileValue(column='seconds', quantile=0.95), QuantileValue(column='total_tokens', quantile=0.95)]
    use_reference = reference is not None and len(reference) >= MIN_REFERENCE
    if use_reference:
        metrics.append(DataDriftPreset(columns=NUMERICAL + ['outcome', 'verdict', 'book', 'answer_length']))
    report = Report(metrics, tags=['marginalia'])
    return report.run(dataset(current), dataset(reference) if use_reference else None)


def _mean(column, legend):
    from evidently.sdk.models import PanelMetric
    return PanelMetric(legend=legend, metric='evidently:metric_v2:MeanValue', metric_labels={'column': column})


def _metric(kind, column, legend):
    from evidently.sdk.models import PanelMetric
    return PanelMetric(legend=legend, metric=f'evidently:metric_v2:{kind}', metric_labels={'column': column})


def dashboard():
    """The project's dashboard: one point per report run."""
    from evidently.sdk.models import DashboardModel, PanelMetric
    from evidently.sdk.panels import counter_panel, line_plot_panel, text_panel

    panels = [
        text_panel('MarginaliaRAG answers', description='One report per hour over the last 24 hours, compared with '
                   'the week before. Shares are of all questions; quality shares of answered ones.'),
        counter_panel('Questions (last report)', [PanelMetric(legend='questions', metric='evidently:metric_v2:RowCount')],
                      size='half', aggregation='last'),
        counter_panel('Columns drifted (last report)', [PanelMetric(
            legend='share', metric='evidently:metric_v2:DriftedColumnsCount', metric_labels={'value_type': 'share'})],
            size='half', aggregation='last'),
        line_plot_panel('Outcomes', [_mean(c, SHARES[c]) for c in ('refused', 'cached', 'failed')], size='half'),
        line_plot_panel('Answer quality', [_mean(c, SHARES[c]) for c in ('supported', 'cites_book', 'not_in_book')],
                        size='half'),
        line_plot_panel('Tokens per answer', [_metric('MeanValue', 'total_tokens', 'mean'),
                                              _metric('MaxValue', 'total_tokens', 'most'),
                                              _metric('MinValue', 'total_tokens', 'fewest')], size='half'),
        line_plot_panel('Generation speed (tokens per second)', [_metric('MeanValue', 'tokens_per_second', 'mean'),
                                                                 _metric('MinValue', 'tokens_per_second', 'slowest')],
                        size='half'),
        line_plot_panel('Seconds to answer', [_metric('MeanValue', 'seconds', 'mean'),
                                              _metric('MaxValue', 'seconds', 'slowest')], size='half'),
        line_plot_panel('Searches and answer length', [_metric('MeanValue', 'searches', 'searches (mean)'),
                                                       _metric('MeanValue', 'answer_length', 'answer characters (mean)')],
                        size='half'),
        line_plot_panel('Drifted columns', [PanelMetric(legend='drifted columns', metric='evidently:metric_v2:DriftedColumnsCount',
                                                        metric_labels={'value_type': 'count'})], size='full'),
    ]
    return DashboardModel(tabs=[], panels=panels)


def project(workspace, name=PROJECT):
    """The project (created on first use), with its dashboard kept up to date."""
    found = workspace.search_project(name)
    proj = found[0] if found else workspace.create_project(
        name, description='How MarginaliaRAG answers behave over time: outcomes, quality, cost and drift.')
    workspace.save_dashboard(proj.id, dashboard())
    return proj
