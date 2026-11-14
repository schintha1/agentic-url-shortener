# Assignment — Building a Conditional GAN (MSAI-630)

**Course:** MSAI-630 • Generative AI with Large Language Models  
**Textbook:** David Foster, *Generative Deep Learning*, 2nd Edition (ISBN 9781098134143)

## Problem

Implement a conditional generative adversarial network (CGAN) using the course/book notebook approach:

1. Condition generation on the CelebA faces dataset’s **Blond_Hair** attribute.
2. Modify CGAN `train_step` so generator and critic label input formats match
   (generator: vector one-hot; critic: spatial label map).
3. Submit **code + generated output** and **interpret** observations about output and accuracy
   (fidelity, diversity, conditioning).

## This repository

| Deliverable | Path |
|-------------|------|
| Problem statement | `ASSIGNMENT.md` (this file) |
| Code | `train_cgan.py`, `notebooks/cgan_blond_hair.ipynb` |
| Observations + further improvements | `observations.md` |
| Generated samples (both classes + shared-z) | `output/final_*.png` |
| How to run / upload checklist | `README.md`, `SUBMISSION_CHECKLIST.md` |

Reference notebook: https://github.com/davidADSP/Generative_Deep_Learning_2nd_Edition/blob/main/notebooks/04_gan/03_cgan/cgan.ipynb
