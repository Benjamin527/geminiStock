from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import mplfinance as mpf
import pandas as pd

from gemini_stock.features.indicators import enrich_indicators
from gemini_stock.schemas import TechnicalSnapshot


class ChartRenderer:
    def __init__(self, output_dir: str | Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def render(self, symbol: str, candles_15m: pd.DataFrame, snapshot: TechnicalSnapshot) -> Path:
        df = candles_15m.copy()
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        df = df.sort_values("timestamp").tail(96)
        enriched = enrich_indicators(df)
        plot_df = enriched.set_index("timestamp").rename(
            columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}
        )

        addplots = [
            mpf.make_addplot(enriched.set_index("timestamp")["vwap"], color="#0f766e", width=1),
            mpf.make_addplot(enriched.set_index("timestamp")["ema_20"], color="#2563eb", width=1),
            mpf.make_addplot(enriched.set_index("timestamp")["ema_50"], color="#dc2626", width=1),
            mpf.make_addplot(enriched.set_index("timestamp")["rsi_14"], panel=1, color="#7c3aed", ylabel="RSI"),
            mpf.make_addplot(enriched.set_index("timestamp")["macd"], panel=2, color="#2563eb", ylabel="MACD"),
            mpf.make_addplot(enriched.set_index("timestamp")["macd_signal"], panel=2, color="#dc2626"),
            mpf.make_addplot(enriched.set_index("timestamp")["macd_histogram"], panel=2, type="bar", color="#9ca3af"),
        ]

        horizontal_lines = list(snapshot.support_levels) + list(snapshot.resistance_levels)
        output = self.output_dir / f"{symbol}_{snapshot.timestamp_utc.strftime('%Y%m%dT%H%M%SZ')}.png"
        mpf.plot(
            plot_df,
            type="candle",
            style="yahoo",
            volume=True,
            addplot=addplots,
            hlines=dict(hlines=horizontal_lines, colors=["#16a34a"] * len(snapshot.support_levels) + ["#ef4444"] * len(snapshot.resistance_levels), linestyle="--", linewidths=0.8),
            title=f"{symbol} 15m AI Watch Snapshot",
            panel_ratios=(4, 1, 1),
            figscale=1.1,
            savefig=dict(fname=output, dpi=140, bbox_inches="tight"),
        )
        return output

    def render_simplified(self, symbol: str, candles_15m: pd.DataFrame, snapshot: TechnicalSnapshot) -> Path:
        df = candles_15m.copy()
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        df = df.sort_values("timestamp").tail(32)
        enriched = enrich_indicators(df)
        indexed = enriched.set_index("timestamp")
        plot_df = indexed.rename(
            columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}
        )
        addplots = [
            mpf.make_addplot(indexed["vwap"], color="#0f766e", width=1),
            mpf.make_addplot(indexed["ema_20"], color="#2563eb", width=1),
            mpf.make_addplot(indexed["ema_50"], color="#dc2626", width=1),
        ]
        horizontal_lines = list(snapshot.support_levels) + list(snapshot.resistance_levels)
        output = self.output_dir / f"{symbol}_{snapshot.timestamp_utc.strftime('%Y%m%dT%H%M%SZ')}_review.png"
        mpf.plot(
            plot_df,
            type="candle",
            style="yahoo",
            volume=True,
            addplot=addplots,
            hlines=dict(
                hlines=horizontal_lines,
                colors=["#16a34a"] * len(snapshot.support_levels) + ["#ef4444"] * len(snapshot.resistance_levels),
                linestyle="--",
                linewidths=0.8,
            ),
            title=f"{symbol} 15m Review",
            figsize=(10, 7),
            savefig=dict(fname=output, dpi=100, bbox_inches="tight", facecolor="white"),
        )
        return output
