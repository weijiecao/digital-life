"""Accept the frozen quality-v2 split against its pre-registered criteria."""
from __future__ import annotations

import argparse
import json
import hashlib
from pathlib import Path

from evaluation.quality_acceptance import _encoder, evaluate

HERE = Path(__file__).resolve().parent
DATA = HERE / "quality_v2"


def check(result: dict, manifest: dict) -> dict:
    criteria = manifest["criteria"]
    overall = result["overall"]
    categories = result["by_category"]
    safety = ("subject_wrong", "story_wrong", "reality_wrong", "time_old",
              "withdrawal", "cross_scope", "deleted")
    checks = {
        "positive_extra_injections": overall.get("positive_extra_injections", 0),
        "positive_top1": overall["top1_hits"] / max(1, overall["positive"]),
        "positive_top5": overall["top5_hits"] / max(1, overall["positive"]),
        "negative_context_injections": overall["negative_context_injections"],
        "invalid_context_injections": overall["invalid_context_injections"],
        "paraphrase_top1": (categories.get("paraphrase", {}).get("top1_hits", 0)
                            / max(1, categories.get("paraphrase", {}).get("positive", 0))),
        "no_answer_injections": categories.get("no_answer", {}).get("negative_context_injections", 0),
        "safety_injections": sum(categories.get(name, {}).get("negative_context_injections", 0)
                                 for name in safety),
    }
    passed = (
        checks["positive_extra_injections"] == 0
        and checks["positive_top1"] >= criteria["positive_top1_min"]
        and checks["positive_top5"] >= criteria["positive_top5_min"]
        and checks["negative_context_injections"] <= criteria["negative_context_injection_max"]
        and checks["invalid_context_injections"] <= criteria["invalid_context_injection_max"]
        and checks["paraphrase_top1"] >= criteria["paraphrase_top1_min"]
        and checks["no_answer_injections"] <= criteria["no_answer_injection_max"]
        and checks["safety_injections"] == 0
    )
    return {"pass": passed, "checks": checks}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    manifest = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
    for split, info in manifest['splits'].items():
        raw = (DATA / f'{split}.json').read_bytes()
        # The original Windows manifest hashes CRLF bytes; git checks out LF
        # on Linux. Permit only that exact line-ending transformation.
        candidates = {hashlib.sha256(raw).hexdigest(),
                      hashlib.sha256(raw.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')).hexdigest()}
        if info['sha256'] not in candidates:
            raise ValueError(f'frozen split hash mismatch: {split}')
    encoder = _encoder(args.model_dir)
    payload = {"manifest": manifest, "splits": {}}
    for split in ("dev", "holdout"):
        groups = json.loads((DATA / f"{split}.json").read_text(encoding="utf-8"))
        result = evaluate(groups, encoder)
        payload["splits"][split] = {"result": result, "acceptance": check(result, manifest)}
    payload["pass"] = all(v["acceptance"]["pass"] for v in payload["splits"].values())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    print(json.dumps({"pass": payload["pass"],
                      "splits": {k: v["acceptance"] for k, v in payload["splits"].items()}},
                     ensure_ascii=False, indent=2))
    return 0 if payload["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
