"""
Import smoke test for data_algo/*.py.

Walks every top-level .py in this directory (excluding _attic and itself),
attempts to import it via importlib, and reports pass/fail with reason.
Returns nonzero exit if any module fails for non-dependency reasons
(SyntaxError, NameError, AttributeError, IndentationError) — those are
real code health issues. Missing third-party deps (ModuleNotFoundError
that does not name a sibling .py module) are reported but do not fail
the run; declaring those is Iter-3's job.

Usage:
    python3 _smoke_imports.py [--strict]

--strict: any failure (including ModuleNotFoundError) exits nonzero.
"""
import argparse
import importlib.util
import sys
from pathlib import Path

CODE_HEALTH_ERRORS = (
    SyntaxError,
    NameError,
    AttributeError,
    IndentationError,
    TabError,
)


def _sibling_module_names(here: Path) -> set[str]:
    return {p.stem for p in here.glob("*.py") if not p.stem.startswith("_")}


def smoke(here: Path, strict: bool) -> int:
    sys.path.insert(0, str(here))
    siblings = _sibling_module_names(here)
    files = sorted(p for p in here.glob("*.py") if not p.stem.startswith("_"))

    ok: list[str] = []
    code_health_fails: list[tuple[str, str, str]] = []
    dep_fails: list[tuple[str, str, str]] = []
    other_fails: list[tuple[str, str, str]] = []

    for py in files:
        modname = py.stem
        spec = importlib.util.spec_from_file_location(modname, py)
        mod = importlib.util.module_from_spec(spec)
        # Register in sys.modules BEFORE exec_module — Python's @dataclass
        # decorator (since 3.10+, hits hard in 3.13) does
        # `sys.modules.get(cls.__module__).__dict__` during class processing,
        # which raises AttributeError on NoneType if the module isn't
        # registered yet. Same applies to typing.get_type_hints() and any
        # code path that walks __module__ → sys.modules.
        sys.modules[modname] = mod
        try:
            spec.loader.exec_module(mod)
            ok.append(modname)
        except CODE_HEALTH_ERRORS as e:
            code_health_fails.append((modname, type(e).__name__, str(e)[:200]))
        except ModuleNotFoundError as e:
            missing = e.name.split(".")[0] if e.name else "?"
            kind = "ModuleNotFoundError"
            if missing in siblings:
                code_health_fails.append((modname, kind, str(e)[:200]))
            else:
                dep_fails.append((modname, kind, str(e)[:200]))
        except Exception as e:
            other_fails.append((modname, type(e).__name__, str(e)[:200]))
        finally:
            # On failure, clean up the partial registration so later imports
            # of the same name don't see a half-initialized module.
            if modname not in ok and modname in sys.modules and sys.modules[modname] is mod:
                del sys.modules[modname]

    total = len(files)
    print(f"=== smoke_imports: {len(ok)}/{total} OK ===")
    if code_health_fails:
        print(f"\n--- {len(code_health_fails)} code-health failures (block CI) ---")
        for name, kind, msg in code_health_fails:
            print(f"  {name}: {kind}: {msg}")
    if dep_fails:
        print(f"\n--- {len(dep_fails)} missing-third-party-dep failures ---")
        for name, kind, msg in dep_fails:
            print(f"  {name}: {kind}: {msg}")
    if other_fails:
        print(f"\n--- {len(other_fails)} other runtime errors ---")
        for name, kind, msg in other_fails:
            print(f"  {name}: {kind}: {msg}")

    if code_health_fails or other_fails:
        return 1
    if strict and dep_fails:
        return 2
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat missing third-party deps as failures.",
    )
    args = parser.parse_args()
    here = Path(__file__).parent.resolve()
    return smoke(here, strict=args.strict)


if __name__ == "__main__":
    sys.exit(main())
