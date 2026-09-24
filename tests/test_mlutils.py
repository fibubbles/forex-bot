import numpy as np
import pytest

from src.mlutils import IsotonicCalibrator, roc_auc


def test_roc_auc_basic_cases():
    y = np.array([0, 0, 1, 1])
    assert roc_auc(y, [0.1, 0.2, 0.8, 0.9]) == pytest.approx(1.0)
    assert roc_auc(y, [0.9, 0.8, 0.2, 0.1]) == pytest.approx(0.0)
    assert roc_auc(y, [0.5, 0.5, 0.5, 0.5]) == pytest.approx(0.5)
    assert np.isnan(roc_auc([1, 1], [0.2, 0.3]))


def test_isotonic_is_monotonic_clipped_and_accurate():
    rng = np.random.default_rng(0)
    x = rng.uniform(0, 1, 2000)
    y = (rng.uniform(0, 1, 2000) < x).astype(float)  # true probability = x

    cal = IsotonicCalibrator().fit(x, y)
    p = cal.predict(np.linspace(-0.5, 1.5, 50))

    assert np.all(np.diff(p) >= 0)
    assert p.min() >= 0 and p.max() <= 1
    assert cal.predict([0.25])[0] == pytest.approx(0.25, abs=0.1)