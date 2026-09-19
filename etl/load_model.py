#!/usr/bin/env python3
"""Store the image model in Postgres (DESIGN §9.2).

    python -m etl.load_model --ckpt moco_vit_s_stage2.ckpt [--name moco_vit_s] [--arch vit_s]

Reads a PyTorch Lightning checkpoint from the research code (MoCoSupervised,
MoCoMulti, BYOLSupervised/BYOLMulti, or the SimCLR family), keeps only the
online backbone weights, and writes them to `model_store`. The web app rebuilds
the backbone from that state dict with timm / torchvision alone.

Needs torch on the machine that runs it (the web pod has it).
"""
from __future__ import annotations

import argparse
import hashlib
import io
import os
import sys
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ARCH_DIM = {"vit_s": 384, "resnet18": 512, "resnet50": 2048}


def detect_arch(hparams: dict, keys) -> str:
    bb = str(hparams.get("backbone", "")).replace("moco_", "").replace("byol_", "")
    if bb in ARCH_DIM:
        return bb
    keys = list(keys)
    if any(k.startswith("backbone.base_model.blocks.") for k in keys):
        return "vit_s"
    if any(k.startswith("convnet.backbone.blocks.") for k in keys):
        return "vit_s"
    if any(k.endswith("layer4.2.conv3.weight") for k in keys):
        return "resnet50"
    return "resnet18"


def strip_state(state: dict, arch: str) -> dict:
    """Map checkpoint keys onto the bare backbone.

    BackboneWrapper (MoCo/BYOL):  backbone.base_model.<x>  -> <x>
    SimCLR ResNet:                convnet.<x> (fc dropped) -> <x>, wrapped as
                                  nn.Sequential(children[:-1]) index names
    SimCLR ViT:                   convnet.backbone.<x>     -> <x>
    """
    out = {}
    if any(k.startswith("backbone.base_model.") for k in state):
        for k, v in state.items():
            if k.startswith("backbone.base_model.") and not k.startswith("backbone_momentum"):
                out[k[len("backbone.base_model."):]] = v
        return out
    if any(k.startswith("convnet.backbone.") for k in state):
        for k, v in state.items():
            if k.startswith("convnet.backbone."):
                out[k[len("convnet.backbone."):]] = v
        return out
    if any(k.startswith("convnet.") for k in state):
        # torchvision resnet children order: conv1 bn1 relu maxpool layer1..4 avgpool fc
        order = ["conv1", "bn1", "relu", "maxpool", "layer1", "layer2", "layer3", "layer4",
                 "avgpool"]
        for k, v in state.items():
            if not k.startswith("convnet.") or k.startswith("convnet.fc."):
                continue
            name = k[len("convnet."):]
            head = name.split(".", 1)[0]
            if head in order:
                out[f"{order.index(head)}.{name[len(head) + 1:]}"] = v
        return out
    raise SystemExit("unrecognised checkpoint layout; keys start with: "
                     + ", ".join(sorted({k.split('.')[0] for k in state})[:8]))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--ckpt", required=True, type=Path)
    ap.add_argument("--name", default=None, help="default: the checkpoint's file stem")
    ap.add_argument("--arch", choices=list(ARCH_DIM), default=None)
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL"))
    a = ap.parse_args()
    if not a.dsn:
        sys.exit("set DATABASE_URL or pass --dsn")
    import torch

    ck = torch.load(a.ckpt, map_location="cpu")
    state = ck.get("state_dict", ck)
    hparams = ck.get("hyper_parameters", {}) or {}
    arch = a.arch or detect_arch(hparams, state.keys())
    bare = strip_state(state, arch)
    if not bare:
        sys.exit("no backbone weights found in the checkpoint")
    buf = io.BytesIO()
    torch.save(bare, buf)
    blob = buf.getvalue()
    name = a.name or a.ckpt.stem
    sha = hashlib.sha256(blob).hexdigest()
    conn = psycopg2.connect(a.dsn)
    cur = conn.cursor()
    cur.execute("""INSERT INTO model_store (name, arch, dim, state, sha256, n_bytes, source_file)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (name) DO UPDATE SET arch = EXCLUDED.arch, dim = EXCLUDED.dim,
                       state = EXCLUDED.state, sha256 = EXCLUDED.sha256,
                       n_bytes = EXCLUDED.n_bytes, source_file = EXCLUDED.source_file,
                       loaded_at = now()""",
                (name, arch, ARCH_DIM[arch], psycopg2.Binary(blob), sha, len(blob), a.ckpt.name))
    conn.commit()
    print(f"stored model {name!r}: {arch}, {ARCH_DIM[arch]}-d, {len(bare)} tensors, "
          f"{len(blob) / 1e6:.0f} MB (from {a.ckpt.name}, keys in checkpoint: {len(state)})")
    cur.execute("SELECT dim FROM embedding_model ORDER BY loaded_at DESC LIMIT 1")
    r = cur.fetchone()
    if r and r[0] != ARCH_DIM[arch]:
        print(f"! the loaded embeddings are {r[0]}-d; this model gives {ARCH_DIM[arch]}-d. "
              f"Reload the embeddings from this model before searching.")


if __name__ == "__main__":
    main()
