"""Questions from the reporting views, as the table the Evidently report works on."""

from datetime import timezone

# Numbers and categories the report tracks and checks for drift; text columns get descriptors.
NUMERICAL = ['seconds', 'total_tokens', 'prompt_tokens', 'completion_tokens', 'tokens_per_second', 'searches',
             'revisions', 'citations']
CATEGORICAL = ['outcome', 'verdict', 'refused_reason', 'book']
TEXT = ['question', 'answer']

QUERY = """
SELECT created_at, question, answer, status, answered_by, supported, refused_reason, book, seconds,
       total_tokens, prompt_tokens, completion_tokens, tokens_per_second, searches, revisions, citations
FROM reporting.questions
WHERE created_at >= $1 AND created_at < $2 AND status IN ('done', 'failed', 'refused')
ORDER BY created_at
"""


async def fetch(url, since, until):
    """The finished questions asked in [since, until), as dicts."""
    import asyncpg
    conn = await asyncpg.connect(url)
    try:
        return [dict(r) for r in await conn.fetch(QUERY, since, until)]
    finally:
        await conn.close()


def outcome(row):
    if row['status'] == 'refused':
        return 'refused'
    if row['status'] == 'failed':
        return 'failed'
    return 'cache' if row.get('answered_by') == 'cache' else 'agent'


def verdict(row):
    if row.get('supported') is True:
        return 'supported'
    if row.get('supported') is False:
        return 'unsupported'
    return 'unchecked'


def to_frame(rows):
    """One row per question: naive UTC timestamps (Evidently drops zones anyway), the outcome and
    the grader's verdict as categories, empty strings for missing text."""
    import pandas as pd
    records = []
    for r in rows:
        created = r['created_at']
        if getattr(created, 'tzinfo', None) is not None:
            created = created.astimezone(timezone.utc).replace(tzinfo=None)
        records.append({
            'created_at': created, 'question': r.get('question') or '', 'answer': r.get('answer') or '',
            'outcome': outcome(r), 'verdict': verdict(r), 'refused_reason': r.get('refused_reason') or 'none',
            'book': r.get('book') or '', **{c: r.get(c) for c in NUMERICAL}})
    frame = pd.DataFrame.from_records(records, columns=['created_at', *TEXT, *CATEGORICAL, *NUMERICAL])
    for c in NUMERICAL:
        frame[c] = pd.to_numeric(frame[c], errors='coerce').astype('float64')
    return frame
