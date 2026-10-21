from __future__ import annotations

from functools import wraps
from pathlib import Path
from threading import RLock
from typing import Any, Callable, TypeVar, cast

from .control import canonical_json


_Result = TypeVar("_Result")


def memoize_root_policy_compile(
    function: Callable[[Path, dict[str, Any]], _Result],
) -> Callable[[Path, dict[str, Any]], _Result]:
    """Memoize a deterministic compile graph within one immutable checkout.

    The CP59+ contracts recursively rebuild the same canonical parents. That
    changes a linear validation chain into exponential work and can exceed the
    Actions timeout. Compile results are frozen dataclasses, so reusing an exact
    root/policy result preserves every validation and fail-closed decision from
    the first evaluation.

    The cache is process-local. Call ``cache_clear`` before recompiling after
    mutating checkout files in the same Python process.
    """

    cache: dict[tuple[str, str], _Result] = {}
    lock = RLock()

    @wraps(function)
    def wrapped(root: Path, policy: dict[str, Any]) -> _Result:
        key = (str(Path(root).resolve()), canonical_json(policy))
        with lock:
            cached = cache.get(key)
        if cached is not None:
            return cached

        result = function(Path(root), policy)
        with lock:
            return cache.setdefault(key, result)

    def cache_clear() -> None:
        with lock:
            cache.clear()

    wrapped.cache_clear = cache_clear  # type: ignore[attr-defined]
    return cast(Callable[[Path, dict[str, Any]], _Result], wrapped)
