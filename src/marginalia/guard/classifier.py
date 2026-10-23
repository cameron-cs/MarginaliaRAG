"""A local prompt-injection classifier: DeBERTa-v3 fine-tuned by Protect AI
(protectai/deberta-v3-base-prompt-injection-v2, Apache-2.0), run as ONNX on the CPU.

onnxruntime and tokenizers already come with fastembed, so this needs no torch. The model
(~740 MB) is downloaded from Hugging Face on first use into HF_HOME.

On real questions it scores 0.0 for book questions and >= 0.99 for injection attempts; the one
legitimate question that scored high ("Ignore the film version: what does the book say...")
scored 0.94, hence the default threshold of 0.98.
"""

import json
import threading
from pathlib import Path

import numpy as np

DEFAULT_MODEL = 'protectai/deberta-v3-base-prompt-injection-v2'


class InjectionClassifier:
    def __init__(self, repo=DEFAULT_MODEL, max_tokens=512, session=None, tokenizer=None, injection_label=None):
        self.repo, self.max_tokens = repo, max_tokens
        self._session, self._tokenizer, self._label = session, tokenizer, injection_label
        self._lock = threading.Lock()

    def load(self):
        """Download (once) and open the model; safe to call from several threads."""
        with self._lock:
            if self._session is not None:
                return
            import onnxruntime as ort
            from huggingface_hub import hf_hub_download
            from tokenizers import Tokenizer

            def fetch(name):
                # The copy already downloaded, without asking Hugging Face again on every start.
                try:
                    return hf_hub_download(self.repo, name, local_files_only=True)
                except Exception:
                    return hf_hub_download(self.repo, name)

            tokenizer = Tokenizer.from_file(fetch('onnx/tokenizer.json'))
            tokenizer.enable_truncation(self.max_tokens)
            config = json.loads(Path(fetch('onnx/config.json')).read_text(encoding='utf-8'))
            labels = {v.upper(): int(k) for k, v in config['id2label'].items()}
            options = ort.SessionOptions()
            options.intra_op_num_threads = 2
            session = ort.InferenceSession(fetch('onnx/model.onnx'), options, providers=['CPUExecutionProvider'])
            self._tokenizer, self._label = tokenizer, labels.get('INJECTION', 1)
            self._session = session

    def scores(self, texts):
        """The probability that each text is a prompt injection."""
        self.load()
        encodings = self._tokenizer.encode_batch(list(texts))
        width = max(len(e.ids) for e in encodings)
        ids = np.array([e.ids + [0] * (width - len(e.ids)) for e in encodings], dtype=np.int64)
        mask = np.array([e.attention_mask + [0] * (width - len(e.ids)) for e in encodings], dtype=np.int64)
        feeds = {'input_ids': ids, 'attention_mask': mask}
        if any(i.name == 'token_type_ids' for i in self._session.get_inputs()):
            feeds['token_type_ids'] = np.zeros_like(ids)
        logits = self._session.run(None, feeds)[0]
        probs = np.exp(logits - logits.max(axis=1, keepdims=True))
        probs /= probs.sum(axis=1, keepdims=True)
        return [float(p) for p in probs[:, self._label]]

    def score(self, text):
        return self.scores([text])[0]
