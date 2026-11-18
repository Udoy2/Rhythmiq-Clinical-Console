# ml/ — beat classifier

| File | What it is |
|---|---|
| `cardiosense_beats.py` | Inference module exported by `main_notebook.ipynb` (cell 27), verbatim. Shared preprocessing, R-peak detector, template, RR features, `BeatClassifier`. Do not change its behaviour. |
| `train_local.py` | Script version of the notebook's final recipe, sized for a CPU box. Produces the **stand-in** model. |
| `verify_model.py` | Runs the exported artefact through `BeatClassifier.classify` on MIT-BIH records and compares with the cardiologist annotations. |
| `models/cardiosense_beat_model/` | `beat_model_ts.pt` (TorchScript `(beat[B,2,250], rr[B,7]) -> probs[B,4]`), `beat_model.pth`, `model_config.json`, `metrics_local.json`, `verify_local.json`. |

## The model in `models/` is a local stand-in

The Kaggle artefacts (`cardiosense-beat-resnet-tpl-20261117`, 3 seeds x 15 epochs on GPU, MIT-BIH + INCART) are not
on this machine. `train_local.py` retrains the same architecture and recipe on CPU with a smaller budget, and
`model_config.json` says so: `"stand_in": true`, `model_version` = `cardiosense-beat-resnet-tpl-local-standin-<date>`.
Every number in `model_config.json["evaluation"]` and `metrics_local.json` was measured here for the stand-in.
The BRD section 8.3 numbers belong to the Kaggle model and are not copied anywhere in this directory.

Shipped weights: one ResNet1D (seed 42) trained on MIT-BIH DS1+DS2 for 6 epochs, SWA over the last 2 (39 min on
3 CPU threads). Its patient-wise numbers come from a separate DS1-only model (8 epochs, SWA over the last 3, 32 min):

| DS2, 22 unseen patients, local stand-in recipe | Acc | Macro-F1 | N Se / +P | S Se / +P | V Se / +P | F Se / +P |
|---|---|---|---|---|---|---|
| Cardiologist R-peaks, argmax labels | 0.915 | 0.552 | 0.951 / 0.966 | 0.267 / 0.257 | 0.870 / 0.799 | 0.222 / 0.117 |
| End-to-end via `BeatClassifier.classify` (own detector, Unknown counts as a miss) | 0.883 | 0.544 | 0.920 / 0.974 | 0.177 / 0.224 | 0.848 / 0.879 | 0.168 / 0.170 |

End-to-end beat detection on DS2: Se 0.979, +P 0.999; 6.0% of beats labelled Unknown at the 0.5 threshold.
Confidences are compressed (label smoothing, short training): only 11% of DS2 beats reach 0.8, so do not raise
`unknown_threshold` above about 0.7 for this stand-in.

The end-to-end row is not produced by `train_local.py`: it was measured by exporting
`~/.cache/cardiosense/runs/eval_ds1_seed42.pth` to a scratch model dir, running
`verify_model.py --model-dir <scratch> --ds2`, and merging the result into `metrics_local.json` and
`model_config.json["evaluation"]["end_to_end"]`.

## Drop in the real Kaggle artefacts

```bash
cp <kaggle export>/beat_model_ts.pt <kaggle export>/model_config.json ml/models/cardiosense_beat_model/
# optional: beat_model.pth, beat_model.onnx; remove metrics_local.json / verify_local.json (they describe the stand-in)
# restart the backend (it reads CARDIOSENSE_MODEL_DIR, default ml/models/cardiosense_beat_model)
~/.venvs/cardiosense/bin/python ml/verify_model.py      # sanity check through the real inference path
```

Nothing else changes: file names, tensor shapes and the config keys used by `cardiosense_beats.py`
(`fs, pre, post, margin, template_prev_beats, bandpass_hz, filter_order, class_bias, unknown_threshold,
model_version`) are identical. The Kaggle config has no `stand_in` key, so treat a missing key as `false`.

## Retrain

```bash
PY=~/.venvs/cardiosense/bin/python
$PY ml/train_local.py prepare                                    # mitdb + nstdb -> data/physionet/, beat cache -> ~/.cache/cardiosense/
$PY ml/train_local.py smoke --export                             # 1 epoch on 10k beats: step timing + interface check
$PY ml/train_local.py eval   --epochs 8 --swa-epochs 3 --seeds 42 --export   # DS1 -> DS2 metrics (metrics_local.json)
$PY ml/train_local.py deploy --epochs 6 --swa-epochs 2 --seeds 42            # DS1+DS2 model -> models/ (atomic rename)
$PY ml/verify_model.py                                           # records 100, 119, 208 + data/records/208.csv
```

`deploy` overwrites `model_config.json`, so the `end_to_end` block and `verify_local.json` have to be regenerated
by hand afterwards (see above). `verify_model.py --ds2` on the shipped model is in-sample, because DS2 is in its
training data.

About 2 to 2.5 s per step (batch 512) on 3 CPU threads: ~3.5 min per DS1 epoch, ~6.5 min per DS1+DS2 epoch; peak RSS
about 1.7 GB. `--seeds 42 43 44 --epochs 15 --swa-epochs 5` reproduces the notebook's budget (several hours on CPU).
Environment overrides: `CARDIOSENSE_PHYSIONET_DIR`, `CARDIOSENSE_CACHE_DIR`, `CARDIOSENSE_MODEL_DIR`.

## Differences from the notebook

- Budget: 1 seed and 8 (evaluation) / 6 (deployment) epochs with SWA over the last 3 / 2, instead of 3 seeds x 15
  epochs with SWA over 5. The export is still a `BeatEnsemble` (softmax averaging) with one member.
- Deployment data: MIT-BIH DS1+DS2 only. INCART is not downloaded, so there is no INCART training data and no
  external-hospital test.
- No 5-fold CV / model selection, no beat-wise 70/30 split, no noise or sample-rate stress tests: the script starts
  from the notebook's selected configuration (`resnet_rr_tpl`, cw_power 0.5, label smoothing 0.05, lr 1e-3, wd 1e-3,
  batch 512, class_bias `[0,0,0,0]`).
- CPU float32, no autocast / GradScaler. No ONNX export (`files` in the config lists only what exists).
- `model_config.json` has the notebook's keys plus `stand_in`, `stand_in_note`; `evaluation` holds only local
  measurements (`ds2_local_standin`, `ds2_per_seed`, `ds2_confusion_matrix`, `unknown_threshold_table_ds2`,
  `end_to_end`).
