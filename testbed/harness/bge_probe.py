"""真实 BGE 编码探针：只测本地 SentenceTransformer 的加载与编码，不碰 AstrBot。

输出 JSON（最后一行），字段：
  ok / dimension / load_seconds / encode_ms_per_item / max_seq_length / model_key
编码耗时按"预热一次 + 3 条取均值"计，避免把首次 kernel 初始化算成常态。
"""

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TESTBED = HERE.parent
sys.path.insert(0, str(TESTBED / 'instance/data/plugins'))

from astrbot_plugin_digital_memory_store.engine.e32.cpu_model import CPUSentenceEmbedder  # noqa: E402

TEXTS = [
    '我喜欢吃苹果',
    '原计划周六去书店找编程资料',
    '改到周日去书店',
    '之前那件事有什么安排？',
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model-dir', required=True)
    parser.add_argument('--threads', type=int, default=4)
    args = parser.parse_args()

    started = time.perf_counter()
    encoder = CPUSentenceEmbedder(args.model_dir, cpu_threads=args.threads, batch_size=8)
    load_seconds = time.perf_counter() - started

    encoder.encode_documents(TEXTS[:1])  # 预热
    started = time.perf_counter()
    vectors = encoder.encode_documents(TEXTS)
    elapsed = time.perf_counter() - started

    import torch
    import sentence_transformers

    result = {
        'ok': True,
        'dimension': len(vectors[0]),
        'load_seconds': round(load_seconds, 2),
        'encode_ms_per_item': round(elapsed * 1000 / len(TEXTS), 1),
        'batch_size': len(TEXTS),
        'threads': args.threads,
        'max_seq_length': int(encoder.model.max_seq_length),
        'model_key': encoder.model_key[:48],
        'torch_version': torch.__version__,
        'sentence_transformers_version': sentence_transformers.__version__,
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
