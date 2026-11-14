# MSAI-630 Residency — Conditional GAN (Blond_Hair)

University of the Cumberlands · PhD in IT (AI Specialization)  
**Course:** Generative AI with Large Language Models (MSAI-630-M41)  
**Assignment:** Residency Day 1 / Project 1 — Building a Conditional GAN  
**Textbook:** David Foster, *Generative Deep Learning*, 2nd Edition (ISBN 9781098134143)

Academic submission package (not an enterprise application). Implements a **Conditional WGAN-GP** on CelebA faces conditioned on **Blond_Hair**, with the required `train_step` label-format fix.

Reference notebook: [davidADSP / cgan.ipynb](https://github.com/davidADSP/Generative_Deep_Learning_2nd_Edition/blob/main/notebooks/04_gan/03_cgan/cgan.ipynb)

---

## What to upload to SafeAssign / LMS

Matches the course prompt: **code + generated output + interpret observations / accuracy**.

| Item | Path | Rubric |
|------|------|--------|
| Code (notebook) | `notebooks/cgan_blond_hair.ipynb` | Code + output (50) |
| Training script | `train_cgan.py` | Code + output (50) |
| Generated pictures (both classes + shared-`z`) | `output/final_label_0_not_blond.png`, `output/final_label_1_blond.png`, `output/final_shared_z_comparison.png` | Code output |
| Observations / accuracy | `observations.md` | Observations (50) — includes **further improvements** |

Optional helpers: `SUBMISSION_CHECKLIST.md`, `output/baseline_run/` (before/after), `README.md`.

---

## Requirements implemented

1. **Condition on Blond_Hair** — two-class one-hot (`[1,0]` not blond, `[0,1]` blond).
2. **`train_step` matches generator vs critic inputs**
   - Generator: noise `(batch, 32)` + vector label `(batch, 2)`
   - Critic: image + **spatial** label map `(batch, 64, 64, 2)`
3. **Code + output + interpretation** — fidelity, diversity, conditioning, plus textbook further improvements in `observations.md`.

---

## How to run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export TFDS_DATA_DIR=./data/tfds   # after first CelebA download

# Primary recorded run (balanced Blond_Hair, graph mode, ~24 min CPU)
python train_cgan.py --epochs 80 --steps-per-epoch 25 --per-class 4000 --balanced

# Book-style eager demo (slower)
python train_cgan.py --eager --epochs 40 --steps-per-epoch 5 --no-balanced
```

Open `notebooks/cgan_blond_hair.ipynb` for the architecture walkthrough and `train_step` explanation.  
Read `observations.md` for the graded interpretation and further improvements.

---

## Project layout

```
.
├── README.md
├── SUBMISSION_CHECKLIST.md
├── observations.md            # graded write-up (incl. further improvements)
├── FURTHER_IMPROVEMENTS.md    # pointer → observations.md §4
├── requirements.txt
├── train_cgan.py
├── notebooks/cgan_blond_hair.ipynb
└── output/
    ├── final_*.png            # primary submission figures
    └── baseline_run/          # short run before improvements
```
