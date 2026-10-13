"""Sequence models (PyTorch): LSTM and a small causal TCN, predicting all horizons at once.

Targets are glucose deltas (future − current) for every horizon, standardised
with training-fold statistics. Missing targets for some horizons are masked
out of the loss. Input standardisation uses training-fold statistics only.
Early stopping uses an inner validation split taken from the END of the
training fold (chronological, separated by the embargo), never the outer
validation month.
"""

from __future__ import annotations

import copy
import logging

import numpy as np
import torch
from torch import nn

log = logging.getLogger("seq_models")


class LSTMNet(nn.Module):
    def __init__(self, n_in: int, n_out: int, hidden_size: int, num_layers: int, dropout: float):
        super().__init__()
        self.lstm = nn.LSTM(n_in, hidden_size, num_layers=num_layers, batch_first=True,
                            dropout=dropout if num_layers > 1 else 0.0)
        self.head = nn.Sequential(nn.Linear(hidden_size, hidden_size), nn.ReLU(), nn.Dropout(dropout),
                                  nn.Linear(hidden_size, n_out))

    def forward(self, x):
        out, _ = self.lstm(x)
        return self.head(out[:, -1])


class CausalBlock(nn.Module):
    def __init__(self, c_in: int, c_out: int, k: int, dilation: int, dropout: float):
        super().__init__()
        self.pad = (k - 1) * dilation  # left padding only: step t never sees later steps
        self.conv1 = nn.Conv1d(c_in, c_out, k, dilation=dilation)
        self.conv2 = nn.Conv1d(c_out, c_out, k, dilation=dilation)
        self.drop = nn.Dropout(dropout)
        self.skip = nn.Conv1d(c_in, c_out, 1) if c_in != c_out else nn.Identity()

    def forward(self, x):
        y = self.drop(torch.relu(self.conv1(nn.functional.pad(x, (self.pad, 0)))))
        y = self.drop(torch.relu(self.conv2(nn.functional.pad(y, (self.pad, 0)))))
        return torch.relu(y + self.skip(x))


class TCNNet(nn.Module):
    def __init__(self, n_in: int, n_out: int, channels: int, kernel_size: int, levels: int, dropout: float):
        super().__init__()
        blocks, c = [], n_in
        for i in range(levels):
            blocks.append(CausalBlock(c, channels, kernel_size, 2**i, dropout))
            c = channels
        self.tcn = nn.Sequential(*blocks)
        self.head = nn.Linear(channels, n_out)

    def forward(self, x):                      # x: (batch, steps, channels)
        y = self.tcn(x.transpose(1, 2))         # (batch, channels, steps)
        return self.head(y[:, :, -1])


def pick_device(pref: str) -> torch.device:
    if pref == "cpu" or (pref == "auto" and not torch.cuda.is_available()):
        return torch.device("cpu")
    return torch.device("cuda")


class SequenceRegressor:
    """fit(X, Y) / predict(X) with X: (n, steps, channels), Y: (n, horizons) deltas (NaN = missing)."""

    def __init__(self, kind: str, cfg: dict, seed: int):
        self.kind, self.cfg, self.seed = kind, cfg, seed
        self.device = pick_device(cfg.get("device", "auto"))

    def _net(self, n_in: int, n_out: int) -> nn.Module:
        p = self.cfg[self.kind]
        if self.kind == "lstm":
            return LSTMNet(n_in, n_out, p["hidden_size"], p["num_layers"], p["dropout"])
        if self.kind == "tcn":
            return TCNNet(n_in, n_out, p["channels"], p["kernel_size"], p["levels"], p["dropout"])
        raise ValueError(self.kind)

    def _scale_x(self, X: np.ndarray) -> np.ndarray:
        Xs = (X - self.x_mean_) / self.x_std_
        return np.nan_to_num(Xs, nan=0.0).astype(np.float32)

    def fit(self, X: np.ndarray, Y: np.ndarray, X_val: np.ndarray, Y_val: np.ndarray):
        torch.manual_seed(self.seed)
        np.random.seed(self.seed)
        t = self.cfg["training"]
        flat = X.reshape(-1, X.shape[2])
        self.x_mean_ = np.nanmean(flat, axis=0)
        self.x_std_ = np.nanstd(flat, axis=0)
        self.x_std_[~(self.x_std_ > 1e-8)] = 1.0
        self.y_mean_, self.y_std_ = np.nanmean(Y, axis=0), np.nanstd(Y, axis=0)
        self.y_std_[~(self.y_std_ > 1e-8)] = 1.0

        def tensors(Xa, Ya):
            ys = (Ya - self.y_mean_) / self.y_std_
            return (torch.from_numpy(self._scale_x(Xa)), torch.from_numpy(np.nan_to_num(ys, nan=0).astype(np.float32)),
                    torch.from_numpy((~np.isnan(ys)).astype(np.float32)))

        xtr, ytr, mtr = tensors(X, Y)
        xva, yva, mva = (a.to(self.device) for a in tensors(X_val, Y_val))
        self.net_ = self._net(X.shape[2], Y.shape[1]).to(self.device)
        opt = torch.optim.AdamW(self.net_.parameters(), lr=t["learning_rate"], weight_decay=t["weight_decay"])
        loss_fn = nn.HuberLoss(reduction="none", delta=t["huber_delta"])

        def masked(pred, y, m):
            return (loss_fn(pred, y) * m).sum() / m.sum().clamp(min=1)

        best, best_state, bad = np.inf, None, 0
        g = torch.Generator().manual_seed(self.seed)
        for epoch in range(t["max_epochs"]):
            self.net_.train()
            order = torch.randperm(len(xtr), generator=g)
            for i in range(0, len(order), t["batch_size"]):
                idx = order[i:i + t["batch_size"]]
                xb, yb, mb = xtr[idx].to(self.device), ytr[idx].to(self.device), mtr[idx].to(self.device)
                opt.zero_grad()
                loss = masked(self.net_(xb), yb, mb)
                loss.backward()
                nn.utils.clip_grad_norm_(self.net_.parameters(), 1.0)
                opt.step()
            self.net_.eval()
            with torch.no_grad():
                vl = float(sum(masked(self.net_(xva[i:i + 4096]), yva[i:i + 4096], mva[i:i + 4096]) *
                               mva[i:i + 4096].sum() for i in range(0, len(xva), 4096)) / mva.sum())
            if vl < best - 1e-4:
                best, best_state, bad = vl, copy.deepcopy(self.net_.state_dict()), 0
            else:
                bad += 1
            log.debug("epoch %d inner-val loss %.4f", epoch, vl)
            if bad >= t["patience"]:
                break
        self.net_.load_state_dict(best_state)
        self.epochs_, self.best_val_loss_ = epoch + 1, best
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        self.net_.eval()
        xs = torch.from_numpy(self._scale_x(X))
        out = []
        with torch.no_grad():
            for i in range(0, len(xs), 4096):
                out.append(self.net_(xs[i:i + 4096].to(self.device)).cpu().numpy())
        return np.concatenate(out) * self.y_std_ + self.y_mean_
