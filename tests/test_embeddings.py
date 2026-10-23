from types import SimpleNamespace

import numpy as np
import pytest

from marginalia.index.embeddings import Bm25Encoder, E5Embedder


class RecordingModel:
    def __init__(self):
        self.seen = []

    def embed(self, texts, batch_size=None):
        self.seen += list(texts)
        return (np.zeros(768) for _ in texts)


def test_e5_prefixes_are_applied():
    model = RecordingModel()
    e5 = E5Embedder('intfloat/e5-base-v2', model=model)
    e5.embed_passages(['Sonia read aloud.'])
    e5.embed_query('who read aloud?')
    assert model.seen == ['passage: Sonia read aloud.', 'query: who read aloud?']


class RecordingSparse:
    def __init__(self):
        self.seen = []

    def embed(self, texts):
        self.seen += list(texts)
        return (SimpleNamespace(indices=np.array([1]), values=np.array([1.0])) for _ in texts)

    def query_embed(self, text):
        self.seen.append(text)
        return iter([SimpleNamespace(indices=np.array([1]), values=np.array([1.0]))])


def test_bm25_folds_diacritics_on_both_sides():
    model = RecordingSparse()
    bm25 = Bm25Encoder(model=model)
    bm25.embed_passages(['Svidrigaïlov shot himself — at dawn.'])
    bm25.embed_query('Svidrigaïlov')
    assert model.seen == ['Svidrigailov shot himself — at dawn.', 'Svidrigailov']


def test_unknown_model_is_rejected():
    with pytest.raises(ValueError, match='unsupported'):
        E5Embedder('intfloat/e5-medium', model=RecordingModel())


@pytest.mark.integration
@pytest.mark.parametrize('name,dim', [('intfloat/e5-small-v2', 384), ('intfloat/e5-base-v2', 768)])
def test_real_e5_ranks_the_right_passage_first(name, dim):
    e5 = E5Embedder(name)
    passages = ['Raskolnikov killed the old pawnbroker with an axe.',
                'Sonia read the story of Lazarus from the New Testament.']
    p = np.array(e5.embed_passages(passages))
    q = np.array(e5.embed_query('What did Sonia read aloud?'))
    assert p.shape == (2, dim)
    assert np.allclose(np.linalg.norm(p, axis=1), 1, atol=1e-3)
    assert (p @ q).argmax() == 1
