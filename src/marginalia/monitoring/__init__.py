"""LLM monitoring with Evidently: how the answers behave over time, and whether that drifts.

    python -m marginalia.monitoring --once        # one report now
    python -m marginalia.monitoring --every 3600  # one report an hour (the `monitor` container)

Each run reads the questions of the last `window` hours from the reporting views (read-only), and
compares them with the week before. The report goes to the Evidently UI service as one run of the
project "MarginaliaRAG answers", whose dashboard shows the trends:

    outcomes      answered by the agent, from the cache, refused, failed
    quality       the grader's verdict (supported), answers that cite the book, answers that say
                  the book does not answer, revisions
    cost          tokens per answer, generation speed, time to answer, searches
    text          answer and question length (Evidently's text descriptors)
    drift         which of these columns moved against the reference week

Needs the `monitoring` extra (evidently, pandas): `pip install -e ".[monitoring]"`.
"""
