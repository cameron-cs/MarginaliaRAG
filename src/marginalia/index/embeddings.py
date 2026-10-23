"""Dense (e5, ONNX via fastembed) and sparse (BM25) encoders.

e5 was trained with "query: " / "passage: " prefixes and ranks noticeably worse
without them, so the prefixes are applied here and nowhere else.
"""

import unicodedata

from fastembed import SparseTextEmbedding, TextEmbedding
from fastembed.common.model_description import ModelSource, PoolingType
from qdrant_client import models

# fastembed does not ship the English e5 v2 models; register their ONNX exports.
E5_MODELS = {
    'intfloat/e5-small-v2': (384, 'model.onnx'),
    'intfloat/e5-base-v2': (768, 'onnx/model.onnx'),
}


def _register(model_name):
    dim, model_file = E5_MODELS[model_name]
    known = {m['model'] for m in TextEmbedding.list_supported_models()}
    if model_name not in known:
        TextEmbedding.add_custom_model(
            model=model_name, pooling=PoolingType.MEAN, normalization=True,
            sources=ModelSource(hf=model_name), dim=dim, model_file=model_file,
        )
    return dim


class E5Embedder:
    def __init__(self, model_name='intfloat/e5-base-v2', batch_size=32, model=None):
        if model_name not in E5_MODELS:
            raise ValueError(f'unsupported e5 model {model_name!r}; choose from {sorted(E5_MODELS)}')
        self.model_name = model_name
        self.dim = _register(model_name)
        self.batch_size = batch_size
        self._model = model or TextEmbedding(model_name)

    def embed_passages(self, texts):
        return self._embed([f'passage: {t}' for t in texts])

    def embed_query(self, text):
        return self._embed([f'query: {text}'])[0]

    def _embed(self, texts):
        return [v.tolist() for v in self._model.embed(texts, batch_size=self.batch_size)]


class Bm25Encoder:
    """Term-frequency half of BM25; Qdrant applies IDF (Modifier.IDF) at query time.

    Diacritics are folded on both sides so a typed "Svidrigailov" matches the
    book's "Svidrigaïlov".
    """

    def __init__(self, model=None):
        self._model = model or SparseTextEmbedding('Qdrant/bm25')

    def embed_passages(self, texts):
        return [_to_sparse(v) for v in self._model.embed([fold_diacritics(t) for t in texts])]

    def embed_query(self, text):
        return _to_sparse(next(iter(self._model.query_embed(fold_diacritics(text)))))


def fold_diacritics(text):
    return ''.join(c for c in unicodedata.normalize('NFKD', text) if not unicodedata.combining(c))


def _to_sparse(v):
    return models.SparseVector(indices=v.indices.tolist(), values=v.values.tolist())
