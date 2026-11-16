"""Strictly local, content-versioned sentence embedding adapter.

Semantic experiments run ONLY when the caller provides a populated local model
path and installed dependencies. Never silently synthesize semantic scores.
"""
from __future__ import annotations
from pathlib import Path
import hashlib
import json
import os


def fingerprint_model(path):
    root=Path(path).expanduser().resolve(strict=True)
    if not root.is_dir():raise ValueError('model-dir must be an existing directory')
    known=('config.json','modules.json')
    if not any((root/k).is_file() for k in known):
        raise ValueError('model-dir does not resemble a locally available model directory')
    items=[]
    for p in sorted(root.rglob('*')):
        if p.is_file() and p.suffix.lower() not in ('.lock','.log'):
            h=hashlib.sha256()
            with p.open('rb') as f:
                for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
            items.append((str(p.relative_to(root)),p.stat().st_size,h.hexdigest()))
    if not items:raise ValueError('model files missing')
    manifest={'file_count':len(items),'files':[{'path':n,'size':sz,'sha256':hs} for n,sz,hs in items]}
    key=hashlib.sha256(json.dumps(manifest,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return key,manifest


class LocalSentenceEmbedder:
    def __init__(self,model_dir,query_instruction=''):
        root=Path(model_dir).expanduser().resolve(strict=True)
        self.weight_sha256,self.manifest=fingerprint_model(root)
        self.query_instruction=query_instruction
        # Must set offline mode BEFORE importing sentence_transformers.
        os.environ['HF_HUB_OFFLINE']='1'
        os.environ['TRANSFORMERS_OFFLINE']='1'
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as ex:
            raise RuntimeError('sentence-transformers not installed. Offline model experiment not run.') from ex
        try:
            self.model=SentenceTransformer(str(root),local_files_only=True)
        except Exception as ex:
            raise RuntimeError('Could not load local model; no semantic evaluation performed.') from ex
        self.model_key=f'local-sentence-transformers:{self.weight_sha256}:instruction:{hashlib.sha256(query_instruction.encode()).hexdigest()}'
    def encode_documents(self,texts):
        return self.model.encode(texts,normalize_embeddings=True,show_progress_bar=False).tolist()
    def encode_queries(self,texts):
        return self.model.encode([self.query_instruction+s for s in texts],normalize_embeddings=True,show_progress_bar=False).tolist()
    def encode(self,texts):return self.encode_documents(texts)
