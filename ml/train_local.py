#!/usr/bin/env python
"""CardioSense AI - local (CPU) reproduction of the beat-classifier recipe in main_notebook.ipynb.

Produces a drop-in STAND-IN for the Kaggle artefacts in ml/models/cardiosense_beat_model/:
    beat_model_ts.pt   TorchScript BeatEnsemble  (beat[B,2,250], rr[B,7]) -> probs[B,4]
    beat_model.pth     state_dict of the same ensemble
    model_config.json  same keys as the notebook export (+ "stand_in": true)
    metrics_local.json patient-wise DS1 -> DS2 measurements made on this machine

Notebook cells reproduced: 1 (beat extraction), 3 (NSTDB noise bank), 4/12 (2-channel ResNet1D + RR head,
make_batch with jitter + noise + patient template), 5 (class weights, report), 11 (running-median template),
15 (train_fixed: AdamW, warmup + cosine, SWA, BN recompute), 18 (DS1 -> DS2 evaluation), 24/25 (deployment
training, BeatEnsemble, TorchScript trace), 26 (unknown-threshold table), 27 (model_config.json).

Usage (from the repo root, with ~/.venvs/cardiosense/bin/python):
    python ml/train_local.py prepare                       # download mitdb + nstdb, build the beat cache
    python ml/train_local.py smoke                         # 1 epoch on a subset; times a step, exports
    python ml/train_local.py eval   --epochs 8 --swa-epochs 3 --seeds 42 --export
    python ml/train_local.py deploy --epochs 8 --swa-epochs 3 --seeds 42

Deviations from the notebook are listed in ml/README.md.
"""
import argparse
import copy
import json
import os
import random
import sys
import time
from math import gcd
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from scipy.signal import butter, resample_poly, sosfiltfilt
from torch.optim.swa_utils import AveragedModel

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("CARDIOSENSE_PHYSIONET_DIR", ROOT / "data" / "physionet"))
CACHE = Path(os.environ.get("CARDIOSENSE_CACHE_DIR", Path.home() / ".cache" / "cardiosense"))
MODEL_DIR = Path(os.environ.get("CARDIOSENSE_MODEL_DIR", ROOT / "ml" / "models" / "cardiosense_beat_model"))

# ----------------------------------------------------------------------------- constants (cell 1)
FS = 250
PRE, POST = int(0.36 * FS), int(0.64 * FS)   # 90 + 160 = 250-sample beat window
MARGIN = 8                                   # +-32 ms jitter room for augmentation
L = PRE + POST
CLASSES = ["N", "S", "V", "F"]
AAMI = {**dict.fromkeys("NLRej", "N"), **dict.fromkeys("AaJS", "S"),
        **dict.fromkeys("VE", "V"), "F": "F", **dict.fromkeys("/fQ", "Q")}
BEAT_SYMBOLS = list(AAMI)
DS1 = ["101", "106", "108", "109", "112", "114", "115", "116", "118", "119", "122",
       "124", "201", "203", "205", "207", "208", "209", "215", "220", "223", "230"]
DS2 = ["100", "103", "105", "111", "113", "117", "121", "123", "200", "202", "210",
       "212", "213", "214", "219", "221", "222", "228", "231", "232", "233", "234"]

# CFG_FINAL of the notebook (selection.json: resnet_tpl_cw0.5, class_bias [0,0,0,0]); epochs / swa_epochs
# are 15 / 5 there and are reduced here to fit a CPU budget (see --epochs / --swa-epochs).
CFG_FINAL = dict(epochs=15, swa_epochs=5, batch=512, lr=1e-3, wd=1e-3, cw_power=0.5, label_smoothing=0.05)
CLASS_BIAS = [0, 0, 0, 0]
UNKNOWN_T = 0.5
TEMPLATE_PREV = 20
DEV = torch.device("cpu")
AR = torch.arange(L)


def log(*a):
    print(time.strftime("[%H:%M:%S]"), *a, flush=True)


def seed_all(s):
    random.seed(s); np.random.seed(s); torch.manual_seed(s)


# ----------------------------------------------------------------------------- data (cells 0, 1, 3, 11)
def download():
    import wfdb
    mit_dir, nst_dir = DATA / "mitdb", DATA / "nstdb"
    need = [r for r in DS1 + DS2 if not ((mit_dir / f"{r}.dat").exists() and (mit_dir / f"{r}.atr").exists()
                                         and (mit_dir / f"{r}.hea").exists())]
    if need:
        log(f"downloading mitdb -> {mit_dir}")
        wfdb.dl_database("mitdb", dl_dir=str(mit_dir))
    if not all((nst_dir / f"{n}.dat").exists() for n in ["em", "ma", "bw"]):
        log(f"downloading nstdb noise records (em, ma, bw) -> {nst_dir}")
        wfdb.dl_database("nstdb", dl_dir=str(nst_dir), records=["em", "ma", "bw"], annotators=None)
    n = len({f[:3] for f in os.listdir(mit_dir) if f.endswith(".dat")})
    log(n, "MIT-BIH records present")


def preprocess(x, fs_in, fs_out=FS):
    x = np.asarray(x, dtype=np.float64)
    if fs_in != fs_out:
        g = gcd(int(fs_in), int(fs_out))
        x = resample_poly(x, fs_out // g, int(fs_in) // g)
    sos = butter(3, [0.5, 40], btype="band", fs=fs_out, output="sos")
    return sosfiltfilt(sos, x)


def causal_mean(v, n):
    c = np.cumsum(np.r_[0.0, v])
    i = np.arange(len(v))
    s = np.maximum(0, i - n + 1)
    return (c[i + 1] - c[s]) / (i + 1 - s)


def rr_features(r, fs=FS):
    rr = np.diff(r) / fs
    pre = np.r_[rr[0], rr]
    post = np.r_[rr, rr[-1]]
    loc10, loc100 = causal_mean(pre, 10), causal_mean(pre, 100)
    f = np.stack([pre, post, loc10, pre / loc10, post / loc10, pre / post, pre / loc100], 1)
    return np.log(np.clip(f, 1e-2, 10)).astype(np.float32)


def beats_from_record(sig, fs_in, samples, symbols, rec_id):
    x = preprocess(sig, fs_in)
    r = np.round(np.asarray(samples) * FS / fs_in).astype(int)
    sym = np.asarray(symbols)
    is_beat = np.isin(sym, BEAT_SYMBOLS)
    r, sym = r[is_beat], sym[is_beat]
    rr = rr_features(r)
    lab = np.array([AAMI[s] for s in sym])
    keep = (np.isin(lab, CLASSES)
            & (r - PRE - MARGIN >= 0) & (r + POST + MARGIN <= len(x)))
    idx = r[keep, None] + np.arange(-PRE - MARGIN, POST + MARGIN)[None, :]
    return dict(W=x[idx].astype(np.float32), RR=rr[keep],
                y=np.array([CLASSES.index(l) for l in lab[keep]], dtype=np.int64),
                rec=np.full(keep.sum(), rec_id), r=r[keep])


def zscore(W):
    return (W - np.median(W, 1, keepdims=True)) / (W.std(1, keepdims=True) + 1e-6)


def add_template(D, n_prev=TEMPLATE_PREV):
    """Running median of the patient's previous n_prev (z-scored) beats - cell 11."""
    Z = zscore(D["W"])
    T = np.empty_like(Z)
    for rec in dict.fromkeys(D["rec"].tolist()):
        idx = np.where(D["rec"] == rec)[0]
        Zr = Z[idx]
        T[idx[0]] = Zr[0]
        for k in range(1, len(idx)):
            T[idx[k]] = np.median(Zr[max(0, k - n_prev):k], 0)
    D["T"] = T


def build_cache(force=False):
    import wfdb
    CACHE.mkdir(parents=True, exist_ok=True)
    beats_p, noise_p = CACHE / "mitdb_beats_tpl.npz", CACHE / "nstdb_noise.npy"
    if force or not beats_p.exists():
        parts = []
        for rec in DS1 + DS2:
            p = str(DATA / "mitdb" / rec)
            s, a = wfdb.rdrecord(p), wfdb.rdann(p, "atr")
            parts.append(beats_from_record(s.p_signal[:, s.sig_name.index("MLII")], s.fs, a.sample, a.symbol, rec))
        mit = {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}
        log("beats extracted:", mit["W"].shape, "- computing patient templates")
        add_template(mit)
        tmp = CACHE / "mitdb_beats_tpl.tmp.npz"
        np.savez(tmp, **mit)
        os.replace(tmp, beats_p)
    if force or not noise_p.exists():
        bank = []
        for name in ["em", "ma", "bw"]:
            s = wfdb.rdrecord(str(DATA / "nstdb" / name))
            for ch in range(s.p_signal.shape[1]):
                n = preprocess(s.p_signal[:, ch], s.fs)
                bank.append(n / (n.std() + 1e-9))
        np.save(noise_p, np.concatenate(bank).astype(np.float32))
    return beats_p, noise_p


def load_data():
    beats_p, noise_p = build_cache()
    d = dict(np.load(beats_p))
    D = dict(y=d["y"], rec=d["rec"].astype(str), Wt=torch.from_numpy(d["W"]), Tt=torch.from_numpy(d["T"]),
             RRt=torch.from_numpy(d["RR"]), yt=torch.from_numpy(d["y"]))
    noise = torch.from_numpy(np.load(noise_p))
    return D, noise


def counts(D, idx):
    return {c: int((D["y"][idx] == i).sum()) for i, c in enumerate(CLASSES)}


# ----------------------------------------------------------------------------- model (cells 4, 12)
class ResBlock(nn.Module):
    def __init__(self, cin, cout, stride, k=7, p=0.1):
        super().__init__()
        self.c1 = nn.Conv1d(cin, cout, k, stride, k // 2, bias=False); self.b1 = nn.BatchNorm1d(cout)
        self.c2 = nn.Conv1d(cout, cout, k, 1, k // 2, bias=False); self.b2 = nn.BatchNorm1d(cout)
        self.drop = nn.Dropout(p)
        self.skip = (nn.Identity() if cin == cout and stride == 1 else
                     nn.Sequential(nn.Conv1d(cin, cout, 1, stride, bias=False), nn.BatchNorm1d(cout)))

    def forward(self, x):
        h = self.drop(F.relu(self.b1(self.c1(x))))
        return F.relu(self.b2(self.c2(h)) + self.skip(x))


class Head(nn.Module):
    def __init__(self, emb, use_rr, n_rr=7, n_cls=4):
        super().__init__()
        self.use_rr = use_rr
        self.rr = nn.Sequential(nn.Linear(n_rr, 32), nn.ReLU()) if use_rr else nn.Identity()
        self.fc = nn.Sequential(nn.Dropout(0.3), nn.Linear(emb + (32 if use_rr else 0), 64),
                                nn.ReLU(), nn.Linear(64, n_cls))

    def forward(self, e, rr):
        if self.use_rr:
            e = torch.cat([e, self.rr(rr)], 1)
        return self.fc(e)


class ResNet1D(nn.Module):
    def __init__(self, use_rr=True, in_ch=1, w=32):
        super().__init__()
        self.in_ch = in_ch
        self.stem = nn.Sequential(nn.Conv1d(in_ch, w, 15, 1, 7, bias=False), nn.BatchNorm1d(w), nn.ReLU())
        self.body = nn.Sequential(ResBlock(w, w, 1), ResBlock(w, 2 * w, 2), ResBlock(2 * w, 2 * w, 1),
                                  ResBlock(2 * w, 4 * w, 2), ResBlock(4 * w, 4 * w, 1), ResBlock(4 * w, 4 * w, 2))
        self.head = Head(8 * w, use_rr)

    def forward(self, x, rr):
        h = self.body(self.stem(x))
        return self.head(torch.cat([h.mean(-1), h.amax(-1)], 1), rr)


class BeatEnsemble(nn.Module):
    def __init__(self, models):
        super().__init__()
        self.models = nn.ModuleList(models)

    def forward(self, x, rr):
        return torch.stack([torch.softmax(m(x, rr), 1) for m in self.models]).mean(0)


def make_model():
    return ResNet1D(use_rr=True, in_ch=2)          # "resnet_rr_tpl"


# ----------------------------------------------------------------------------- batches (cell 12)
def _norm(x):
    return (x - x.median(1, keepdim=True).values) / (x.std(1, keepdim=True) + 1e-6)


def make_batch(D, noise, j, train):
    xb = D["Wt"][j]
    B = xb.shape[0]
    off = torch.randint(0, 2 * MARGIN + 1, (B,)) if train else torch.full((B,), MARGIN)
    gi = off[:, None] + AR[None]
    x = xb.gather(1, gi)
    if train:
        sig_std = x.std(1, keepdim=True) + 1e-6
        start = torch.randint(0, noise.numel() - L, (B,))
        nz = noise[start[:, None] + AR[None]]
        snr_db = torch.empty(B, 1).uniform_(6, 30)
        apply = (torch.rand(B, 1) < 0.7).float()
        x = x + apply * nz * sig_std / (10 ** (snr_db / 20))
    x = _norm(x)
    t = _norm(D["Tt"][j].gather(1, gi))
    return torch.stack([x, x - t], 1)


# ----------------------------------------------------------------------------- training (cells 5, 15)
def class_weights(y, power):
    c = np.bincount(y, minlength=4).astype(float)
    w = np.where(c > 0, (c.sum() / (4 * np.maximum(c, 1))) ** power, 0.0)
    return w / w[c > 0].mean()


@torch.no_grad()
def predict_proba(model, D, noise, idx, bs=1024):
    model.eval()
    out = []
    for i in range(0, len(idx), bs):
        j = torch.as_tensor(idx[i:i + bs])
        out.append(torch.softmax(model(make_batch(D, noise, j, False), D["RRt"][j]), 1).numpy())
    return np.concatenate(out)


@torch.no_grad()
def recompute_bn(model, D, noise, tr, n_batches=60, bs=1024):
    bns = [m for m in model.modules() if isinstance(m, nn.BatchNorm1d)]
    for m in bns:
        m.reset_running_stats(); m.momentum = None
    model.train()
    perm = tr[torch.randperm(len(tr))]
    for i in range(0, min(len(perm), n_batches * bs), bs):
        j = perm[i:i + bs]
        model(make_batch(D, noise, j, False), D["RRt"][j])
    model.eval()


def train_fixed(D, noise, tr_idx, cfg, desc="train", max_steps=None):
    model = make_model()
    opt = torch.optim.AdamW(model.parameters(), lr=cfg["lr"], weight_decay=cfg["wd"])
    steps = int(np.ceil(len(tr_idx) / cfg["batch"]))
    sched = torch.optim.lr_scheduler.SequentialLR(opt, [
        torch.optim.lr_scheduler.LinearLR(opt, start_factor=0.1, total_iters=steps),
        torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=max(1, (cfg["epochs"] - 1) * steps),
                                                   eta_min=cfg["lr"] * 0.01)], milestones=[steps])
    cw = torch.tensor(class_weights(D["y"][tr_idx], cfg["cw_power"]), dtype=torch.float32)
    tr = torch.as_tensor(tr_idx)
    swa = AveragedModel(model)
    swa_start = cfg["epochs"] - cfg["swa_epochs"]
    t_start, done = time.time(), 0
    for ep in range(cfg["epochs"]):
        model.train()
        perm = tr[torch.randperm(len(tr))]
        t0, run = time.time(), 0.0
        for k, i in enumerate(range(0, len(perm), cfg["batch"])):
            j = perm[i:i + cfg["batch"]]
            logits = model(make_batch(D, noise, j, True), D["RRt"][j])
            loss = F.cross_entropy(logits, D["yt"][j], weight=cw, label_smoothing=cfg["label_smoothing"])
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            sched.step()
            run += loss.item(); done += 1
            if (k + 1) % 25 == 0 or k + 1 == steps:
                log(f"{desc} ep {ep + 1}/{cfg['epochs']} step {k + 1}/{steps} loss {run / (k + 1):.4f} "
                    f"lr {sched.get_last_lr()[0]:.1e} | {(time.time() - t_start) / done:.2f} s/step")
            if max_steps and done >= max_steps:
                break
        if ep >= swa_start:
            swa.update_parameters(model)
        log(f"{desc} epoch {ep + 1} done in {time.time() - t0:.0f}s (swa={ep >= swa_start})")
        if max_steps and done >= max_steps:
            break
    final = swa.module
    recompute_bn(final, D, noise, tr)
    return final, (time.time() - t_start)


# ----------------------------------------------------------------------------- metrics (cells 5, 26)
def report(y, p):
    cm = np.zeros((4, 4), dtype=int)
    np.add.at(cm, (y, p), 1)
    tp = np.diag(cm)
    se = tp / np.maximum(cm.sum(1), 1)
    ppv = tp / np.maximum(cm.sum(0), 1)
    f1 = 2 * se * ppv / np.maximum(se + ppv, 1e-9)
    row = {"acc": float(tp.sum() / cm.sum()), "macroF1": float(f1.mean())}
    for i, c in enumerate(CLASSES):
        row[f"{c}_Se"], row[f"{c}_+P"], row[f"{c}_F1"] = float(se[i]), float(ppv[i]), float(f1[i])
    return row, cm


def threshold_table(P, y):
    rows = []
    for t in [0.4, 0.5, 0.6, 0.7, 0.8]:
        keep = P.max(1) >= t
        rows.append(dict(threshold=t, coverage=float(keep.mean()),
                         acc_kept=float((P.argmax(1)[keep] == y[keep]).mean()),
                         acc_flagged_unknown=float((P.argmax(1)[~keep] == y[~keep]).mean()) if (~keep).any() else None))
    return rows


# ----------------------------------------------------------------------------- export (cells 25, 27)
def atomic_write(path, writer):
    tmp = Path(str(path) + ".tmp")
    writer(tmp)
    os.replace(tmp, path)


def export(models, info, metrics=None):
    """Write beat_model.pth, beat_model_ts.pt and model_config.json into MODEL_DIR (atomic renames)."""
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    ens = BeatEnsemble([copy.deepcopy(m).cpu().eval() for m in models]).eval()
    atomic_write(MODEL_DIR / "beat_model.pth", lambda p: torch.save(ens.state_dict(), p))
    ex_x, ex_rr = torch.randn(8, 2, L), torch.randn(8, 7)
    with torch.no_grad():
        traced = torch.jit.trace(ens, (ex_x, ex_rr))
        atomic_write(MODEL_DIR / "beat_model_ts.pt", lambda p: traced.save(str(p)))
        tx, trr = torch.randn(37, 2, L), torch.randn(37, 7)
        diff = (torch.jit.load(str(MODEL_DIR / "beat_model_ts.pt"))(tx, trr) - ens(tx, trr)).abs().max().item()
    log("TorchScript vs eager max diff:", diff)

    n = len(models)
    config = {
        "model_version": f"cardiosense-beat-resnet-tpl-local-standin-{time.strftime('%Y%m%d')}",
        "stand_in": True,
        "stand_in_note": ("LOCAL STAND-IN, not the Kaggle model. Trained on CPU with a reduced budget ("
                          + info["summary"] + "). It is NOT the model whose metrics are quoted in the BRD (section "
                          "8.3); the numbers under 'evaluation' were measured on this machine for this stand-in. "
                          "Replace beat_model_ts.pt + model_config.json with the Kaggle export to deploy the real one."),
        "architecture": "ResNet1D, 2-channel input (beat, beat minus patient template) + 7 RR features; "
                        f"{n}-seed ensemble, SWA weights, softmax-averaged",
        "files": {"torchscript": "beat_model_ts.pt", "state_dict": "beat_model.pth"},
        "classes": CLASSES, "fs": FS, "pre": PRE, "post": POST, "margin": MARGIN, "window_len": L,
        "bandpass_hz": [0.5, 40.0], "filter_order": 3, "template_prev_beats": TEMPLATE_PREV,
        "rr_features": ["pre_rr", "post_rr", "local10_rr", "pre/local10", "post/local10", "pre/post", "pre/local100"],
        "rr_transform": "log(clip(x, 0.01, 10)), RR in seconds, causal local means",
        "inputs": {"beat": "(B, 2, 250) float32", "rr": "(B, 7) float32"}, "output": "(B, 4) class probabilities",
        "unknown_threshold": UNKNOWN_T, "class_bias": CLASS_BIAS,
        "latency_note": "post_rr needs the next beat, so each label is available ~1 beat after the R-peak",
        "training": {"deployment_data": info["data"],
                     "augmentation": "R-peak jitter +-32 ms, NSTDB em/ma/bw noise at 6-30 dB SNR",
                     "cfg": info["cfg"], "seeds": info["seeds"], "device": "cpu",
                     "wall_time_s": info.get("wall_time_s"), "torch": torch.__version__,
                     "script": "ml/train_local.py"},
        "evaluation_note": info["evaluation_note"],
        "evaluation": metrics or {},
        "limitations": [
            "Local stand-in trained with a reduced CPU budget; expect it to be somewhat weaker than the "
            "Kaggle 3-seed ensemble described in the BRD.",
            "Single-lead (MLII / lead II) only; not for ischaemia or 12-lead interpretation.",
            "Supraventricular detection is weak when ectopic beats dominate the rhythm (MIT-BIH record 232); "
            "rhythm-level rules (pauses, bradycardia) must cover this case.",
            "Fusion (F) beats are rarely detected on unseen patients.",
            "Not validated on AD8232 recordings yet; research and demonstration use only, not a medical device.",
        ],
    }
    atomic_write(MODEL_DIR / "model_config.json",
                 lambda p: Path(p).write_text(json.dumps(config, indent=2, default=float)))
    log("exported:", config["model_version"], "->", MODEL_DIR)


def load_metrics():
    p = MODEL_DIR / "metrics_local.json"
    return json.loads(p.read_text()) if p.exists() else None


def evaluation_block(m):
    """What goes under model_config.json["evaluation"] - local measurements only."""
    if not m:
        return {}
    return {
        "source": "LOCAL STAND-IN measurements (ml/train_local.py on this machine) - not the BRD / Kaggle numbers",
        "protocol": m["protocol"],
        "ds2_local_standin": m["ds2"]["ensemble"],
        "ds2_per_seed": m["ds2"]["per_seed"],
        "ds2_confusion_matrix": m["ds2"]["confusion_matrix"],
        "ds2_S_Se_without_record_232": m["ds2"].get("S_Se_without_record_232"),
        "unknown_threshold_table_ds2": m["ds2"]["unknown_threshold_table"],
        **({"end_to_end": m["end_to_end"]} if "end_to_end" in m else {}),
    }


# ----------------------------------------------------------------------------- stages
def stage_smoke(a):
    D, noise = load_data()
    ds1 = np.where(np.isin(D["rec"], DS1))[0]
    ds2 = np.where(np.isin(D["rec"], DS2))[0]
    seed_all(0)
    sub = np.random.permutation(ds1)[:a.subset]
    cfg = {**CFG_FINAL, "epochs": 1, "swa_epochs": 1}
    m, wall = train_fixed(D, noise, sub, cfg, desc="smoke")
    steps = int(np.ceil(len(sub) / cfg["batch"]))
    log(f"smoke: {wall / steps:.2f} s/step -> DS1 epoch ~{wall / steps * np.ceil(len(ds1) / 512) / 60:.1f} min, "
        f"DS1+DS2 epoch ~{wall / steps * np.ceil((len(ds1) + len(ds2)) / 512) / 60:.1f} min")
    t0 = time.time()
    row, _ = report(D["y"][ds2[::10]], predict_proba(m, D, noise, ds2[::10]).argmax(1))
    log(f"smoke DS2 (1/10 sample) acc {row['acc']:.3f} macroF1 {row['macroF1']:.3f}; "
        f"inference {len(ds2[::10]) / (time.time() - t0):.0f} beats/s")
    if a.export:
        export([m], dict(summary=f"SMOKE TEST: 1 epoch on {len(sub)} DS1 beats - interface check only",
                         data=f"MIT-BIH DS1 subset ({len(sub)} beats)", cfg=cfg, seeds=[0], wall_time_s=wall,
                         evaluation_note="Smoke-test weights; not evaluated. Will be replaced."))


def stage_eval(a):
    D, noise = load_data()
    ds1 = np.where(np.isin(D["rec"], DS1))[0]
    ds2 = np.where(np.isin(D["rec"], DS2))[0]
    cfg = {**CFG_FINAL, "epochs": a.epochs, "swa_epochs": a.swa_epochs}
    log("DS1:", counts(D, ds1), "| DS2:", counts(D, ds2), "| cfg:", cfg)
    (CACHE / "runs").mkdir(parents=True, exist_ok=True)
    models, probs, per_seed, wall = [], [], [], 0.0
    y = D["y"][ds2]
    for s in a.seeds:
        seed_all(s)
        m, w = train_fixed(D, noise, ds1, cfg, desc=f"eval-DS1 seed {s}")
        wall += w
        torch.save(m.state_dict(), CACHE / "runs" / f"eval_ds1_seed{s}.pth")
        P = predict_proba(m, D, noise, ds2)
        row, _ = report(y, P.argmax(1))
        log(f"seed {s} DS2: " + " ".join(f"{k} {v:.3f}" for k, v in row.items()))
        models.append(m); probs.append(P); per_seed.append({"seed": s, **row})
    P = np.mean(probs, 0)
    np.savez_compressed(CACHE / "runs" / "ds2_probs.npz", P=P, y=y, rec=D["rec"][ds2])
    row, cm = report(y, P.argmax(1))
    no232 = D["rec"][ds2] != "232"
    s_no232 = float(((P.argmax(1) == 1) & (y == 1) & no232).sum() / max(1, ((y == 1) & no232).sum()))
    metrics = {
        "stand_in": True,
        "note": "Measured locally by ml/train_local.py for the CPU stand-in model. NOT the BRD / Kaggle numbers.",
        "protocol": (f"patient-wise: trained on MIT-BIH DS1 (22 records, {len(ds1)} beats), tested on DS2 "
                     f"(22 unseen records, {len(ds2)} beats), cardiologist R-peaks, AAMI N/S/V/F, argmax labels; "
                     f"{len(a.seeds)} seed(s) {a.seeds}, {cfg['epochs']} epochs, SWA over last {cfg['swa_epochs']}"),
        "cfg": cfg, "seeds": a.seeds, "train_wall_time_s": wall,
        "ds1_counts": counts(D, ds1), "ds2_counts": counts(D, ds2),
        "ds2": {"ensemble": row, "per_seed": per_seed, "confusion_matrix": cm.tolist(),
                "confusion_matrix_axes": "rows = true N,S,V,F; columns = predicted N,S,V,F",
                "S_Se_without_record_232": s_no232,
                "unknown_threshold_table": threshold_table(P, y)},
    }
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write(MODEL_DIR / "metrics_local.json", lambda p: Path(p).write_text(json.dumps(metrics, indent=2)))
    log("DS2 ensemble: " + " ".join(f"{k} {v:.3f}" for k, v in row.items()))
    log("confusion matrix (rows true, cols pred):\n" + str(cm))
    if a.export:
        export(models, dict(
            summary=f"MIT-BIH DS1 only, {len(a.seeds)} seed(s), {cfg['epochs']} epochs with SWA over the last "
                    f"{cfg['swa_epochs']}; the notebook uses 3 seeds x 15 epochs on GPU and adds DS2 + INCART",
            data="MIT-BIH DS1 only (22 non-paced records) - this is the evaluation model shipped as stand-in",
            cfg=cfg, seeds=a.seeds, wall_time_s=wall,
            evaluation_note="The shipped weights ARE the evaluated model: trained on DS1 only, metrics below are "
                            "its patient-wise results on DS2 (22 unseen patients), measured locally."),
            evaluation_block(metrics))


def stage_deploy(a):
    D, noise = load_data()
    idx = np.arange(len(D["y"]))
    cfg = {**CFG_FINAL, "epochs": a.epochs, "swa_epochs": a.swa_epochs}
    log("deployment training beats:", len(idx), counts(D, idx), "| cfg:", cfg)
    (CACHE / "runs").mkdir(parents=True, exist_ok=True)
    models, wall = [], 0.0
    for s in a.seeds:
        seed_all(s)
        m, w = train_fixed(D, noise, idx, cfg, desc=f"deploy seed {s}")
        wall += w
        torch.save(m.state_dict(), CACHE / "runs" / f"deploy_seed{s}.pth")
        models.append(m)
    export(models, dict(
        summary=f"MIT-BIH DS1+DS2 (44 non-paced records, no INCART), {len(a.seeds)} seed(s), {cfg['epochs']} epochs "
                f"with SWA over the last {cfg['swa_epochs']}; the notebook uses 3 seeds x 15 epochs on GPU and adds INCART",
        data="MIT-BIH 44 non-paced records (DS1 + DS2); INCART not included",
        cfg=cfg, seeds=a.seeds, wall_time_s=wall,
        evaluation_note="Reported metrics come from a separate local model trained on DS1 only (patient-wise, "
                        "tested on DS2); the shipped stand-in is retrained on DS1+DS2 and has no held-out test "
                        "of its own. All numbers were measured locally, not copied from the BRD."),
        evaluation_block(load_metrics()))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stage", choices=["prepare", "smoke", "eval", "deploy"])
    ap.add_argument("--epochs", type=int, default=CFG_FINAL["epochs"])
    ap.add_argument("--swa-epochs", type=int, default=CFG_FINAL["swa_epochs"])
    ap.add_argument("--seeds", type=int, nargs="+", default=[42])
    ap.add_argument("--threads", type=int, default=3)
    ap.add_argument("--subset", type=int, default=10240, help="smoke: number of DS1 beats")
    ap.add_argument("--export", action="store_true", help="smoke/eval: also write the model into the model dir")
    ap.add_argument("--force", action="store_true", help="prepare: rebuild the beat cache")
    a = ap.parse_args()
    torch.set_num_threads(a.threads)
    import warnings
    warnings.filterwarnings("ignore", message=".*lr_scheduler.step.*")
    warnings.filterwarnings("ignore", message=".*epoch parameter.*")
    log("stage", a.stage, "| torch", torch.__version__, "| threads", torch.get_num_threads())
    if a.stage == "prepare":
        download()
        build_cache(force=a.force)
        D, _ = load_data()
        log("cache ready:", tuple(D["Wt"].shape), "DS1", counts(D, np.isin(D["rec"], DS1)),
            "DS2", counts(D, np.isin(D["rec"], DS2)))
    else:
        {"smoke": stage_smoke, "eval": stage_eval, "deploy": stage_deploy}[a.stage](a)


if __name__ == "__main__":
    sys.exit(main())
