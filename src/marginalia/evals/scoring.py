"""Scoring a single answer against a golden question. Deterministic: no LLM judge."""

import re
import unicodedata


# Ways of saying "the book does not tell us", for unanswerable questions.
_DECLINES = re.compile(
    r"\b(?:do|does|did)(?: not|n't) (?:say|specify|mention|state|describe|answer|reveal|contain|include|refer)"
    r"|\bno (?:information|mention|reference|indication|details?|evidence)\b"
    r"|\bnot (?:found|mentioned|specified|stated|in the)\b|\bnowhere\b|\bthere (?:is|are) no\b"
    r"|\bcould(?: not|n't) find\b|\bnever (?:mentioned|says)\b"
    # "no car make is mentioned", "no team is named in the passages"
    r"|\bno\b[^.]{0,40}\b(?:is|are|was|were) (?:mentioned|named|given|stated|described)\b"
    # "the setting predates automobiles"
    r"|\bpredates?\b|\banachronis", re.I)


def fold(text):
    """Lower-case and strip diacritics, so 'Svidrigaïlov' matches 'svidrigailov'."""
    decomposed = unicodedata.normalize('NFKD', text)
    return ''.join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def mentions(answer, alternative):
    """Matches at a word start, so 'horse' finds 'horses' but 'eight' does not find 'weight'."""
    return re.search(r'\b' + re.escape(fold(alternative)), fold(answer)) is not None


def declines(answer):
    return _DECLINES.search(answer) is not None


def answer_correct(question, answer):
    if not question.answerable:
        return declines(answer)
    return all(any(mentions(answer, alt) for alt in group) for group in question.must_mention)


def missing_terms(question, answer):
    return [group for group in question.must_mention if not any(mentions(answer, alt) for alt in group)]


def chapters_of(citations, locate):
    """(part, chapter) of each citation; `locate` maps a citation to them (BookProfile.locate)."""
    return {loc for loc in map(locate, citations) if loc}


def score_run(question, result, evidence_citations, locate):
    """Everything recorded for one agent run of one question.

    `evidence_citations` are the citations of everything the tools returned during the run.
    """
    retrieved = chapters_of(evidence_citations, locate)
    cited = chapters_of(result.citations, locate)
    gold = question.gold_chapters
    return {
        'correct': answer_correct(question, result.answer),
        'missing': missing_terms(question, result.answer),
        # Retrieval: did any tool result come from a chapter that holds the evidence?
        'retrieved_gold': bool(retrieved & gold) if question.answerable else None,
        # Attribution: does the answer cite a chapter that holds the evidence?
        'cited_gold': bool(cited & gold) if question.answerable else None,
        'supported': result.supported,
        'unknown_citations': len(result.unknown_citations),
        'tool_calls': len(result.tool_calls),
        'tools': [c['name'] for c in result.tool_calls],
        'revisions': result.revisions,
        'budget_exhausted': result.budget_exhausted,
    }
