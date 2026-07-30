"""Compare engine open/index cost for the SAME delimited data presented as
`.csv` (extension-detected) vs `.txt` (format override) — to prove whether the
two paths really differ, or whether a perceived difference is just file size.

Usage:
    python validators/bench_open.py                 # synthetic 200k-row sample
    python validators/bench_open.py /path/to/file.txt   # your real file

Prints, for each variant: open ms, prime ms, root child_count ms + value, and
the time to materialize the first 1000 display children (what the tree's first
paint effectively does).
"""

import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))

from openxmljson import _native  # noqa: E402

Document = _native.Document
LazyDocument = getattr(_native, "LazyDocument", None)

HEADER = "VEHICLE_ID|DELETED|YEAR|MAKE|MODEL|ENGINE|CODE|TYPE"
ROW = ("{i}|0|1960|Fairthorpe|Electrina|"
       "1.0L 998CC L4 Carb VIN: 99H|{c}|3")


def make_sample(rows: int, suffix: str) -> str:
    fh = tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False,
                                     encoding="utf-8")
    fh.write(HEADER + "\n")
    for i in range(rows):
        fh.write(ROW.format(i=507447 + i * 3, c=1489572 + i) + "\n")
    fh.close()
    return fh.name


def ms(t0) -> float:
    return (time.perf_counter() - t0) * 1000.0


def bench(path: str, fmt, lazy: bool, label: str) -> None:
    t0 = time.perf_counter()
    try:
        if lazy and LazyDocument is not None:
            doc = (LazyDocument.open(path, fmt) if fmt
                   else LazyDocument.open(path))
        else:
            doc = Document.open(path, fmt) if fmt else Document.open(path)
    except TypeError:
        print(f"{label:<28} SKIPPED (native module lacks the format arg — "
              f"run `maturin develop --release`)")
        return
    except Exception as exc:
        print(f"{label:<28} FAILED: {exc}")
        return
    t_open = ms(t0)

    t_prime = 0.0
    if lazy and hasattr(doc, "prime"):
        t1 = time.perf_counter()
        try:
            doc.prime()
        except Exception:
            pass
        t_prime = ms(t1)

    root = doc.root()
    t2 = time.perf_counter()
    try:
        n = doc.child_count(root)
    except Exception as exc:
        n = f"err {exc}"
    t_count = ms(t2)

    t3 = time.perf_counter()
    try:
        kids = doc.child_nodes(root)[:1000]
        for k in kids:
            doc.display_text(k)
    except Exception:
        kids = []
    t_first = ms(t3)

    print(f"{label:<28} fmt={doc.format_name():<4} open={t_open:8.1f}ms "
          f"prime={t_prime:8.1f}ms child_count={t_count:8.1f}ms (n={n}) "
          f"first1000={t_first:8.1f}ms")


def main() -> None:
    args = sys.argv[1:]
    tmp_made = []
    if args:
        src = args[0]
        base = os.path.splitext(src)[0]
        as_txt = src
        as_csv = base + ".bench.csv"
        shutil.copyfile(src, as_csv)
        tmp_made.append(as_csv)
        print(f"file: {src}  ({os.path.getsize(src) / 1e6:.1f} MB)\n")
    else:
        rows = 200_000
        as_txt = make_sample(rows, ".txt")
        as_csv = make_sample(rows, ".csv")
        tmp_made += [as_txt, as_csv]
        print(f"synthetic sample: {rows:,} rows "
              f"({os.path.getsize(as_txt) / 1e6:.1f} MB)\n")

    for lazy in (False, True):
        tag = "lazy" if lazy else "eager"
        bench(as_csv, None, lazy, f".csv  (detected)   [{tag}]")
        bench(as_txt, "csv", lazy, f".txt  (override)   [{tag}]")
        print()

    for p in tmp_made:
        try:
            os.unlink(p)
        except OSError:
            pass


if __name__ == "__main__":
    main()
