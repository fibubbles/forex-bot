"""List multi-day holes in the Dukascopy D1 files (normal weekends/holidays excluded)."""
import pandas as pd

from scripts.research_momentum_multi import PAIRS, load

for pair in PAIRS:
    try:
        s = load(pair)
    except (pd.errors.EmptyDataError, FileNotFoundError):
        print(f"{pair}: EMPTY OR MISSING FILE")
        continue
    gaps = s.index.to_series().diff()
    holes = gaps[gaps > pd.Timedelta(days=5)]
    print(f"{pair}: rows {len(s)} | {s.index[0].date()} -> {s.index[-1].date()} | holes {len(holes)}")
    for end, g in holes.items():
        print(f"    {(end - g).date()} -> {end.date()}  ({g.days} days)")