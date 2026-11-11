"""Offline BGE feasibility probe; never changes the engine's default policy."""
import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model-dir', type=Path, required=True)
    parser.add_argument('--gold-dir', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    import torch
    from sentence_transformers import SentenceTransformer
    from memory_engine.engine import MemoryEngine
    from v1_5_consolidator.contracts import SourceEnvelope
    from v1_5_consolidator.store import V15Store
    torch.set_num_threads(2)
    model = SentenceTransformer(str(args.model_dir.resolve()), device='cpu', local_files_only=True)
    manifest = json.loads((args.gold_dir / 'gold_manifest.json').read_text(encoding='utf-8'))
    data = {}
    for split in ('dev', 'holdout'):
        path = args.gold_dir / (split + '_gold.json')
        assert hashlib.sha256(path.read_bytes()).hexdigest() == manifest['splits'][split]['sha256']
        data[split] = json.loads(path.read_text(encoding='utf-8'))
    # These vectors come from real, frozen model weights, not a test encoder.
    texts = sorted({text for groups in data.values() for group in groups
                    for text in [group['messages']['current']] + [q['query'] for q in group['queries']]})
    vectors = dict(zip(texts, model.encode(texts, batch_size=32, normalize_embeddings=True, show_progress_bar=False)))
    results = {}
    for split, groups in data.items():
        rows = []
        for group in groups:
            with tempfile.TemporaryDirectory() as directory:
                store = V15Store(Path(directory) / 'probe.sqlite3')
                engine = MemoryEngine(store)
                def source(seq, text):
                    return SourceEnvelope(group['group'], seq, hashlib.sha256(text.encode()).hexdigest(), 'frozen', text)
                message = group['messages']
                old = engine.ingest(group['group'], source(1, message['initial'])).event_id
                current = engine.revise_source(group['group'], 1, source(2, message['current']))
                engine.ingest(group['group'], source(3, message['deleted']))
                engine.delete_source(group['group'], 3)
                removed = engine.ingest(group['group'], source(4, message['withdrawn'])).event_id
                engine.withdraw_event(group['group'], removed)
                for query in group['queries']:
                    base = engine.retrieve(group['group'], query['query'])
                    rows.append({**query, 'group': group['group'],
                                 'baseline_hit': any(item['event_id'] == current for item in base),
                                 'cosine': float(vectors[query['query']] @ vectors[message['current']])})
                store.close()
        results[split] = rows
    def metrics(rows, threshold):
        pos = [q for q in rows if q['expected'] is not None]
        neg = [q for q in rows if q['expected'] is None]
        inject = lambda q: q['baseline_hit'] or q['cosine'] >= threshold
        return {'positive': len(pos), 'negative': len(neg),
                'positive_hits': sum(inject(q) for q in pos),
                'negative_injections': sum(inject(q) for q in neg),
                'paraphrase_hits': sum(inject(q) for q in pos if q['category'] == 'paraphrase')}
    grid = [n / 100 for n in range(30, 102)]
    dev = {threshold: metrics(results['dev'], threshold) for threshold in grid}
    eligible = [t for t in grid if dev[t]['negative_injections'] == 0]
    chosen = max(eligible, key=lambda t: (dev[t]['positive_hits'], t))
    report = {'kind': 'real BGE cosine fallback feasibility; not production policy',
              'model': 'BAAI/bge-small-zh-v1.5',
              'weights_sha256': hashlib.sha256((args.model_dir / 'model.safetensors').read_bytes()).hexdigest(),
              'selection': 'dev only; zero dev negative injection, maximize positives, then stricter threshold',
              'threshold': chosen, 'dev_selected': dev[chosen], 'holdout': metrics(results['holdout'], chosen),
              'dev_grid': dev, 'predictions': results,
              'boundary': 'single-author synthetic regression; one valid target per group, no real-chat gold'}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('threshold', 'dev_selected', 'holdout', 'weights_sha256')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
