#!/usr/bin/env python3
"""
MSAI-630 Residency — Conditional WGAN-GP on CelebA (Blond_Hair).

Adapted from Generative Deep Learning, 2nd Edition (Foster) / davidADSP cgan.ipynb:
https://github.com/davidADSP/Generative_Deep_Learning_2nd_Edition/blob/main/notebooks/04_gan/03_cgan/cgan.ipynb

Textbook techniques applied here (Ch. 4 GAN / CGAN):
  - Conditional WGAN-GP (Wasserstein critic + gradient penalty)
  - Label concatenation: vector labels for G, spatial maps for C
  - Critic trained multiple steps per generator step (CRITIC_STEPS)
  - Adam betas from the book (0.5 / 0.9)

Further improvements for a short residency deadline (efficiency + conditioning):
  - Class-balanced Blond_Hair sampling (~16% natural rate otherwise)
  - In-memory cached tensors (avoids repeated TFDS decode on CPU)
  - Graph-mode training (run_eagerly=False) instead of the book's eager demo
  - More steps/epoch than the book's steps_per_epoch=1 demo pattern
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import tensorflow as tf
import tensorflow_datasets as tfds
from tensorflow.keras import callbacks, layers, metrics, models, optimizers

# ---------------------------------------------------------------------------
# 0. Parameters (match the reference notebook where possible)
# ---------------------------------------------------------------------------
IMAGE_SIZE = 64
CHANNELS = 3
CLASSES = 2
BATCH_SIZE = 64
Z_DIM = 32
LEARNING_RATE = 0.00005
ADAM_BETA_1 = 0.5
ADAM_BETA_2 = 0.9
CRITIC_STEPS = 3
GP_WEIGHT = 10.0
LABEL = "Blond_Hair"

ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "output"
CHECKPOINT_DIR = ROOT / "checkpoint"
MODELS_DIR = ROOT / "models"
LOG_DIR = ROOT / "logs"
CACHE_DIR = ROOT / "data" / "cache"


def ensure_dirs() -> None:
    for d in (OUTPUT_DIR, CHECKPOINT_DIR, MODELS_DIR, LOG_DIR, CACHE_DIR):
        d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# 1. Data — balanced Blond_Hair (textbook CGAN needs both classes present)
# ---------------------------------------------------------------------------
def _resize_norm(image):
    image = tf.image.resize(image, (IMAGE_SIZE, IMAGE_SIZE))
    return (tf.cast(image, tf.float32) - 127.5) / 127.5


def materialize_balanced_arrays(
    per_class: int, seed: int = 42
) -> tuple[np.ndarray, np.ndarray]:
    """Load equal not-blond / blond faces into RAM (major CPU efficiency win)."""
    cache_x = CACHE_DIR / f"balanced_x_{per_class}.npy"
    cache_y = CACHE_DIR / f"balanced_y_{per_class}.npy"
    if cache_x.exists() and cache_y.exists():
        print(f"Loading cached balanced arrays from {CACHE_DIR}")
        return np.load(cache_x), np.load(cache_y)

    data_dir = os.environ.get("TFDS_DATA_DIR", str(ROOT / "data" / "tfds"))
    print("Materializing class-balanced CelebA subset (one-time)...")
    ds = tfds.load("celeb_a", split="train", shuffle_files=True, data_dir=data_dir)
    ds = ds.shuffle(10_000, seed=seed, reshuffle_each_iteration=False)

    xs_0, xs_1 = [], []
    for ex in ds:
        blond = bool(ex["attributes"][LABEL].numpy())
        img = _resize_norm(ex["image"]).numpy()
        if blond and len(xs_1) < per_class:
            xs_1.append(img)
        elif (not blond) and len(xs_0) < per_class:
            xs_0.append(img)
        if len(xs_0) >= per_class and len(xs_1) >= per_class:
            break

    x0 = np.stack(xs_0).astype("float32")
    x1 = np.stack(xs_1).astype("float32")
    y0 = np.repeat([[1.0, 0.0]], len(x0), axis=0).astype("float32")
    y1 = np.repeat([[0.0, 1.0]], len(x1), axis=0).astype("float32")
    x = np.concatenate([x0, x1], axis=0)
    y = np.concatenate([y0, y1], axis=0)
    rng = np.random.default_rng(seed)
    perm = rng.permutation(len(x))
    x, y = x[perm], y[perm]
    np.save(cache_x, x)
    np.save(cache_y, y)
    print(f"Cached {len(x)} images ({per_class}/class) -> {CACHE_DIR}")
    return x, y


def build_dataset(
    max_samples: int,
    batch_size: int,
    balanced: bool,
    per_class: int,
    seed: int,
) -> tf.data.Dataset:
    if balanced:
        x, y = materialize_balanced_arrays(per_class=per_class, seed=seed)
        ds = tf.data.Dataset.from_tensor_slices((x, y))
    else:
        data_dir = os.environ.get("TFDS_DATA_DIR", str(ROOT / "data" / "tfds"))
        ds = tfds.load(
            "celeb_a", split="train", shuffle_files=True, data_dir=data_dir
        )

        def preprocess_example(example):
            image = _resize_norm(example["image"])
            blond = tf.cast(example["attributes"][LABEL], tf.int32)
            return image, tf.one_hot(blond, depth=CLASSES)

        ds = ds.map(preprocess_example, num_parallel_calls=tf.data.AUTOTUNE)
        if max_samples > 0:
            ds = ds.take(max_samples)

    ds = ds.cache()
    ds = ds.shuffle(min(4096, max(batch_size * 16, 512)), reshuffle_each_iteration=True)
    ds = ds.repeat()
    ds = ds.batch(batch_size, drop_remainder=True)
    ds = ds.prefetch(tf.data.AUTOTUNE)
    return ds


def save_real_sample_grid(dataset: tf.data.Dataset, path: Path, n: int = 10) -> None:
    batch = next(iter(dataset))
    images = batch[0].numpy()[:n]
    _save_image_grid(images, path, title="Real CelebA samples")


def _save_image_grid(images: np.ndarray, path: Path, title: str | None = None) -> None:
    images = images.copy()
    if images.max() > 1.0:
        images = images / 255.0
    elif images.min() < 0.0:
        images = (images + 1.0) / 2.0

    n = len(images)
    fig, axes = plt.subplots(1, n, figsize=(2 * n, 2.5))
    if n == 1:
        axes = [axes]
    for ax, img in zip(axes, images):
        ax.imshow(np.clip(img, 0.0, 1.0))
        ax.axis("off")
    if title:
        fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight", dpi=120)
    plt.close(fig)
    print(f"Saved {path}")


# ---------------------------------------------------------------------------
# 2. Build the GAN (Foster / book architectures)
# ---------------------------------------------------------------------------
def build_critic() -> models.Model:
    critic_input = layers.Input(shape=(IMAGE_SIZE, IMAGE_SIZE, CHANNELS))
    label_input = layers.Input(shape=(IMAGE_SIZE, IMAGE_SIZE, CLASSES))
    x = layers.Concatenate(axis=-1)([critic_input, label_input])
    x = layers.Conv2D(64, kernel_size=4, strides=2, padding="same")(x)
    x = layers.LeakyReLU(0.2)(x)
    x = layers.Conv2D(128, kernel_size=4, strides=2, padding="same")(x)
    x = layers.LeakyReLU()(x)
    x = layers.Dropout(0.3)(x)
    x = layers.Conv2D(128, kernel_size=4, strides=2, padding="same")(x)
    x = layers.LeakyReLU(0.2)(x)
    x = layers.Dropout(0.3)(x)
    x = layers.Conv2D(128, kernel_size=4, strides=2, padding="same")(x)
    x = layers.LeakyReLU(0.2)(x)
    x = layers.Dropout(0.3)(x)
    x = layers.Conv2D(1, kernel_size=4, strides=1, padding="valid")(x)
    critic_output = layers.Flatten()(x)
    return models.Model([critic_input, label_input], critic_output, name="critic")


def build_generator() -> models.Model:
    generator_input = layers.Input(shape=(Z_DIM,))
    label_input = layers.Input(shape=(CLASSES,))
    x = layers.Concatenate(axis=-1)([generator_input, label_input])
    x = layers.Reshape((1, 1, Z_DIM + CLASSES))(x)
    x = layers.Conv2DTranspose(
        128, kernel_size=4, strides=1, padding="valid", use_bias=False
    )(x)
    x = layers.BatchNormalization(momentum=0.9)(x)
    x = layers.LeakyReLU(0.2)(x)
    x = layers.Conv2DTranspose(
        128, kernel_size=4, strides=2, padding="same", use_bias=False
    )(x)
    x = layers.BatchNormalization(momentum=0.9)(x)
    x = layers.LeakyReLU(0.2)(x)
    x = layers.Conv2DTranspose(
        128, kernel_size=4, strides=2, padding="same", use_bias=False
    )(x)
    x = layers.BatchNormalization(momentum=0.9)(x)
    x = layers.LeakyReLU(0.2)(x)
    x = layers.Conv2DTranspose(
        64, kernel_size=4, strides=2, padding="same", use_bias=False
    )(x)
    x = layers.BatchNormalization(momentum=0.9)(x)
    x = layers.LeakyReLU(0.2)(x)
    generator_output = layers.Conv2DTranspose(
        CHANNELS, kernel_size=4, strides=2, padding="same", activation="tanh"
    )(x)
    return models.Model(
        [generator_input, label_input], generator_output, name="generator"
    )


class ConditionalWGAN(models.Model):
    """Conditional WGAN-GP. train_step matches generator vs critic label formats."""

    def __init__(self, critic, generator, latent_dim, critic_steps, gp_weight):
        super().__init__()
        self.critic = critic
        self.generator = generator
        self.latent_dim = latent_dim
        self.critic_steps = critic_steps
        self.gp_weight = gp_weight

    def compile(self, c_optimizer, g_optimizer, run_eagerly: bool = False):
        # Book notebook uses eager for teaching; graph mode is much faster on CPU.
        super().compile(run_eagerly=run_eagerly)
        self.c_optimizer = c_optimizer
        self.g_optimizer = g_optimizer
        self.c_wass_loss_metric = metrics.Mean(name="c_wass_loss")
        self.c_gp_metric = metrics.Mean(name="c_gp")
        self.c_loss_metric = metrics.Mean(name="c_loss")
        self.g_loss_metric = metrics.Mean(name="g_loss")

    @property
    def metrics(self):
        return [
            self.c_loss_metric,
            self.c_wass_loss_metric,
            self.c_gp_metric,
            self.g_loss_metric,
        ]

    def gradient_penalty(
        self, batch_size, real_images, fake_images, image_one_hot_labels
    ):
        alpha = tf.random.normal([batch_size, 1, 1, 1], 0.0, 1.0)
        diff = fake_images - real_images
        interpolated = real_images + alpha * diff

        with tf.GradientTape() as gp_tape:
            gp_tape.watch(interpolated)
            pred = self.critic([interpolated, image_one_hot_labels], training=True)

        grads = gp_tape.gradient(pred, [interpolated])[0]
        norm = tf.sqrt(tf.reduce_sum(tf.square(grads), axis=[1, 2, 3]))
        gp = tf.reduce_mean((norm - 1.0) ** 2)
        return gp

    def train_step(self, data):
        real_images, one_hot_labels = data

        # REQUIRED FIX: vector label -> spatial label map for the critic
        image_one_hot_labels = one_hot_labels[:, None, None, :]
        image_one_hot_labels = tf.repeat(
            image_one_hot_labels, repeats=IMAGE_SIZE, axis=1
        )
        image_one_hot_labels = tf.repeat(
            image_one_hot_labels, repeats=IMAGE_SIZE, axis=2
        )

        batch_size = tf.shape(real_images)[0]

        for _ in range(self.critic_steps):
            random_latent_vectors = tf.random.normal(
                shape=(batch_size, self.latent_dim)
            )
            with tf.GradientTape() as tape:
                fake_images = self.generator(
                    [random_latent_vectors, one_hot_labels], training=True
                )
                fake_predictions = self.critic(
                    [fake_images, image_one_hot_labels], training=True
                )
                real_predictions = self.critic(
                    [real_images, image_one_hot_labels], training=True
                )
                c_wass_loss = tf.reduce_mean(fake_predictions) - tf.reduce_mean(
                    real_predictions
                )
                c_gp = self.gradient_penalty(
                    batch_size, real_images, fake_images, image_one_hot_labels
                )
                c_loss = c_wass_loss + c_gp * self.gp_weight

            c_gradient = tape.gradient(c_loss, self.critic.trainable_variables)
            self.c_optimizer.apply_gradients(
                zip(c_gradient, self.critic.trainable_variables)
            )

        random_latent_vectors = tf.random.normal(shape=(batch_size, self.latent_dim))
        with tf.GradientTape() as tape:
            fake_images = self.generator(
                [random_latent_vectors, one_hot_labels], training=True
            )
            fake_predictions = self.critic(
                [fake_images, image_one_hot_labels], training=True
            )
            g_loss = -tf.reduce_mean(fake_predictions)

        gen_gradient = tape.gradient(g_loss, self.generator.trainable_variables)
        self.g_optimizer.apply_gradients(
            zip(gen_gradient, self.generator.trainable_variables)
        )

        self.c_loss_metric.update_state(c_loss)
        self.c_wass_loss_metric.update_state(c_wass_loss)
        self.c_gp_metric.update_state(c_gp)
        self.g_loss_metric.update_state(g_loss)
        return {m.name: m.result() for m in self.metrics}


class ImageGenerator(callbacks.Callback):
    def __init__(self, num_img, latent_dim, every_n_epochs=20, prefix="improved"):
        super().__init__()
        self.num_img = num_img
        self.latent_dim = latent_dim
        self.every_n_epochs = every_n_epochs
        self.prefix = prefix
        self.fixed_noise = tf.random.normal(shape=(num_img, latent_dim))

    def on_epoch_end(self, epoch, logs=None):
        if (epoch + 1) % self.every_n_epochs != 0 and epoch != 0:
            return
        zero_label = tf.tile(tf.constant([[1.0, 0.0]]), [self.num_img, 1])
        one_label = tf.tile(tf.constant([[0.0, 1.0]]), [self.num_img, 1])

        imgs0 = self.model.generator([self.fixed_noise, zero_label], training=False)
        imgs1 = self.model.generator([self.fixed_noise, one_label], training=False)
        _save_image_grid(
            imgs0.numpy(),
            OUTPUT_DIR / f"{self.prefix}_epoch_{epoch:03d}_label_0.png",
            title=f"{self.prefix} epoch {epoch} — label 0",
        )
        _save_image_grid(
            imgs1.numpy(),
            OUTPUT_DIR / f"{self.prefix}_epoch_{epoch:03d}_label_1.png",
            title=f"{self.prefix} epoch {epoch} — label 1 (Blond_Hair)",
        )


def generate_final_samples(generator, num_img: int = 10, prefix: str = "final") -> None:
    z = np.random.normal(size=(num_img, Z_DIM)).astype("float32")
    label0 = np.repeat([[1, 0]], num_img, axis=0).astype("float32")
    label1 = np.repeat([[0, 1]], num_img, axis=0).astype("float32")
    imgs0 = generator.predict([z, label0], verbose=0)
    imgs1 = generator.predict([z, label1], verbose=0)

    _save_image_grid(
        imgs0,
        OUTPUT_DIR / f"{prefix}_label_0_not_blond.png",
        title=f"{prefix} — label 0 (not Blond_Hair)",
    )
    _save_image_grid(
        imgs1,
        OUTPUT_DIR / f"{prefix}_label_1_blond.png",
        title=f"{prefix} — label 1 (Blond_Hair)",
    )

    fig, axes = plt.subplots(2, num_img, figsize=(2 * num_img, 4.5))
    for i in range(num_img):
        for row, img, row_title in (
            (0, imgs0[i], "not blond"),
            (1, imgs1[i], "blond"),
        ):
            show = (img + 1.0) / 2.0
            axes[row, i].imshow(np.clip(show, 0.0, 1.0))
            axes[row, i].axis("off")
            if i == 0:
                axes[row, i].set_ylabel(row_title)
    fig.suptitle("Shared noise z — top: label 0, bottom: label 1 (Blond_Hair)")
    fig.tight_layout()
    pair_path = OUTPUT_DIR / f"{prefix}_shared_z_comparison.png"
    fig.savefig(pair_path, bbox_inches="tight", dpi=120)
    plt.close(fig)
    print(f"Saved {pair_path}")


def parse_args():
    p = argparse.ArgumentParser(description="Train Blond_Hair Conditional WGAN-GP")
    p.add_argument("--epochs", type=int, default=80)
    p.add_argument("--steps-per-epoch", type=int, default=25)
    p.add_argument("--max-samples", type=int, default=8000)
    p.add_argument("--per-class", type=int, default=4000, help="Images per Blond class")
    p.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument(
        "--balanced",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Class-balance Blond_Hair (textbook CGAN improvement)",
    )
    p.add_argument(
        "--eager",
        action="store_true",
        help="Use run_eagerly=True like the book notebook (slower)",
    )
    p.add_argument("--prefix", type=str, default="improved")
    return p.parse_args()


def main():
    args = parse_args()
    ensure_dirs()
    tf.keras.utils.set_random_seed(args.seed)

    # Prefer all CPU cores without oversubscription thrash
    tf.config.threading.set_intra_op_parallelism_threads(0)
    tf.config.threading.set_inter_op_parallelism_threads(0)

    print(f"Conditioning attribute: {LABEL}")
    print(f"TensorFlow {tf.__version__}")
    print(
        f"Mode: {'eager' if args.eager else 'graph'}; "
        f"balanced={args.balanced}; epochs={args.epochs}; "
        f"steps/epoch={args.steps_per_epoch}"
    )

    t0 = time.time()
    train = build_dataset(
        max_samples=args.max_samples,
        batch_size=args.batch_size,
        balanced=args.balanced,
        per_class=args.per_class,
        seed=args.seed,
    )
    save_real_sample_grid(train, OUTPUT_DIR / "real_celeba_samples.png")

    critic = build_critic()
    generator = build_generator()
    critic.summary()
    generator.summary()

    cgan = ConditionalWGAN(
        critic=critic,
        generator=generator,
        latent_dim=Z_DIM,
        critic_steps=CRITIC_STEPS,
        gp_weight=GP_WEIGHT,
    )
    cgan.compile(
        c_optimizer=optimizers.Adam(
            learning_rate=LEARNING_RATE, beta_1=ADAM_BETA_1, beta_2=ADAM_BETA_2
        ),
        g_optimizer=optimizers.Adam(
            learning_rate=LEARNING_RATE, beta_1=ADAM_BETA_1, beta_2=ADAM_BETA_2
        ),
        run_eagerly=args.eager,
    )

    history = cgan.fit(
        train,
        epochs=args.epochs,
        steps_per_epoch=args.steps_per_epoch,
        callbacks=[
            ImageGenerator(
                num_img=10,
                latent_dim=Z_DIM,
                every_n_epochs=max(10, args.epochs // 4),
                prefix=args.prefix,
            )
        ],
    )
    elapsed = time.time() - t0
    print(f"Training wall time: {elapsed / 60:.1f} min")

    generate_final_samples(generator, num_img=10, prefix=args.prefix)
    # Also refresh canonical submission filenames used by observations.md
    for src_name, dst_name in (
        (f"{args.prefix}_label_0_not_blond.png", "final_label_0_not_blond.png"),
        (f"{args.prefix}_label_1_blond.png", "final_label_1_blond.png"),
        (f"{args.prefix}_shared_z_comparison.png", "final_shared_z_comparison.png"),
    ):
        src, dst = OUTPUT_DIR / src_name, OUTPUT_DIR / dst_name
        if src.exists():
            shutil.copy2(src, dst)

    history_path = OUTPUT_DIR / f"{args.prefix}_training_history.json"
    serializable = {k: [float(x) for x in v] for k, v in history.history.items()}
    history_path.write_text(json.dumps(serializable, indent=2))
    shutil.copy2(history_path, OUTPUT_DIR / "training_history.json")

    try:
        generator.save(MODELS_DIR / "generator.keras")
        critic.save(MODELS_DIR / "critic.keras")
    except Exception as exc:  # noqa: BLE001
        print(f"Model save skipped: {exc}")

    meta = {
        "label": LABEL,
        "classes": CLASSES,
        "z_dim": Z_DIM,
        "image_size": IMAGE_SIZE,
        "epochs": args.epochs,
        "steps_per_epoch": args.steps_per_epoch,
        "total_steps": args.epochs * args.steps_per_epoch,
        "max_samples": args.max_samples,
        "per_class": args.per_class if args.balanced else None,
        "balanced": args.balanced,
        "run_eagerly": args.eager,
        "batch_size": args.batch_size,
        "critic_steps": CRITIC_STEPS,
        "gp_weight": GP_WEIGHT,
        "wall_time_sec": elapsed,
        "textbook": "Foster, Generative Deep Learning 2e — Ch.4 Conditional WGAN-GP",
        "improvements": [
            "class-balanced Blond_Hair sampling",
            "in-memory cached tensors",
            "graph-mode train_step (not eager)",
            "more steps/epoch than book demo (steps_per_epoch=1)",
        ],
        "note": (
            "train_step expands one_hot_labels (batch, 2) to "
            "image_one_hot_labels (batch, 64, 64, 2) for the critic."
        ),
    }
    (OUTPUT_DIR / f"{args.prefix}_run_metadata.json").write_text(
        json.dumps(meta, indent=2)
    )
    shutil.copy2(
        OUTPUT_DIR / f"{args.prefix}_run_metadata.json",
        OUTPUT_DIR / "run_metadata.json",
    )
    print("Training complete.")


if __name__ == "__main__":
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
    main()
