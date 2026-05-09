from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from gemini_stock.charts.renderer import ChartRenderer
from gemini_stock.features.snapshot import build_technical_snapshot


def test_simplified_chart_has_png_output(tmp_path):
    start = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
    rows = []
    for i in range(50):
        close = 100 + i * 0.1
        rows.append(
            {
                "timestamp": start + timedelta(minutes=15 * i),
                "open": close - 0.1,
                "high": close + 0.4,
                "low": close - 0.4,
                "close": close,
                "volume": 1000 + i * 5,
            }
        )
    df = pd.DataFrame(rows)
    snapshot = build_technical_snapshot("QQQ", df)

    output = ChartRenderer(tmp_path).render_simplified("QQQ", df, snapshot)

    assert output.suffix == ".png"
    assert Path(output).stat().st_size > 10_000
