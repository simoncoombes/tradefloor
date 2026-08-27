"""Re-execute every example notebook and write its outputs back.

The era-boundary checklist has said "re-execute every notebook and re-sync
its prose" through three boundaries and there has never been a tool for it.
Committed notebook outputs are a market: at an era boundary every one of them
shows a market that no longer exists, and `test_the_committed_notebook_
carries_its_output` will happily pass on stale numbers because it checks that
output EXISTS, not that it is current.

Deliberately separate from the test suite. `test_the_notebook_executes_
without_error` says in as many words that it "never writes back: a test that
rewrote the committed output would hide the drift it exists to find". That is
right, and it is why re-execution has to be an explicit act by someone who
means it.

    python tools/docs/reexecute_notebooks.py            # all of them
    python tools/docs/reexecute_notebooks.py --check    # report drift, write nothing
    python tools/docs/reexecute_notebooks.py 04 09      # just these

`--check` is the one to run before a release: it reports which notebooks
would change without changing them, so "the notebooks are stale" becomes a
number rather than an assumption.
"""
from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
EXAMPLES = ROOT / "examples"


def outputs_of(nb) -> list:
    """Just the output payloads, for comparing before against after."""
    return [copy.deepcopy(c.get("outputs", [])) for c in nb.cells
            if c.cell_type == "code"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("only", nargs="*",
                    help="substrings; default is every notebook")
    ap.add_argument("--check", action="store_true",
                    help="report which notebooks would change, write nothing")
    ap.add_argument("--timeout", type=int, default=900)
    args = ap.parse_args()

    try:
        import nbformat
        from nbclient import NotebookClient
    except ImportError as e:
        sys.exit(f"needs nbformat and nbclient: {e}")

    paths = sorted(EXAMPLES.glob("*.ipynb"))
    if args.only:
        paths = [p for p in paths if any(s in p.name for s in args.only)]
    if not paths:
        sys.exit("no notebooks matched")

    changed, failed = [], []
    for path in paths:
        nb = nbformat.read(path, as_version=4)
        before = outputs_of(nb)
        print(f"{path.name} ... ", end="", flush=True)
        try:
            NotebookClient(
                nb, timeout=args.timeout, kernel_name="python3",
                resources={"metadata": {"path": str(EXAMPLES)}},
            ).execute()
        except Exception as e:
            failed.append((path.name, str(e).strip().splitlines()[-1][:120]))
            print("FAILED")
            continue
        after = outputs_of(nb)
        drifted = before != after
        if drifted:
            changed.append(path.name)
        if drifted and not args.check:
            nbformat.write(nb, path)
        print("changed" if drifted else "unchanged",
              "(not written)" if args.check and drifted else "")

    print(f"\n{len(paths)} notebooks: {len(changed)} changed, "
          f"{len(failed)} failed")
    for name in changed:
        print(f"  changed: {name}")
    for name, err in failed:
        print(f"  FAILED : {name}: {err}")
    if changed and args.check:
        print("\nRun without --check to write them back, then re-sync the "
              "PROSE that quotes their numbers -- which this tool cannot do.")
    # A failure is an error; drift under --check is a report, not an error.
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
