# V3 Controlled Experiment

## Thesis

`V3` is worth running as a **small, controlled live experiment**, not as a
validated standalone production strategy.

The case for running it is narrow:

- recent out-of-sample behavior was good
- `first_fire` does not appear to be the main source of error
- the strategy is sparse enough that live evidence is still useful

The case against overconfidence is stronger:

- the NIFTY-only sample is thin
- trade flow is regime-dependent and lumpy
- broader-history losing clusters exist
- one large losing cycle can offset many average winners

So the correct posture is: **trade it small, trade it mechanically, and treat
the next live cycles as evidence collection**.

## Frozen Rules

- Underlying: `NIFTY` only
- Strategy spec: `configs/nfo/strategies/v3_frozen.yaml`
- Structure: `0.30Δ` short put, fixed `100`-point width, target `35 DTE`
- Gate:
  - `IV - RV >= -2`
  - `trend_score >= 2`
  - no `RBI`, `FOMC`, or `Budget` event inside `10` days
  - at least one vol condition:
    - `VIX > 20`
    - or `VIX 3-mo percentile >= 80%`
    - or `IV rank >= 60%`
- Selection: one trade per cycle
- Exit: `HTE` only

## Entry Policy

Use **`first_fire` only**.

If `first_fire` is not constructible under the frozen rules, **skip the cycle**.
Do not switch to:

- later fire-days
- manual strike selection
- PT50
- discretionary exits

Any of those changes create a different strategy and destroy parity with the
research evidence.

## Position Sizing

- Start with **1 lot only**
- Hold at 1 lot for at least **6 closed cycles or 12 months**, whichever is
  longer
- Budget every cycle as if a current-lot losing event of roughly `₹7k` is
  possible

This strategy does not fire often enough to justify aggressive scaling.

## Data-Driven View

Source artifacts:

- `results/nfo/audits/expansion_decision_2026-04-21.md`
- `results/nfo/audits/walkforward_v3_2026-04-21.md`
- `results/nfo/audits/multi_fire_cycle_audit_2026-04-22.md`

Key numbers:

- `25` distinct fire-cycles over `5.68` years: about `4.4` cycles/year
- OOS walk-forward: `11` unique trades, all positive by P&L sign
- OOS multi-fire audit:
  - `8 / 8` first-fire entries profitable
  - `34 / 36` tradable fire-days profitable
- Full-union multi-fire audit:
  - `14 / 17` tradable first-fire entries profitable
  - `62 / 80` tradable fire-days profitable
  - but average losses were much larger than average wins

Important implication:

- recent sample EV looks positive
- broader-history EV is not stable enough to trust

This is why V3 is a controlled experiment, not a proven edge.

## Original Thoughts

The core issue is **not** that V3 picks obviously bad entry days. The audit
evidence suggests the opposite: within the cycles it chooses, entry timing was
often reasonable, especially in the recent OOS window.

The real issue is **surface area**. NIFTY-only monthly expiries do not produce
enough independent opportunities to establish strong statistical confidence.
That makes V3 unsuitable as a conviction strategy, but still potentially useful
as a disciplined sleeve while the rest of the strategy book is being built.

In other words:

- V3 may be a decent filter
- V3 is not yet a reliable business by itself

## Live Operating Rules

- Enter only when the engine says V3 fired and the spread is constructible
- Record before entry:
  - fire date
  - expiry
  - strikes
  - net credit
  - lot size
  - planned max loss
- Record after exit:
  - realized P&L
  - outcome
  - any execution drift vs engine intent

## Hard Stop Rules

Pause the experiment immediately if any of these happen:

- `2` losing cycles within the first `6` closed cycles
- one realized loss materially worse than the predeclared risk envelope
- live strike selection or fills drift materially from engine-selected intent
- you start making discretionary overrides

If any hard stop triggers, the sample is no longer clean. Stop and re-evaluate
before taking another trade.

## Decision Standard

Continue only if all of these remain true:

- live behavior matches research logic
- execution is operationally clean
- losses stay inside the planned envelope
- the experiment is adding real evidence rather than just consuming attention

This experiment is successful if it gives you a cleaner answer to one question:
**does V3 deserve a place in the eventual strategy book?**
