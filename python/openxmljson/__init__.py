"""OPENXMLJSON — view, navigate and search very large JSON/XML/CSV files.

The heavy lifting lives in the native Rust engine, exposed as
``openxmljson._native`` (built by maturin from crates/oxj-py). This package
is the Qt application shell; it only ever requests the handful of tree rows
on screen, so Python is never on the hot path (SPEC §2.3).
"""

__version__ = None

try:
    from openxmljson._native import (  # noqa: F401
        Document,
        LazyDocument,
        node_kind_names,
    )

    NATIVE_AVAILABLE = True
    # Primary version source: baked into the native module at compile time from
    # the Cargo version (CI stamps it from the git tag). The native module is
    # always bundled, so this is reliable in the packaged app — unlike Python
    # package metadata, which PyInstaller does not bundle by default.
    try:
        from openxmljson._native import __version__ as _native_version
        __version__ = _native_version
    except Exception:
        __version__ = None
except ImportError:  # pragma: no cover - build-time condition
    NATIVE_AVAILABLE = False
    Document = None  # type: ignore[assignment]
    LazyDocument = None  # type: ignore[assignment]

    def node_kind_names():  # type: ignore[misc]
        raise ImportError(
            "openxmljson._native is not built. Run: "
            "pip install maturin && maturin develop --release"
        )

# Fallbacks if the native module didn't provide a version: installed package
# metadata, then a dev marker for an unbuilt source tree.
if not __version__:
    try:
        from importlib.metadata import (
            PackageNotFoundError, version as _pkg_version,
        )
        try:
            __version__ = _pkg_version("openxmljson")
        except PackageNotFoundError:
            __version__ = "0.0.0+dev"
    except Exception:
        __version__ = "0.0.0+dev"
