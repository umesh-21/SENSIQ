"""Build-time only: downloads the NER model so it can be bundled into the installer.

The installed app never runs this and never connects to the internet.

Usage: python tools/download_model.py [--repo Xenova/bert-base-NER] [--out models/ner]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

# (path in repo, local name). Each tuple lists alternatives tried in order.
FILES = [
    (("config.json",), "config.json"),
    (("tokenizer.json",), "tokenizer.json"),
    (("onnx/model_quantized.onnx", "onnx/model_int8.onnx", "onnx/model.onnx"), "model.onnx"),
]


def fetch(url: str, dest: Path) -> None:
    headers = {"User-Agent": "piiguard-build/1.0"}
    token = os.environ.get("HF_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, "wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
            done += len(chunk)
            if total:
                sys.stderr.write(f"\r  {dest.name}: {done / 1e6:6.1f} / {total / 1e6:.1f} MB")
        sys.stderr.write("\n")
    tmp.replace(dest)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="Xenova/bert-base-NER")
    ap.add_argument("--revision", default="main")
    ap.add_argument("--out", default="models/ner")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    info = {"repo": args.repo, "revision": args.revision, "files": {}}
    for alternatives, local in FILES:
        dest = out / local
        if dest.exists() and dest.stat().st_size > 0 and not args.force:
            print(f"  {local}: already present")
        else:
            last_err = None
            for remote in alternatives:
                url = f"https://huggingface.co/{args.repo}/resolve/{args.revision}/{remote}"
                try:
                    print(f"  downloading {remote}")
                    fetch(url, dest)
                    last_err = None
                    break
                except urllib.error.HTTPError as e:
                    last_err = e
                    print(f"  {remote}: HTTP {e.code}, trying next option")
            if last_err is not None:
                print(f"ERROR: could not download {local} from {args.repo}: {last_err}")
                return 1
        info["files"][local] = sha256(dest)

    cfg = json.loads((out / "config.json").read_text(encoding="utf-8"))
    if "id2label" not in cfg:
        print("ERROR: config.json has no id2label; this is not a token-classification model.")
        return 1
    print(f"  labels: {sorted(set(v.split('-')[-1] for v in cfg['id2label'].values()))}")
    (out / "MODEL_INFO.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    print(f"Model ready in {out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
