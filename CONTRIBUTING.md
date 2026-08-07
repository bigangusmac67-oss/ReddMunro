# Contributing

## Sign-off is required

Every commit must carry a `Signed-off-by` line:

```bash
git commit -s -m "your message"
```

That line is the [Developer Certificate of Origin](https://developercertificate.org/)
— you are stating that you wrote the change, or have the right to submit
it, and that it may be distributed under this project's licence.

**Why this is enforced from the first commit.** The project is Apache
2.0 today and may later be dual-licensed, so that organisations whose
legal teams cannot accept Apache can buy a commercial licence instead.
That option exists only while the copyright is cleanly held. One merged
contribution without a sign-off makes relicensing require tracking down
its author for permission, and it is the kind of thing nobody notices
until years later. Asking for one line up front is cheaper than the
alternative, and it is not a judgement about any contribution.

Pull requests without sign-off will be asked for one before merge.

## The unusual part: predictions come first

This project's central claim is that predictions were written down
**before** the data was fetched, and scored afterwards including the
misses. That is only worth anything if the ordering is real, so:

**If a change alters what a check reports, or adds a detector, register
what you expect before you measure it.** Open the issue or the PR with
the prediction, and a stopping rule — what result would mean the change
should be abandoned. Then run it. A prediction written after the numbers
are known is not a prediction, and a stopping rule that moves when it
fires is not a stopping rule. `MI_SCALING_PREREG.md` has a worked
example, including the part where the rule fired and the change was
dropped.

**Negative controls before the positive case.** Three of four rejected
`derived_aggregates` designs looked right on planted data and failed
only on data with no relation in it. Show the detector staying silent
before showing it firing.

**A miss is a result.** Failed predictions are recorded, not deleted.
Several of the most useful findings in this repository are our own bugs.

## Practical

- `python test_signal_audit.py` must pass. It is a plain script, not
  pytest; exit code 0 means green.
- `python build_demo.py` after touching the engine — `demo/` is build
  output and CI checks it has not drifted.
- The engine depends on NumPy and nothing else. That constraint is what
  lets it run in the browser under Pyodide; please do not add a
  dependency without raising it first.
- **The browser is not a deployment target, it is the product.** An
  int64 index that was free on x86-64 raised `TypeError` on wasm32 and
  broke every audit on the live site while 134 tests passed. `ci/` runs
  the engine under real Pyodide for exactly this reason.

## What the tool must never do

It reports mathematical states. It does not assert a cause it cannot
distinguish, and it does not tell anyone something is safe to delete.
It can prove a metric carries no variation the others already carry;
whether that metric is the sole condition on a paging rule is not in a
CSV of values. Copy or output that blurs this is a bug, and there are
tests that fail on specific phrases.
