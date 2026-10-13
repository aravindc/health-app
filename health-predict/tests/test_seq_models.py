import numpy as np
import pytest

torch = pytest.importorskip("torch")

from src.seq_models import CausalBlock, SequenceRegressor

CFG = {
    "device": "cpu",
    "lstm": {"hidden_size": 16, "num_layers": 1, "dropout": 0.0},
    "tcn": {"channels": 8, "kernel_size": 3, "levels": 3, "dropout": 0.0},
    "training": {"batch_size": 64, "learning_rate": 0.01, "weight_decay": 0.0, "max_epochs": 30, "patience": 5,
                 "huber_delta": 1.0},
}


def test_tcn_block_is_causal():
    torch.manual_seed(0)
    block = CausalBlock(2, 4, 3, dilation=2, dropout=0.0).eval()
    x = torch.randn(1, 2, 20)
    y1 = block(x)
    x2 = x.clone()
    x2[:, :, 12:] += 5.0                     # change the future
    y2 = block(x2)
    assert torch.allclose(y1[:, :, :12], y2[:, :, :12])
    assert not torch.allclose(y1[:, :, 12:], y2[:, :, 12:])


@pytest.mark.parametrize("kind", ["lstm", "tcn"])
def test_learns_trend_and_handles_missing_targets(kind):
    rng = np.random.default_rng(0)
    n, steps = 1500, 12
    slope = rng.normal(0, 1, n)
    X = (np.arange(steps)[None, :] * slope[:, None])[:, :, None] + rng.normal(0, 0.05, (n, steps, 1))
    X = X.astype(np.float32)
    Y = np.stack([slope * 3, slope * 6], axis=1).astype(np.float32)  # future change grows with the trend
    Y[::5, 1] = np.nan                                               # some horizons missing
    reg = SequenceRegressor(kind, CFG, seed=0).fit(X[:1200], Y[:1200], X[1200:], Y[1200:])
    pred = reg.predict(X[1200:])
    assert pred.shape == (300, 2) and np.isfinite(pred).all()
    assert np.corrcoef(pred[:, 0], Y[1200:, 0])[0, 1] > 0.95
    assert np.isfinite(reg.x_mean_).all() and reg.epochs_ >= 1


def test_scaling_uses_training_data_only():
    X = np.ones((50, 4, 1), dtype=np.float32)
    Y = np.zeros((50, 1), dtype=np.float32)
    reg = SequenceRegressor("lstm", {**CFG, "training": {**CFG["training"], "max_epochs": 1}}, 0)
    reg.fit(X, Y, X * 100, Y)
    assert reg.x_mean_[0] == pytest.approx(1.0)
