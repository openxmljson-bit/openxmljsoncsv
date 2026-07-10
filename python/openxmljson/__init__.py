"""OPENXMLJSON — view, navigate and search very large JSON/XML/CSV files.

The heavy lifting lives in the native Rust engine, exposed as
``openxmljson._native`` (built by maturin from crates/oxj-py). This package
is the Qt application shell; it only ever requests the handful of tree rows
on screen, so Python is never on the hot path (SPEC §2.3).
"""

__version__ = "0.1.0"

try:
    from openxmljson._native import (  # noqa: F401
        Document,
        LazyDocument,
        node_kind_names,
    )

    NATIVE_AVAILABLE = True
except ImportError:  # pragma: no cover - build-time condition
    NATIVE_AVAILABLE = False
    Document = None  # type: ignore[assignment]
    LazyDocument = None  # type: ignore[assignment]

    def node_kind_names():  # type: ignore[misc]
        raise ImportError(
            "openxmljson._native is not built. Run: "
            "pip install maturin && maturin develop --release"
        )
