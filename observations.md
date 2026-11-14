# Observations about Output and Accuracy

**Course:** MSAI-630-M41 — Generative AI with Large Language Models  
**Assignment:** Residency Day 1 / Project 1 — Building a Conditional GAN  
**Textbook:** David Foster, *Generative Deep Learning*, 2nd Edition (ISBN 9781098134143), Chapter 4  

This write-up is the graded **observations / accuracy** deliverable. It interprets the code output (fidelity, diversity, conditioning) and records the **further improvements** applied from Foster’s textbook approaches after the first short run.

**Primary figures (upload these):**

- `output/final_label_0_not_blond.png`
- `output/final_label_1_blond.png`
- `output/final_shared_z_comparison.png` (same noise `z`, label flipped)

Baseline short-run figures (before improvements) are archived in `output/baseline_run/`.

---

## Assignment requirements covered

| Requirement | Where shown |
|-------------|-------------|
| Condition generation on CelebA **Blond_Hair** | Code (`train_cgan.py`, notebook) + both-class outputs |
| Modify CGAN **`train_step`** so generator and critic label formats match | Generator gets vector `(batch, 2)`; critic / GP get spatial map `(batch, 64, 64, 2)` |
| Submit **code + generated output** | Notebook, script, PNG grids |
| **Interpret** observations and accuracy | This document |

---

## Setup summary

| Item | Baseline run | Improved run (primary) |
|------|--------------|------------------------|
| Conditioning | `Blond_Hair` | `Blond_Hair` |
| Data mix | First 8k images (~16% blond) | **Balanced** 4k not-blond + 4k blond |
| Updates | 40×5 = **200** steps | 80×25 = **2,000** steps |
| Mode | Eager (`run_eagerly=True`) | **Graph** (~0.65–0.70 s/step) |
| Wall time (CPU) | ~5 min | **~24 min** |
| Artifacts | `output/baseline_run/` | `output/final_*.png` |

Reproduce the improved run:

```bash
python train_cgan.py --epochs 80 --steps-per-epoch 25 --per-class 4000 --balanced --prefix improved
```

---

## 1. Fidelity — Do samples resemble real faces?

**Verdict: improved but still moderate / early–mid fidelity.**

- Real references: `output/real_celeba_samples.png`.
- Improved finals show clearer **face layouts** (eye/nose/mouth regions) than the baseline blobs in `output/baseline_run/`.
- Images remain soft, noisy, and show transposed-convolution **checkerboard** texture. Photoreal quality would need longer GPU training or Progressive / StyleGAN-class methods from later Foster chapters — outside this residency slice.
- After 10× more WGAN-GP updates, structure is directionally better and enough to judge conditioning.

**Accuracy note:** Fidelity is **not** photoreal. The pictures support “face-like progressing samples,” not CelebA-quality faces.

---

## 2. Diversity — Does the generator avoid mode collapse?

**Verdict: no total mode collapse; diversity is visible.**

Across 10 samples per class:

- Skin tone, pose cues, and background brightness vary.
- Shared-`z` columns differ from each other (different identities/styles), so the generator is not stuck on one image.
- Composition is still mostly centered portraits; diversity is limited but not collapsed.

Balanced Blond_Hair sampling (Foster-aligned data hygiene for a rare attribute) also reduces collapse toward the majority dark-hair mode.

---

## 3. Conditioning — Does blond-hair control actually work?

**Verdict: yes — materially stronger than the baseline short run.**

Decisive figure: `output/final_shared_z_comparison.png` (same noise `z` per column; only the Blond_Hair label flips). This is the residency pair-discussion test: evidence that would convince you the model learned the condition.

What the pictures support:

1. **Label 0 (top):** darker hair regions (brown/black) in most columns.
2. **Label 1 (bottom):** systematically **lighter / yellowish** hair while pose/identity-like structure stays aligned with the top row.
3. Separate class grids agree: not-blond samples skew dark-haired; blond samples show light hair washes.

Baseline shared-`z` pairs were nearly identical across labels (weak/inconclusive). Balancing Blond_Hair (~16% natural rate → 50/50) plus more WGAN-GP steps was the main fix for conditioning accuracy.

---

## 4. Further improvements (Foster textbook approaches)

After the short Friday baseline, the following **improvements** from Foster Ch. 4 / the course CGAN notebook were applied and are reflected in the primary figures above.

### Textbook techniques retained

1. **Conditional generation** — concatenate class labels into generator and critic.
2. **WGAN-GP** — Wasserstein critic + gradient penalty (`GP_WEIGHT=10`).
3. **Multi-step critic** — `CRITIC_STEPS=3` before each generator update.
4. **Adam (β1=0.5, β2=0.9)** — book GAN training defaults.
5. **Spatial label maps for the critic** — required `train_step` expansion of one-hots to `(H, W, C)` so generator and critic input formats match.

### Efficiency / conditioning upgrades applied

| Improvement | Textbook / residency rationale | Result |
|-------------|-------------------------------|--------|
| **Class-balanced Blond_Hair** | Rare attribute (~16%) starves conditioning | Equal blond / not-blond exposure |
| **Cached resized tensors** | Avoid repeated JPEG decode on CPU | Faster steps; reusable `.npy` cache |
| **Graph-mode training** | Book demo uses eager for teaching; graph is same math, less overhead | ~0.65–0.70 s/step |
| **2,000 generator updates** | More WGAN-GP signal in one hour | ~24 min wall time vs ~5 min baseline |
| **Shared-`z` evaluation** | Foster-style conditional check | Clearer blond vs not-blond rows |

### Before → after

| | Baseline | Improved (submitted) |
|--|----------|----------------------|
| Steps | 200 | 2,000 |
| Blond mix | Natural ~16% | 50/50 |
| Conditioning evidence | Weak / inconclusive | Supported by shared-`z` figure |
| Fidelity | Soft blobs | Clearer face structure (still noisy) |

### Impact on grading lenses

| Lens | Why the improvement helps |
|------|---------------------------|
| Fidelity | More WGAN-GP steps + stable graph training |
| Diversity | Balanced classes reduce majority-mode collapse |
| Conditioning | Equal blond exposure + shared-`z` label flip |

---

## 5. Rubric self-check

| Criterion | Assessment |
|-----------|------------|
| **Code + output (50)** — network looks correct; output makes sense | Conditional WGAN-GP matches Foster/book; `train_step` label expansion present; both-class + shared-`z` grids uploaded |
| **Observations / accuracy (50)** — interpret fidelity, diversity, conditioning | Sections 1–3 above; Section 4 records further improvements from the textbook |

---

## 6. Conclusion

The submission meets the LMS prompt: Blond_Hair CGAN, `train_step` label-format fix, code + generated samples, and interpretation of output accuracy. After textbook-aligned improvements (balanced classes, cached tensors, graph-mode training, 2,000 WGAN-GP steps in ~24 CPU minutes), **blond-hair control is visually supported** by the shared-`z` comparison. Faces remain soft/noisy, so fidelity is still a longer-training problem — not a missing-condition problem.

**LMS / SafeAssign package**

1. Code: `notebooks/cgan_blond_hair.ipynb`, `train_cgan.py`
2. Output: `output/final_label_0_not_blond.png`, `final_label_1_blond.png`, `final_shared_z_comparison.png`
3. Observations (this file): fidelity, diversity, conditioning, and further improvements
