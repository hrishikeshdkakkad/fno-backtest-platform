"""Generate matplotlib charts for docs/options-reading-material.md.

Produces 9 PNGs in `docs/options-reading-material-assets/` in a consistent
"clean academic" style:

  * white background, thin light grid, no chartjunk
  * black axes, single steel-blue accent for profit curves
  * terracotta accent for loss regions
  * serif typography sized for iPad reading at ~150 DPI
"""
from __future__ import annotations

import math
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "docs" / "options-reading-material-assets"
ASSETS.mkdir(parents=True, exist_ok=True)

# ─── Style ──────────────────────────────────────────────────────────────────
PROFIT = "#2B5A8A"       # steel blue
LOSS = "#B5562E"         # dark terracotta
AXIS = "#222222"
GRID = "#D8D8D8"
FAINT = "#888888"
FILL_PROFIT = "#2B5A8A15"     # ~8% alpha
FILL_LOSS = "#B5562E18"

mpl.rcParams.update({
    "figure.dpi": 150,
    "savefig.dpi": 150,
    "figure.facecolor": "white",
    "axes.facecolor": "white",
    "axes.edgecolor": AXIS,
    "axes.labelcolor": AXIS,
    "axes.titlesize": 11,
    "axes.titleweight": "regular",
    "axes.labelsize": 10,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "xtick.color": AXIS,
    "ytick.color": AXIS,
    "xtick.labelsize": 9,
    "ytick.labelsize": 9,
    "grid.color": GRID,
    "grid.linestyle": "-",
    "grid.linewidth": 0.6,
    "font.family": "serif",
    "font.serif": ["Georgia", "Times New Roman", "DejaVu Serif"],
    "axes.grid": True,
    "axes.axisbelow": True,
    "legend.frameon": False,
    "legend.fontsize": 9,
})

FIGSIZE_STD = (7.0, 3.8)
FIGSIZE_WIDE = (8.5, 3.8)


def _style_axes(ax):
    ax.axhline(0, color=AXIS, linewidth=0.9)
    ax.tick_params(length=3)


def _save(fig, name: str) -> None:
    path = ASSETS / f"{name}.png"
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight", pad_inches=0.15)
    plt.close(fig)
    print(f"  wrote {path.relative_to(ROOT)}")


# ─── 1. Moneyness number line (Ch 2) ────────────────────────────────────────


def chart_moneyness():
    fig, ax = plt.subplots(figsize=FIGSIZE_WIDE)
    spot = 24_353
    strikes = [23_000, 24_000, 24_350, 24_400, 24_800]
    labels = ["deep OTM", "OTM", "ATM", "ITM", "deep ITM"]

    ax.axhline(0, color=AXIS, linewidth=1.1)
    # Zone shading for a PUT: left of spot = OTM, right = ITM
    ax.axvspan(22_800, spot, alpha=0.05, color=LOSS)
    ax.axvspan(spot, 25_000, alpha=0.05, color=PROFIT)
    # spot line
    ax.axvline(spot, color=AXIS, linestyle="--", linewidth=1.0)
    ax.text(spot, 0.85, f"S = ₹{spot:,}", ha="center", va="bottom",
            fontsize=10, color=AXIS)

    for k, lab in zip(strikes, labels):
        ax.plot(k, 0, "o", markersize=6, color=AXIS, markerfacecolor="white",
                markeredgewidth=1.2)
        ax.text(k, -0.30, f"{k:,}", ha="center", va="top", fontsize=9, color=AXIS)
        ax.text(k, 0.30, lab, ha="center", va="bottom", fontsize=9.5, color=AXIS,
                style="italic")

    ax.set_xlim(22_800, 25_000)
    ax.set_ylim(-1.0, 1.2)
    ax.set_yticks([])
    ax.set_xticks([])
    ax.spines["left"].set_visible(False)
    ax.spines["bottom"].set_visible(False)
    ax.grid(False)
    ax.set_title("Moneyness of PUT options (strikes relative to spot)",
                 fontsize=11)
    ax.text(23_900, -0.75, "OTM zone for puts", ha="center", fontsize=9,
            color=LOSS, style="italic")
    ax.text(24_700, -0.75, "ITM zone for puts", ha="center", fontsize=9,
            color=PROFIT, style="italic")

    _save(fig, "moneyness")


# ─── 2-5. Single-option payoff diagrams (Ch 3) ─────────────────────────────


def _plot_payoff(s, payoff, title, be_x=None, be_label=None, *,
                 max_y=None, min_y=None, annotations=None):
    fig, ax = plt.subplots(figsize=FIGSIZE_STD)
    # Fill profit/loss regions
    ax.fill_between(s, payoff, 0, where=(payoff > 0), color=FILL_PROFIT,
                    interpolate=True)
    ax.fill_between(s, payoff, 0, where=(payoff < 0), color=FILL_LOSS,
                    interpolate=True)
    ax.plot(s, payoff, color=PROFIT, linewidth=2.0)
    if be_x is not None:
        ax.plot(be_x, 0, "o", markersize=7, color=AXIS, markerfacecolor="white",
                markeredgewidth=1.4)
        if be_label:
            ax.annotate(be_label, xy=(be_x, 0), xytext=(be_x, (max_y or 1) * 0.3),
                        ha="center", fontsize=9, color=AXIS,
                        arrowprops=dict(arrowstyle="->", color=AXIS, lw=0.8))
    for xy, text, xytext in annotations or []:
        ax.annotate(text, xy=xy, xytext=xytext, fontsize=9, color=AXIS,
                    ha="center",
                    arrowprops=dict(arrowstyle="->", color=AXIS, lw=0.7))
    ax.set_xlabel("Spot at expiry")
    ax.set_ylabel("P&L per share (₹)")
    ax.set_title(title, fontsize=11)
    if max_y is not None or min_y is not None:
        ax.set_ylim(min_y, max_y)
    _style_axes(ax)
    return fig


def chart_payoff_long_call():
    K, prem = 100, 6
    s = np.linspace(70, 140, 200)
    payoff = np.maximum(s - K, 0) - prem
    fig = _plot_payoff(
        s, payoff, f"Long call (strike {K}, premium {prem})",
        be_x=K + prem, be_label=f"break-even = K + premium = {K+prem}",
        max_y=40, min_y=-10,
        annotations=[
            ((65, -prem), f"max loss = premium paid = {prem}", (80, -8)),
            ((135, 29), "profit grows linearly above K + premium", (115, 35)),
        ],
    )
    _save(fig, "payoff_long_call")


def chart_payoff_long_put():
    K, prem = 100, 6
    s = np.linspace(60, 130, 200)
    payoff = np.maximum(K - s, 0) - prem
    fig = _plot_payoff(
        s, payoff, f"Long put (strike {K}, premium {prem})",
        be_x=K - prem, be_label=f"break-even = K − premium = {K-prem}",
        max_y=40, min_y=-10,
        annotations=[
            ((125, -prem), f"max loss = premium paid = {prem}", (115, -9)),
            ((70, 24), "profit grows as spot falls below K − premium", (85, 35)),
        ],
    )
    _save(fig, "payoff_long_put")


def chart_payoff_short_call():
    K, prem = 100, 6
    s = np.linspace(70, 140, 200)
    payoff = prem - np.maximum(s - K, 0)
    fig = _plot_payoff(
        s, payoff, f"Short call (strike {K}, premium {prem} collected)",
        be_x=K + prem, be_label=f"break-even = K + premium = {K+prem}",
        max_y=10, min_y=-40,
        annotations=[
            ((75, prem), f"max profit = premium collected = {prem}", (82, 8.5)),
            ((135, -29), "loss grows without bound above K + premium", (115, -36)),
        ],
    )
    _save(fig, "payoff_short_call")


def chart_payoff_short_put():
    K, prem = 100, 6
    s = np.linspace(55, 130, 200)
    payoff = prem - np.maximum(K - s, 0)
    fig = _plot_payoff(
        s, payoff, f"Short put (strike {K}, premium {prem} collected)",
        be_x=K - prem, be_label=f"break-even = K − premium = {K-prem}",
        max_y=10, min_y=-45,
        annotations=[
            ((125, prem), f"max profit = premium collected = {prem}", (118, 8.5)),
            ((60, -39), "large loss as spot falls\n(capped at K − premium when S→0)", (82, -42)),
        ],
    )
    _save(fig, "payoff_short_put")


# ─── 6. √T time-value decay (Ch 4) ─────────────────────────────────────────


def chart_sqrt_t_decay():
    fig, ax = plt.subplots(figsize=FIGSIZE_STD)
    dte = np.linspace(90, 0, 200)
    # Time value ∝ √T relative to a reference at T=35 days
    time_value = 100 * np.sqrt(dte / 35.0)
    ax.plot(dte, time_value, color=PROFIT, linewidth=2.0)
    ax.fill_between(dte, time_value, 0, color=FILL_PROFIT)
    # Mark 21-DTE reference line
    ax.axvline(21, color=LOSS, linestyle="--", linewidth=1.2, alpha=0.9)
    ax.text(21, 165, "21 DTE\n(manage line)", ha="center", va="top",
            fontsize=9, color=LOSS)
    ax.annotate("most time value decays in the\nfinal 21 days",
                xy=(10, 80), xytext=(45, 150),
                fontsize=9, color=AXIS, ha="center",
                arrowprops=dict(arrowstyle="->", color=AXIS, lw=0.8))

    ax.set_xlabel("Days to expiry (DTE)")
    ax.set_ylabel("Relative time value (indexed to 100 at 35 DTE)")
    ax.set_title("Time value decay follows √T  — acceleration near expiry",
                 fontsize=11)
    ax.set_xlim(90, 0)    # reverse: countdown to expiry
    ax.set_ylim(0, 180)
    _style_axes(ax)
    _save(fig, "time_decay_sqrt_t")


# ─── 7. IV smile / skew (Ch 5) ─────────────────────────────────────────────


def chart_iv_smile():
    fig, ax = plt.subplots(figsize=FIGSIZE_STD)
    # moneyness: negative = OTM put side, positive = OTM call side
    moneyness = np.linspace(-0.08, 0.05, 120)
    # Skewed smile: left tilt higher
    iv = 16 + 4.0 * (moneyness + 0.015) ** 2 * 300 + 2.0 * np.maximum(
        -moneyness - 0.01, 0) * 60
    # Clip roof
    iv = np.clip(iv, 14, 28)
    ax.plot(moneyness * 100, iv, color=PROFIT, linewidth=2.0)
    # Mark ATM
    ax.axvline(0, color=AXIS, linestyle="--", linewidth=0.9)
    atm_iv = float(np.interp(0, moneyness, iv))
    ax.plot(0, atm_iv, "o", color=AXIS, markersize=6,
            markerfacecolor="white", markeredgewidth=1.3)
    ax.annotate(f"ATM IV ≈ {atm_iv:.1f}%", xy=(0, atm_iv),
                xytext=(2.5, atm_iv + 2.5), fontsize=9, color=AXIS,
                arrowprops=dict(arrowstyle="->", color=AXIS, lw=0.7))

    ax.text(-5.5, 26, "OTM puts demand higher IV\n(left-tilt skew)",
            fontsize=9, color=LOSS, style="italic")
    ax.text(3.0, 14.5, "OTM calls",
            fontsize=9, color=FAINT, style="italic", ha="left")

    ax.set_xlabel("Moneyness (%) — strike vs spot")
    ax.set_ylabel("Implied volatility (%)")
    ax.set_title("Volatility smile / skew (Indian index typical)", fontsize=11)
    ax.set_xlim(-8, 5)
    ax.set_ylim(13, 29)
    _style_axes(ax)
    _save(fig, "iv_smile")


# ─── 8. Theta decay + 21 DTE zone (Ch 10) — same as #6 but annotated ──────


def chart_theta_manage_zone():
    fig, ax = plt.subplots(figsize=FIGSIZE_STD)
    dte = np.linspace(90, 0, 200)
    time_value = 100 * np.sqrt(dte / 35.0)
    ax.plot(dte, time_value, color=PROFIT, linewidth=2.0)
    ax.fill_between(dte, time_value, 0, color=FILL_PROFIT)
    # Shade the gamma-danger zone
    ax.axvspan(0, 21, alpha=0.12, color=LOSS)
    ax.axvline(21, color=LOSS, linestyle="--", linewidth=1.2)
    ax.text(10, 155, "Gamma-danger zone\n(close positions here)",
            ha="center", fontsize=9, color=LOSS, style="italic")
    ax.text(50, 155, "theta collection zone\n(trade is still stable)",
            ha="center", fontsize=9, color=PROFIT, style="italic")

    ax.annotate("entry at 35 DTE",
                xy=(35, 100), xytext=(55, 60),
                fontsize=9, color=AXIS,
                arrowprops=dict(arrowstyle="->", color=AXIS, lw=0.7))
    ax.annotate("manage or close",
                xy=(21, 78), xytext=(40, 30),
                fontsize=9, color=AXIS,
                arrowprops=dict(arrowstyle="->", color=AXIS, lw=0.7))

    ax.set_xlabel("Days to expiry (DTE)")
    ax.set_ylabel("Relative time value")
    ax.set_title("Why the last 21 days dominate both theta AND gamma",
                 fontsize=11)
    ax.set_xlim(90, 0)
    ax.set_ylim(0, 175)
    _style_axes(ax)
    _save(fig, "theta_manage_zone")


# ─── 9. Put credit spread payoff (Ch 12) ───────────────────────────────────


def chart_credit_spread_payoff():
    fig, ax = plt.subplots(figsize=FIGSIZE_STD)
    K_s, K_l = 23_500, 23_400
    credit = 25
    s = np.linspace(23_250, 23_750, 300)
    pnl = np.where(
        s >= K_s, credit,
        np.where(s >= K_l, credit - (K_s - s), credit - (K_s - K_l)),
    )
    ax.plot(s, pnl, color=PROFIT, linewidth=2.0)
    ax.fill_between(s, pnl, 0, where=(pnl > 0), color=FILL_PROFIT,
                    interpolate=True)
    ax.fill_between(s, pnl, 0, where=(pnl < 0), color=FILL_LOSS,
                    interpolate=True)

    # Mark strikes
    for k, label in [(K_l, f"long K_l = {K_l:,}"),
                     (K_s, f"short K_s = {K_s:,}")]:
        ax.axvline(k, color=AXIS, linestyle=":", linewidth=0.9)
        ax.text(k, -80, label, ha="center", va="bottom", fontsize=9,
                color=AXIS, rotation=0)
    # Mark break-even
    be = K_s - credit
    ax.plot(be, 0, "o", color=AXIS, markersize=7, markerfacecolor="white",
            markeredgewidth=1.4)
    ax.annotate(f"break-even\n= K_s − credit\n= {be:,}",
                xy=(be, 0), xytext=(be - 100, 18),
                fontsize=9, color=AXIS, ha="center",
                arrowprops=dict(arrowstyle="->", color=AXIS, lw=0.7))
    # Max profit / loss labels
    ax.annotate(f"max profit = credit = ₹{credit}",
                xy=(23_700, credit), xytext=(23_700, credit + 8),
                fontsize=9, color=AXIS, ha="center")
    ax.annotate(f"max loss = ₹{credit - (K_s - K_l)}",
                xy=(23_320, credit - (K_s - K_l)),
                xytext=(23_320, credit - (K_s - K_l) - 12),
                fontsize=9, color=AXIS, ha="center")

    ax.set_xlabel("Spot at expiry (₹)")
    ax.set_ylabel("P&L per share (₹)")
    ax.set_title(f"Put credit spread — short {K_s:,} / long {K_l:,}, "
                 f"credit ₹{credit}", fontsize=11)
    ax.set_ylim(-110, 45)
    _style_axes(ax)
    _save(fig, "put_credit_spread_payoff")


# ─── Main ───────────────────────────────────────────────────────────────────


def main() -> None:
    print(f"Writing charts to {ASSETS.relative_to(ROOT)}/")
    chart_moneyness()
    chart_payoff_long_call()
    chart_payoff_long_put()
    chart_payoff_short_call()
    chart_payoff_short_put()
    chart_sqrt_t_decay()
    chart_iv_smile()
    chart_theta_manage_zone()
    chart_credit_spread_payoff()
    print("done.")


if __name__ == "__main__":
    main()
