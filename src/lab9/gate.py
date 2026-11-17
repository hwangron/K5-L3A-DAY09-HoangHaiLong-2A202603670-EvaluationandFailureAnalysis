"""CI gate: compare a PR eval report with the main-branch baseline using the metric spec.

    python -m lab9.gate --pr reports/eval_workflow.json --baseline reports/baseline_v1.json

exit 0 = merge, exit 1 = blocked. Safety metrics: max 0, no averaging away.
Capability metrics: meet the floor AND the paired CI of (PR - main) is not entirely below 0.
Thresholds may only get stricter on the same eval-set version (ratchet).
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from lab8.console import utf8_stdio
from lab9 import stats
from lab9.eval import SPEC, aggregate, load_spec, per_item
from lab9.graders import GRADERS


def integrity_check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> list[str]:
    """M7 (Expert): refuse to compare reports that cannot be trusted. Return a list of problems
    (empty = trustworthy). A gate that passes a broken report is worse than no gate.
    Must catch at least:
      - any metric value in pr["metrics"] that is NaN or inf
      - len(pr["rows"]) != manifest items x len(manifest seeds)   (dropped / duplicated rows)
      - the (item_id, seed) set of PR differs from the baseline's (cherry-picked seeds/items)
      - pr["evaluator_failures"] non-empty, or a row whose value for a graded spec metric is
        None (a crashed grader silently scores as "missing" and drops out of the mean)
      - base_spec given and a threshold in spec is LOOSER than in base_spec for the same
        eval-set version (ratchet: max may not rise, min may not fall, a blocking metric may
        not become non-blocking, a metric may not disappear; the same for spec["slices"],
        including dropping no_significant_drop)
      - an item's slice tags in PR rows differ from the baseline's (relabelling an item out of a
        slice hides its regression from the slice gate)"""
    problems: list[str] = []
    rows = pr.get("rows") or []
    man = pr.get("manifest") or {}
    graded = [m["id"] for m in spec.get("metrics", []) if m["id"] in GRADERS]

    # 1. metric values must be finite numbers (NaN < min is False: a naive gate lets it through)
    for k, v in (pr.get("metrics") or {}).items():
        if isinstance(v, float) and not math.isfinite(v):
            problems.append(f"metric {k} is {v} (not a finite number)")

    # 2. graders must have run cleanly on every row
    if pr.get("evaluator_failures"):
        problems.append(f"{len(pr['evaluator_failures'])} evaluator failure(s) in the PR run, "
                        f"first: {str(pr['evaluator_failures'][0])[:80]}")
    for r in rows:
        bad = [m for m in graded if not isinstance(r.get(m), bool)]
        if bad:
            problems.append(f"row {r.get('item_id')}@{r.get('seed')}: no boolean score for {bad} "
                            "(a crashed or dropped grader must not fall out of the mean)")
            break

    # 3. coverage: every (item, seed) exactly once, and the same set as the baseline
    keys = [(r.get("item_id"), r.get("seed")) for r in rows]
    want_n = (man.get("items") or 0) * len(man.get("seeds") or [])
    if len(rows) != want_n:
        problems.append(f"{len(rows)} rows, manifest says {man.get('items')} items x "
                        f"{len(man.get('seeds') or [])} seeds = {want_n}")
    dups = len(keys) - len(set(keys))
    if dups:
        problems.append(f"{dups} duplicated (item, seed) row(s)")
    base_keys = {(r.get("item_id"), r.get("seed")) for r in base.get("rows") or []}
    if set(keys) != base_keys:
        problems.append(f"(item, seed) set differs from the baseline: {len(set(keys) - base_keys)} extra, "
                        f"{len(base_keys - set(keys))} missing (cherry-picked items or seeds?)")
    bman = base.get("manifest") or {}
    if man.get("items") != bman.get("items") or sorted(man.get("seeds") or []) != sorted(bman.get("seeds") or []):
        problems.append(f"manifest items/seeds {man.get('items')}/{man.get('seeds')} differ from the baseline's "
                        f"{bman.get('items')}/{bman.get('seeds')}")
    if spec.get("seeds") and sorted(man.get("seeds") or []) != sorted(spec["seeds"]):
        problems.append(f"PR seeds {man.get('seeds')} are not the spec seeds {spec['seeds']}")

    # 4. the headline numbers must be what the rows say (no hand-edited metrics)
    if not problems and rows:
        try:
            ids = [m for m in graded if m in rows[0]]
            has_cost = all("cost" in r for r in rows)
            again = aggregate([r if has_cost else {**r, "cost": 0.0} for r in rows], ids,
                              k_values=spec.get("pass_k", []))
            if not has_cost:
                again.pop("cost_per_success", None)
            for k, v in again.items():
                got = (pr.get("metrics") or {}).get(k)
                if isinstance(v, (int, float)) and k in (pr.get("metrics") or {}) and \
                        (not isinstance(got, (int, float)) or abs(float(got) - float(v)) > 1e-6):
                    problems.append(f"metric {k}={got} but the rows give {v}")
        except (KeyError, TypeError, ValueError) as e:
            problems.append(f"rows cannot be re-aggregated: {e!r}")

    # 5. slice tags of an item must match the baseline's (moving an item out of a slice hides it)
    base_tags = {r.get("item_id"): sorted(r.get("slices") or []) for r in base.get("rows") or []}
    moved = sorted({r.get("item_id") for r in rows if r.get("item_id") in base_tags
                    and sorted(r.get("slices") or []) != base_tags[r.get("item_id")]})
    if moved:
        problems.append(f"slice tags differ from the baseline for {moved[:5]}")

    # 6. ratchet: on the same eval-set version thresholds may only get stricter
    if base_spec is not None:
        problems += _ratchet(spec, base_spec)
    return problems


def _looser(th: dict, old: dict) -> list[str]:
    out = []
    for k, ov in old.items():
        nv = th.get(k)
        if nv is None:
            out.append(f"{k} removed")
        elif k in ("max", "max_increase") and nv > ov:
            out.append(f"{k} {ov} -> {nv}")
        elif k == "min" and nv < ov:
            out.append(f"{k} {ov} -> {nv}")
        elif k == "no_significant_drop" and ov and not nv:
            out.append("no_significant_drop dropped")
    return out


def _ratchet(spec: dict, base_spec: dict) -> list[str]:
    if spec.get("eval_set_version") != base_spec.get("eval_set_version"):
        return []                                  # new eval-set version: thresholds start over
    problems = []
    new = {m["id"]: m for m in spec.get("metrics", [])}
    for m in base_spec.get("metrics", []):
        n = new.get(m["id"])
        if n is None:
            problems.append(f"RATCHET metric {m['id']} removed")
            continue
        if m.get("blocking") and not n.get("blocking"):
            problems.append(f"RATCHET metric {m['id']} no longer blocking")
        problems += [f"RATCHET metric {m['id']}: {x}" for x in _looser(n.get("threshold") or {}, m.get("threshold") or {})]
    new_sl = {(s["name"], s["metric"]): s for s in spec.get("slices") or []}
    for s in base_spec.get("slices") or []:
        n = new_sl.get((s["name"], s["metric"]))
        if n is None:
            problems.append(f"RATCHET slice {s['name']}/{s['metric']} removed")
            continue
        if s.get("blocking") and not n.get("blocking"):
            problems.append(f"RATCHET slice {s['name']}/{s['metric']} no longer blocking")
        keys = ("min", "max", "no_significant_drop")
        problems += [f"RATCHET slice {s['name']}/{s['metric']}: {x}"
                     for x in _looser({k: n[k] for k in keys if k in n}, {k: s[k] for k in keys if k in s})]
    onf, nnf = base_spec.get("new_failures"), spec.get("new_failures")
    if onf:
        if not nnf:
            problems.append("RATCHET new_failures check removed")
        else:
            if nnf.get("max", 0) > onf.get("max", 0):
                problems.append(f"RATCHET new_failures max {onf.get('max', 0)} -> {nnf.get('max', 0)}")
            if onf.get("blocking") and not nnf.get("blocking"):
                problems.append("RATCHET new_failures no longer blocking")
            gone = set(onf.get("metrics") or []) - set(nnf.get("metrics") or [])
            if gone:
                problems.append(f"RATCHET new_failures metrics removed {sorted(gone)}")
    if sorted(spec.get("seeds") or []) != sorted(base_spec.get("seeds") or []):
        problems.append(f"RATCHET seeds changed {base_spec.get('seeds')} -> {spec.get('seeds')}")
    ob, nb = base_spec.get("bootstrap") or {}, spec.get("bootstrap") or {}
    if nb.get("confidence", 0.95) < ob.get("confidence", 0.95):
        problems.append("RATCHET bootstrap confidence lowered")
    for k in ("eval_set", "labels", "adversarial_pack"):
        if spec.get(k) != base_spec.get(k):
            problems.append(f"RATCHET {k} changed on the same eval-set version")
    return problems


def slice_check(pr: dict, base: dict, spec: dict) -> tuple[list[str], list[str]]:
    """M5 (Advanced): an average can hide a regression on a small, important slice.
    For each entry of spec["slices"] ({"name", "metric", "min"?, "no_significant_drop"?, "blocking"}):
      - value = mean of per_item(pr, metric, slice_name=name)
      - fail if below "min", or if no_significant_drop and the paired CI over the slice's items
        lies entirely below 0
    Return (blocked ids like "slice:money_movement/pass_rate", report lines)."""
    blocked, lines = [], []
    bs = spec.get("bootstrap", {})
    for sl in spec.get("slices") or []:
        name, metric = sl["name"], sl["metric"]
        sid = f"slice:{name}/{metric}"
        a, b = per_item(pr, metric, slice_name=name), per_item(base, metric, slice_name=name)
        verdict, note, val = "ok", "", None
        if not a:
            verdict = "missing"                        # a slice with no rows cannot be shown safe
        else:
            val = round(sum(a.values()) / len(a), 4)
            if "min" in sl and val < sl["min"]:
                verdict = "fail"
            if "max" in sl and val > sl["max"]:
                verdict = "fail"
            keys = sorted(set(a) & set(b))
            if sl.get("no_significant_drop") and keys:
                d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                                   n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027))
                note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
                if hi < 0:
                    verdict = "fail"
        if verdict != "ok" and sl.get("blocking"):
            blocked.append(sid)
        th = {k: sl[k] for k in ("min", "max", "no_significant_drop") if k in sl}
        label = {"ok": "ok", "missing": "MISSING"}.get(verdict, "BLOCK" if sl.get("blocking") else "warn")
        lines.append(f"  {sid:<38} {val!s:<8} n={len(a):<3} {json.dumps(th):<36} {note:<28} {label}")
    return blocked, lines


def new_failures_check(pr: dict, base: dict, spec: dict) -> tuple[list[str], list[str]]:
    """An average can stay flat while a PR fixes some rows and breaks others (a rare 1-in-5-seed bug
    hiding behind a fix). spec["new_failures"] = {"metrics": [...], "max": N, "blocking": bool}:
    count (item, seed) rows that pass every listed metric on the baseline but fail one on the PR.
    On MockLLM the A/A noise is 0 rows, so max = 0; with a real LLM set max from the A/A run."""
    cfg = spec.get("new_failures")
    if not cfg:
        return [], []
    mets = list(cfg.get("metrics") or [])
    ok = lambda r: all(r.get(m) is True for m in mets)
    base_ok = {(r["item_id"], r["seed"]) for r in base.get("rows") or [] if ok(r)}
    broke = sorted((r["item_id"], r["seed"]) for r in pr.get("rows") or []
                   if (r["item_id"], r["seed"]) in base_ok and not ok(r))
    limit = cfg.get("max", 0)
    bad = len(broke) > limit
    label = ("BLOCK" if cfg.get("blocking") else "warn") if bad else "ok"
    shown = ", ".join(f"{i}@{s}" for i, s in broke[:6]) + (" …" if len(broke) > 6 else "")
    line = f"  {'new_failures':<22} {len(broke):<8} {json.dumps({'max': limit}):<40} {shown:<28} {label}"
    return (["new_failures"] if bad and cfg.get("blocking") else []), [line]


def check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> tuple[list[str], list[str]]:
    if pr["manifest"]["eval_set_sha"] != base["manifest"]["eval_set_sha"]:
        return ["eval_set_sha"], ["  EVAL SET differs from the baseline's: re-run the baseline on the same eval set"]
    problems = integrity_check(pr, base, spec, base_spec)
    if problems:
        return ["integrity"], [f"  INTEGRITY {p}" for p in problems]
    blocked, lines = [], []
    bs = spec.get("bootstrap", {})
    for m in spec["metrics"]:
        mid, th, val = m["id"], m.get("threshold", {}), pr["metrics"].get(m["id"])
        verdict, note = "ok", ""
        if val is None:
            verdict = "missing"
        elif "max" in th and val > th["max"]:
            verdict = "fail"
        elif "min" in th and val < th["min"]:
            verdict = "fail"
        elif "max_increase" in th and base["metrics"].get(mid):
            if val > base["metrics"][mid] * (1 + th["max_increase"]):
                verdict = "fail"
        if th.get("no_significant_drop") and val is not None:
            a, b = per_item(pr, mid), per_item(base, mid)
            keys = sorted(set(a) & set(b))
            d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                               n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027))
            note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
            if hi < 0:
                verdict = "fail"
        if verdict != "ok" and m.get("blocking"):
            blocked.append(mid)
        label = {"ok": "ok", "missing": "MISSING"}.get(verdict, "BLOCK" if m.get("blocking") else "warn")
        lines.append(f"  {mid:<22} {val!s:<8} {json.dumps(th):<40} {note:<28} {label}")
    if spec.get("slices"):
        sb, sl = slice_check(pr, base, spec)
        blocked += sb
        lines += sl
    nb, nl = new_failures_check(pr, base, spec)
    blocked += nb
    lines += nl
    return blocked, lines


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(prog="lab9.gate")
    ap.add_argument("--pr", required=True)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--spec", default=str(SPEC))
    ap.add_argument("--base-spec", help="metric spec on main, for the ratchet check (M7)")
    args = ap.parse_args(argv)
    pr = json.loads(Path(args.pr).read_text(encoding="utf-8"))
    base = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    base_spec = load_spec(Path(args.base_spec)) if args.base_spec else None
    blocked, lines = check(pr, base, load_spec(Path(args.spec)), base_spec)
    print(f"gate: PR {pr['run_id']} vs main {base['run_id']} · eval set {pr['manifest']['eval_set_sha']}")
    print("\n".join(lines))
    if blocked:
        print(f"exit 1 — blocked: {', '.join(blocked)}")
        return 1
    print("exit 0 — merge")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
