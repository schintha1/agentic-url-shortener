# Submission checklist — MSAI-630 Project 1 (CGAN)

Package for Blackboard / SafeAssign using the course prompt:

> Condition on blond-hair · Fix `train_step` label formats · Submit **code + output** · **Interpret** observations and accuracy

## Required by the prompt

- [x] Condition CGAN on CelebA **Blond_Hair**
- [x] Modify CGAN **`train_step`** so generator and critic label formats match
- [x] Provide **code** used and its **output** (both classes)
- [x] **Interpret** observations about output and accuracy (fidelity / diversity / conditioning)
- [x] **Further improvements** documented inside `observations.md` (Foster textbook approaches)

## Files to attach (minimum)

1. `notebooks/cgan_blond_hair.ipynb` — code
2. `train_cgan.py` — runnable training script (same model / `train_step`)
3. `observations.md` — observations + accuracy + further improvements
4. Pictures:
   - `output/final_label_0_not_blond.png`
   - `output/final_label_1_blond.png`
   - `output/final_shared_z_comparison.png` (same `z`, both labels)

## Optional extras

5. `output/baseline_run/` — before-improvements short run
6. `README.md` / this checklist

Do **not** treat `FURTHER_IMPROVEMENTS.md` as a separate LMS upload — it only points to `observations.md` §4.

## Rubric self-check

| Criterion | Target | Where shown |
|-----------|--------|-------------|
| Code for network looks correct | Complete (50) | Notebook + `train_cgan.py` + fixed `train_step` |
| Code output makes sense | Complete | Both-class + shared-`z` grids |
| Observations / accuracy | Complete (50) | `observations.md` §§1–4 (fidelity, diversity, conditioning, further improvements) |

## Academic note

Faces are still soft/noisy. The shared-`z` figure is the evidence that **blond control works**; fidelity can improve with longer GPU training (Foster progressive / longer WGAN-GP runs).
