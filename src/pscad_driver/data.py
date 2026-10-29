"""Selected-channel numeric access. Statistics use original samples; previews are explicit."""
from itertools import zip_longest
from pathlib import Path
import math

from .reader import read_inf, channel_names, csv_headers


def _selection(base, channels):
    base = str(base)
    if base.endswith(".inf"):
        base = base[:-4]
    metadata = read_inf(base + ".inf")
    names = channel_names(metadata)
    if len(set(names)) != len(names):
        names = ["%s#%s" % (n, m["pgb"]) for n, m in zip(names, metadata)]
    wanted = channels or names
    if not wanted:
        raise ValueError("No channels in the output")
    unknown = set(wanted) - set(names)
    if unknown:
        raise KeyError("Unknown channels: %s; available: %s" % (sorted(unknown), names))
    chosen = {name: (i // 10 + 1, i % 10 + 1) for i, name in enumerate(names) if name in wanted}
    units = {name: item["units"] for name, item in zip(names, metadata) if name in chosen}
    return base, chosen, units


def _rows(base, chosen, window):
    """Read only necessary numbered files. A window is [start, end), in seconds."""
    blocks = sorted({b for b, _ in chosen.values()})
    files = [open("%s_%02d.out" % (base, b), encoding="utf-8") for b in blocks]
    try:
        streams = [(line.split() for line in f if line.strip()) for f in files]
        previous = None
        for rowset in zip_longest(*streams):
            if any(row is None for row in rowset):
                raise ValueError("Output blocks have different row counts")
            times = [float(row[0]) for row in rowset]
            t = times[0]
            if any(other != t for other in times) or not math.isfinite(t) or (previous is not None and t <= previous):
                raise ValueError("Output time axes do not match or are not increasing")
            previous = t
            if window[0] is not None and t < window[0]:
                continue
            if window[1] is not None and t >= window[1]:
                break
            values = {name: float(rowset[blocks.index(block)][col]) for name, (block, col) in chosen.items()}
            if not all(math.isfinite(value) for value in values.values()):
                raise ValueError("Nonfinite output value at t=%s" % t)
            yield t, values
    finally:
        for f in files:
            f.close()


def query(base, channels=None, window=None, mode="stats", max_points=2000):
    """Modes: stats (all samples), raw (bounded), envelope (retains bin extrema)."""
    if mode not in ("stats", "raw", "envelope"):
        raise ValueError("mode must be stats, raw or envelope")
    if not isinstance(max_points, int) or max_points < 4:
        raise ValueError("max_points must be an integer >= 4")
    window = list(window or [None, None])
    if len(window) != 2 or (None not in window and window[0] >= window[1]):
        raise ValueError("window must be [start, end), with start < end")
    base, chosen, units = _selection(base, channels)
    stats = {name: {"min": math.inf, "max": -math.inf, "sum": 0.0, "sum_sq": 0.0} for name in chosen}
    count, first, last = 0, None, None
    raw = []
    for t, values in _rows(base, chosen, window):
        first = t if first is None else first
        last = t
        count += 1
        for name, value in values.items():
            row = stats[name]
            row["min"], row["max"] = min(row["min"], value), max(row["max"], value)
            row["sum"] += value
            row["sum_sq"] += value * value
        if mode == "raw":
            if count > max_points:
                raise ValueError("Raw window exceeds max_points; narrow the window, use envelope, or export the full data")
            raw.append([t, *[values[name] for name in chosen]])
    if not count:
        raise ValueError("No samples in the requested window")
    for name, row in stats.items():
        row["mean"] = row.pop("sum") / count
        row["rms"] = math.sqrt(row.pop("sum_sq") / count)
        row["abs_peak"] = max(abs(row["min"]), abs(row["max"]))
        row["units"] = units[name]
    result = {"base": base, "mode": mode, "window_semantics": "start <= t < end",
              "samples": count, "t_range": [first, last], "statistics": stats,
              "statistics_source": "all original samples in the window"}
    if mode == "raw":
        result.update(columns=["TIME", *chosen], points=raw, decimated=False)
    elif mode == "envelope":
        # A second streaming pass avoids retaining the entire waveform in memory.
        bins = max(1, (max_points - 2) // 2)
        candidates = {name: {} for name in chosen}
        endpoints = {name: [] for name in chosen}
        for i, (t, values) in enumerate(_rows(base, chosen, window)):
            key = min(bins - 1, i * bins // count)
            for name, value in values.items():
                point = (t, value)
                bucket = candidates[name].setdefault(key, [point, point])
                if value < bucket[0][1]:
                    bucket[0] = point
                if value > bucket[1][1]:
                    bucket[1] = point
                if i in (0, count - 1):
                    endpoints[name].append(point)
        result["traces"] = {name: sorted(set(endpoints[name] + [p for pair in groups.values() for p in pair]))
                            for name, groups in candidates.items()}
        result["decimated"] = True
        result["preview_method"] = "time-ordered min/max envelope; use raw data for timing, phase and spectral calculations"
    return result


def export(base, path, channels=None, window=None):
    import csv
    base, chosen, units = _selection(base, channels)
    count = 0
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(csv_headers(["TIME", *chosen]))
        for t, values in _rows(base, chosen, window or [None, None]):
            writer.writerow([t, *[values[name] for name in chosen]])
            count += 1
    return {"path": str(Path(path).resolve()), "samples": count, "units": units}


def main():
    import argparse
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base")
    parser.add_argument("--channels", nargs="+")
    parser.add_argument("--start", type=float)
    parser.add_argument("--end", type=float)
    parser.add_argument("--mode", choices=["stats", "raw", "envelope"], default="stats")
    parser.add_argument("--max-points", type=int, default=2000)
    parser.add_argument("--export")
    a = parser.parse_args()
    result = (export(a.base, a.export, a.channels, [a.start, a.end]) if a.export else
              query(a.base, a.channels, [a.start, a.end], a.mode, a.max_points))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
