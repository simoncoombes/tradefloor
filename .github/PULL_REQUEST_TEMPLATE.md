<!--
Titles say what changed, plainly. `CONTENT.md` holds the register and the
budgets; the short version is that a title is a noun phrase or a plain
statement, a commit message stays abstract because this repository is public,
and the specifics belong in this body, where a reviewer can use them.
-->

## What changed



## How it was checked

<!--
Both suites, and the one that is easy to forget:

    maturin develop --release   # first: a stale build reports phantom drift
    python -m pytest tests/ -q
    cd rust && cargo test

    python tools/release/check.py
-->

## Does the market move

<!--
Delete whichever is untrue. A trajectory change is breaking however small it
looks, and arrives as a NEW preset rather than an edit to an existing one.
-->

- [ ] No trajectory change: the known-answer digest is unchanged
- [ ] Trajectory changes, and `KAT_VERSION` is bumped with the baseline
      regenerated
