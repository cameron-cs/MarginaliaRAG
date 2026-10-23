"""The words a question is about, checked against the book's own vocabulary.

Used twice: the question guard asks whether the names in a question occur in the book at all
("Who is Hagrid?" asked of Crime and Punishment), and the answer cache refuses to reuse an answer
when two similar questions differ in a name, a number or a negation ("Who killed Lizaveta?" is
not "Who killed Alyona?", however close their embeddings are).

A word counts as one of the book's names when the book nearly always capitalizes it, so a
lower-case "halloween" in a question still matches the book's "Halloween".
"""

import difflib
import re
import unicodedata
from collections import Counter

_WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*")
_POSSESSIVE = re.compile(r"['’]s$", re.IGNORECASE)
_SENTENCE_END = '.!?:;"“”(\n'
_ROMAN = re.compile(r'(?=[IVXLC])(X{0,3})(IX|IV|V?I{0,3})$')

# Question words, function words and common verbs of requests: capitalized at the start of a
# sentence, never names. Keeps "Summarize" or "Explain" from looking like a name not in the book.
COMMON = set('''
a an the and or but nor so yet if then than that this these those there here it its i me my mine we us our you your
he him his she her hers they them their who whom whose what when where why how which whether do does did done is are
was were be been being am have has had having can could will would shall should may might must not no yes oh well
in on at by for from of to with without about into onto over under after before during between among through up down
out off again also just only very too more most some any all each every both few many much other such own same
tell explain summarize summarise describe give list write compare name show find please help translate define
chapter chapters part parts book books story novel author character characters page pages volume prologue epilogue
mr mrs ms dr sir madam miss
'''.split())

WH_WORDS = {'who': 'who', 'whom': 'who', 'whose': 'whose', 'what': 'what', 'when': 'when', 'where': 'where',
            'why': 'why', 'how': 'how', 'which': 'which'}
NEGATIONS = {'not', 'no', 'never', 'nobody', 'nothing', 'none', 'neither', 'nor', 'nowhere'}
NUMBER_WORDS = set('''
one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen
eighteen nineteen twenty thirty forty fifty hundred thousand first second third fourth fifth sixth seventh
eighth ninth tenth eleventh twelfth thirteenth fourteenth fifteenth sixteenth seventeenth eighteenth
nineteenth twentieth last final
'''.split())


def fold(text):
    """Strip diacritics, so a typed "Svidrigailov" matches the book's "Svidrigaïlov"."""
    return ''.join(c for c in unicodedata.normalize('NFKD', text) if not unicodedata.combining(c))


def _base(word):
    return _POSSESSIVE.sub('', word)


def words(text):
    """(word, starts_a_sentence) for every word of `text`, diacritics folded, possessives dropped."""
    text = fold(text)
    out = []
    for m in _WORD.finditer(text):
        before = text[:m.start()].rstrip()
        out.append((_base(m.group()), not before or before[-1] in _SENTENCE_END))
    return out


class BookTerms:
    def __init__(self, vocabulary, names):
        self.vocabulary = vocabulary          # every word in the book, casefolded
        self.names = names                    # words the book (nearly) always capitalizes
        self._names = sorted(names)

    @classmethod
    def from_texts(cls, texts, extra_names=()):
        counts, capitalized = Counter(), Counter()
        for text in texts:
            for word, _ in words(text):
                key = word.casefold()
                counts[key] += 1
                capitalized[key] += word[0].isupper()
        names = {k for k, n in counts.items()
                 if n >= 2 and len(k) > 1 and k not in COMMON and capitalized[k] >= 0.8 * n}
        # The title and author are names too, even when the text never prints them.
        for text in extra_names:
            for word, _ in words(text or ''):
                if len(word) > 2 and word.casefold() not in COMMON:
                    names.add(word.casefold())
        return cls(set(counts), names)

    def find_names(self, question):
        """(names of the book the question mentions, capitalized words the book never uses)."""
        present, foreign = [], []
        for word, sentence_start in words(question):
            key = word.casefold()
            if key in COMMON or len(key) < 2:
                continue
            if key in self.names:
                present.append(key)
            elif word[0].isupper() and not sentence_start and key not in self.vocabulary:
                close = difflib.get_close_matches(key, self._names, n=1, cutoff=0.85)   # typos
                (present if close else foreign).append(close[0] if close else word)
        return _unique(present), _unique(foreign)


def key_terms(question, terms=None):
    """What two questions must share for one's answer to serve the other: the numbers (digits,
    number words, Roman numerals), the question word, whether it is negated, and the names in
    their order. Order matters: "What does Sonia read to Raskolnikov?" is not "What does
    Raskolnikov read to Sonia?", though e5 scores the two 0.997 alike."""
    out, names = set(re.findall(r'\d+', question)), []
    for word, sentence_start in words(question):
        key = word.casefold()
        if key in WH_WORDS:
            out.add('wh:' + WH_WORDS[key])
        elif key in NEGATIONS or key.endswith(("n't", "n’t")):
            out.add('not')
        elif key in NUMBER_WORDS:
            out.add(key)
        elif key != 'i' and _ROMAN.match(word):
            out.add(word.lower())
        elif terms is not None and key in terms.names:
            names.append(key)
        elif word[0].isupper() and not sentence_start and key not in COMMON:
            names.append(key)
    return sorted(out) + [f'n:{n}' for n in _unique(names)]


def _unique(items):
    return list(dict.fromkeys(items))
