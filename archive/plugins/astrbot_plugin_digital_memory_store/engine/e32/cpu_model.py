"""Conservative CPU-only adapter for local SentenceTransformer model folders.

No network; default use without model returns a clearly marked non-semantic baseline.
Avoid implicit GPU selection. No use of user conversations or existing QQ databases.
"""
from __future__ import annotations
from hashlib import sha256
from pathlib import Path
import os
import time

from ..e31.local_model import fingerprint_model


class CPUSentenceEmbedder:
    def __init__(self, model_dir: str, *, cpu_threads: int = 4, batch_size: int = 8,
                 query_instruction: str = '', max_tokens: int = 512):
        if not (1 <= cpu_threads <= 16):
            raise ValueError('cpu_threads must be 1..16')
        if not (1 <= batch_size <= 128):
            raise ValueError('batch_size must be 1..128')
        if not (32 <= max_tokens <= 512):
            raise ValueError('max_tokens must be 32..512')
        root=Path(model_dir).expanduser().resolve(strict=True)
        self.weight_sha256,self.manifest=fingerprint_model(root)
        self.query_instruction=query_instruction
        self.batch_size=batch_size
        self.cpu_threads=cpu_threads
        self.max_tokens=max_tokens
        # Set before importing HF / torch; no automatic model download is allowed.
        os.environ['HF_HUB_OFFLINE']='1'
        os.environ['TRANSFORMERS_OFFLINE']='1'
        os.environ['TOKENIZERS_PARALLELISM']='false'
        os.environ['CUDA_VISIBLE_DEVICES']=''
        start=time.perf_counter()
        try:
            import torch
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            raise RuntimeError('CPU model dependencies missing; install optional requirements; no semantic benchmark ran') from e
        torch.set_num_threads(cpu_threads)
        try:
            self.model=SentenceTransformer(str(root),device='cpu',local_files_only=True,trust_remote_code=False)
        except Exception as e:
            raise RuntimeError('Failed loading local pretrained model; no semantic benchmark ran') from e
        self.model.max_seq_length=min(max_tokens,int(self.model.max_seq_length))
        self.loaded_in_seconds=time.perf_counter()-start
        self.model_key=('local-cpu-sbert:'+self.weight_sha256 + ':max_tokens:'+str(self.model.max_seq_length)
                        + ':query_instruction:'+sha256(query_instruction.encode()).hexdigest())
        self.count_doc=0
        self.count_query=0

    def _ensure_fits(self,texts):
        # Prevent a hidden 512-token truncation from turning an omitted negation
        # or date into an apparently complete memory record.
        tok=self.model.tokenizer
        for text in texts:
            if not isinstance(text,str):
                raise TypeError('memory embedding input must be text')
            ids=tok(text,add_special_tokens=True,truncation=False)['input_ids']
            if len(ids)>self.model.max_seq_length:
                raise ValueError(f'embedding input exceeds {self.model.max_seq_length} tokens; split while preserving source IDs')

    def _encode(self,texts):
        if not texts:return []
        self._ensure_fits(texts)
        encoded=self.model.encode(texts,batch_size=self.batch_size,normalize_embeddings=True,
                     show_progress_bar=False,convert_to_numpy=True)
        return encoded.tolist()

    def encode_documents(self,texts):
        self.count_doc+=len(texts)
        return self._encode(texts)

    def encode_queries(self,texts):
        self.count_query+=len(texts)
        return self._encode([self.query_instruction+s for s in texts])

    def encode(self,texts):
        return self.encode_documents(texts)
