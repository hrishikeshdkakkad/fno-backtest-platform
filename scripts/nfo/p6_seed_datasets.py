"""P6 one-shot: ingest current features + trade_universe into data/nfo/datasets/."""
from nfo.config import DATA_DIR, RESULTS_DIR
from nfo.datasets.features import ingest_features_parquet
from nfo.datasets.trade_universe import ingest_trade_universe_csv


def main() -> int:
    ds_root = DATA_DIR / "datasets"

    features_manifest = ingest_features_parquet(
        parquet_path=RESULTS_DIR / "historical_signals.parquet",
        dataset_id="historical_features_2024-01_2026-04",
        datasets_root=ds_root,
    )
    print(f"features: {features_manifest.dataset_id} sha256={features_manifest.parquet_sha256[:12]}")

    trades_paths = [RESULTS_DIR / "spread_trades.csv"]
    gaps = RESULTS_DIR / "spread_trades_v3_gaps.csv"
    if gaps.exists():
        trades_paths.append(gaps)
    trades_manifest = ingest_trade_universe_csv(
        csv_paths=trades_paths,
        dataset_id="trade_universe_nifty_2024-01_2026-04",
        datasets_root=ds_root,
    )
    print(f"trade_universe: {trades_manifest.dataset_id} sha256={trades_manifest.parquet_sha256[:12]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
