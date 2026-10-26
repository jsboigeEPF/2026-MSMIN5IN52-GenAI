import math
import unicodedata

import numpy as np


def describe(values: list[float], seed: int = 42, bootstrap: int = 1000) -> dict:
    if not values:
        return {"n": 0}
    data = np.asarray(values, dtype=float)
    if not np.isfinite(data).all():
        raise ValueError("Mesures non finies")
    rng = np.random.default_rng(seed)
    medians = [float(np.median(rng.choice(data, len(data)))) for _ in range(bootstrap)]
    return {
        "n": len(data),
        "min": float(data.min()),
        "max": float(data.max()),
        "p50": float(np.percentile(data, 50)),
        "p95": float(np.percentile(data, 95)),
        "p50_ci_low": float(np.percentile(medians, 2.5)),
        "p50_ci_high": float(np.percentile(medians, 97.5)),
        "p95_reliable": len(data) >= 20,
    }


def wilson(successes: int, trials: int) -> dict:
    if not 0 <= successes <= trials:
        raise ValueError("Proportion invalide")
    if trials == 0:
        return {"n": 0, "rate": None, "ci_low": None, "ci_high": None}
    z = 1.959963984540054
    proportion = successes / trials
    denominator = 1 + z * z / trials
    centre = (proportion + z * z / (2 * trials)) / denominator
    radius = z * math.sqrt(proportion * (1 - proportion) / trials + z * z / (4 * trials**2))
    radius /= denominator
    return {
        "n": trials,
        "rate": proportion,
        "ci_low": centre - radius,
        "ci_high": centre + radius,
    }


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    text = "".join(" " if unicodedata.category(char).startswith("P") else char for char in text)
    return " ".join(text.split())


def word_errors(reference: str, hypothesis: str) -> dict:
    from jiwer import process_words

    result = process_words(normalize(reference), normalize(hypothesis))
    words = result.hits + result.substitutions + result.deletions
    errors = result.substitutions + result.deletions + result.insertions
    return {"words": words, "errors": errors, "wer": result.wer}
