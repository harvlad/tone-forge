"""lab CLI.  Run from backend/:  python3 -m lab <command> ...

Local-first: `benchmark` runs inference on this machine only for cache
misses.  Remote spend always goes through pending-gpu -> estimate-gpu ->
export-gpu-job -> (user runs machine) -> import-results.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import config


def _manifest():
    from . import corpus
    return corpus.load_manifest()


def _tier_stems(args):
    from . import tiers
    return tiers.select_tier(_manifest(), args.tier,
                             inst_class=getattr(args, "inst_class", None),
                             seed=getattr(args, "seed", config.DEFAULT_TIER_SEED))


# -- commands ---------------------------------------------------------------

def cmd_corpus(args):
    from . import corpus
    if args.action == "build":
        df = corpus.build_manifest()
        print(f"manifest: {len(df)} stems -> {config.MANIFEST_PATH}")
    df = _manifest()
    print(f"\ncorpus manifest ({len(df)} stems)")
    print(df.groupby(["dataset", "split"]).size().to_string())
    print("\nby GM class (validation split):")
    dev = df[df["split"] == "validation"]
    print(dev.groupby("inst_class_gm").size().sort_values(ascending=False).to_string())


def cmd_models(args):
    from . import models_registry as mr
    mr.seed_defaults()
    if args.action == "add":
        fields = json.loads(args.fields or "{}")
        entry = mr.upsert(args.model_id, **fields)
        print(json.dumps(entry, indent=1))
        return
    for m in mr.list_models():
        print(f"{m['model_id']:<16} {m.get('status', '?'):<22} "
              f"license={m.get('license', '?'):<14} {m.get('display_name', '')}")


def cmd_cache(args):
    from . import cache
    df = cache.status()
    print(df.to_string(index=False) if len(df) else "cache empty")


def cmd_benchmark(args):
    from . import analysis, experiments, reporting, runner
    stems = _tier_stems(args)
    print(f"tier '{args.tier}' class={args.inst_class or 'all'}: {len(stems)} stems")
    start = time.time()
    n_run = 0
    if not args.no_infer:
        missing = runner.missing_stems(args.model, stems)
        print(f"cache: {len(stems) - len(missing)} hit, {len(missing)} missing")
        for i, (_, row) in enumerate(missing.iterrows()):
            print(f"  [{i+1}/{len(missing)}] inferring {row['stem_id']}", flush=True)
            runner.get_predictions(args.model, row)
            n_run += 1
    result = analysis.evaluate_model(args.model, stems,
                                     onset_tolerance=args.tolerance)
    reporting.print_eval(args.model, args.tier, args.inst_class, result)
    hint = reporting.early_termination_hint(result, reporting.incumbent_recall(),
                                            args.tier)
    if hint:
        print(f"\n>>> {hint}")
    exp_id = experiments.record(
        "benchmark", models=[args.model], dataset="slakh2100",
        split="test" if args.tier == "heldout" else "validation",
        tier=args.tier, inst_class=args.inst_class, n_stems=len(stems),
        eval_config={"onset_tolerance": args.tolerance,
                     "seed": args.seed, "inferred_now": n_run},
        result_summary={"global": result["global"],
                        "per_class": result["per_class"],
                        "n_missing": len(result["missing"])},
        runtime_seconds=time.time() - start, device="local")
    print(f"\nexperiment recorded: {exp_id}")
    print(f"decision options: {reporting.decision_options()}")


def cmd_compare(args):
    from . import analysis, reporting
    stems = _tier_stems(args)
    for model in (args.model_a, args.model_b):
        result = analysis.evaluate_model(model, stems, onset_tolerance=args.tolerance)
        reporting.print_eval(model, args.tier, args.inst_class, result)


def cmd_oracle(args):
    from . import analysis, experiments
    stems = _tier_stems(args)
    r = analysis.head_to_head(args.model_a, args.model_b, stems,
                              onset_tolerance=args.tolerance)
    g = r["global"]
    print(f"\n== ORACLE {args.model_a} + {args.model_b} | tier={args.tier} ==")
    print(f"stems missing predictions: {len(r['missing'])}")
    if g:
        print(f"both={g.get('both_correct', 0)}  {args.model_a}_only={g.get('a_only', 0)}  "
              f"{args.model_b}_only={g.get('b_only', 0)}  both_wrong={g.get('both_wrong', 0)}")
        if "oracle_recall" in g:
            print(f"oracle recall: {g['oracle_recall']:.3f}  "
                  f"({args.model_b} unique: {g.get('b_unique_contribution', 0)*100:.1f}pp)")
    for cls, d in r["per_class"].items():
        if "oracle_recall" in d:
            print(f"  {cls:<22} oracle={d['oracle_recall']:.3f} "
                  f"a_only={d.get('a_only', 0)} b_only={d.get('b_only', 0)}")
    experiments.record("oracle", models=[args.model_a, args.model_b],
                       tier=args.tier, inst_class=args.inst_class,
                       n_stems=len(stems),
                       eval_config={"onset_tolerance": args.tolerance},
                       result_summary={"global": {k: v for k, v in g.items()}})


def cmd_validate(args):
    from . import validate as v
    ok_all = True
    for model in (args.models or ["basic_pitch", "mt3"]):
        result = v.validate_model(model, onset_tolerance=0.1)
        ok = v.print_validation(model, result)
        if result["n_missing"] == 0 and not ok:
            ok_all = False
    sys.exit(0 if ok_all else 1)


def cmd_pending_gpu(args):
    from .remote import jobs
    if args.add:
        stems = _tier_stems(args)
        entry = jobs.queue_add(args.add, stems, tier=args.tier,
                               inst_class=args.inst_class)
        print(f"queued: {entry['model_id']} {entry.get('inst_class') or 'all'} "
              f"{len(entry['stem_ids'])} missing stems "
              f"({entry['audio_seconds']/60:.1f} audio min)")
        return
    q = jobs.queue_load()
    if not q:
        print("no pending GPU work")
        return
    print("PENDING GPU WORK")
    for e in q:
        print(f"  {e['model_id']:<16} {e.get('inst_class') or 'all':<12} "
              f"tier={e['tier']:<10} {len(e['stem_ids'])} stems  "
              f"{e['audio_seconds']/60:.1f} audio min")


def cmd_estimate_gpu(args):
    from .remote import jobs
    stems = _tier_stems(args)
    est = jobs.estimate(args.model, stems, gpu_name=args.gpu,
                        hourly_usd=args.price,
                        throughput_audio_sec_per_sec=args.throughput)
    print("\n== GPU PREFLIGHT ==")
    for k, v in est.items():
        print(f"  {k}: {v}")
    print("\nRemote execution requires explicit user approval. Nothing has "
          "been provisioned.")


def cmd_export_gpu_job(args):
    from .remote import jobs
    stems = _tier_stems(args)
    bundle = jobs.export_job(args.model, stems)
    job = json.loads((bundle / "job.json").read_text())
    print(f"bundle: {bundle}")
    print(f"  {job['n_stems']} missing stems, model={job['model_id']}")
    print("  next: rsync to GPU box, run lab_worker/worker.py, rsync results/ "
          "back, then: lab import-results " + str(bundle))


def cmd_import_results(args):
    from . import experiments
    from .remote import jobs
    r = jobs.import_results(Path(args.bundle))
    print(f"job {r['job_id']}: imported {len(r['imported'])}, "
          f"skipped {len(r['skipped'])}, failed {len(r['failed'])}")
    for f in r["failed"]:
        print(f"  FAILED {f['stem_id']}: {f['error'][:120]}")
    experiments.record("import_results", models=[r["model_id"]],
                       n_stems=len(r["imported"]),
                       result_summary={"imported": len(r["imported"]),
                                       "skipped": len(r["skipped"]),
                                       "failed": len(r["failed"])},
                       notes=f"bundle={args.bundle}",
                       provenance=f"remote_job:{r['job_id']}")


def cmd_experiments(args):
    from . import experiments
    recs = experiments.load_all()
    for r in recs[-args.limit:]:
        g = (r.get("result_summary") or {}).get("global", {})
        extra = (f" recall={g['recall']:.3f} f1={g['f1']:.3f}"
                 if isinstance(g, dict) and "recall" in g else "")
        print(f"{r['experiment_id']}  {r['kind']:<14} {','.join(r['models']):<24} "
              f"tier={r.get('tier', ''):<12} n={r.get('n_stems', 0):<5}{extra}")


def cmd_tiers(args):
    stems = _tier_stems(args)
    print(f"tier={args.tier} class={args.inst_class or 'all'} seed={args.seed}: "
          f"{len(stems)} stems")
    print(stems.groupby("inst_class_gm").size().to_string())
    if args.verbose:
        for sid in stems["stem_id"]:
            print(" ", sid)


def cmd_query(args):
    from .db import connect
    con = connect()
    con.sql(args.sql).show(max_rows=args.limit)


def cmd_import_legacy(args):
    from . import legacy_import
    legacy_import.run(dry_run=args.dry_run)


# -- parser -----------------------------------------------------------------

def _add_tier_args(p, default_tier="scout"):
    p.add_argument("--tier", default=default_tier,
                   help=f"{config.TIER_ORDER} or validated100")
    p.add_argument("--class", dest="inst_class", default=None,
                   help='GM class, e.g. "Bass", "Synth Lead"')
    p.add_argument("--seed", type=int, default=config.DEFAULT_TIER_SEED)


def _add_eval_args(p):
    p.add_argument("--tolerance", type=float, default=0.1,
                   help="onset tolerance seconds (default 0.1 = validated)")


def build_parser():
    ap = argparse.ArgumentParser(prog="lab", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("corpus", help="build/inspect the corpus manifest")
    p.add_argument("action", choices=["build", "status"], nargs="?", default="status")
    p.set_defaults(fn=cmd_corpus)

    p = sub.add_parser("models", help="model registry")
    p.add_argument("action", choices=["list", "add"], nargs="?", default="list")
    p.add_argument("model_id", nargs="?")
    p.add_argument("--fields", help="JSON dict of registry fields")
    p.set_defaults(fn=cmd_models)

    p = sub.add_parser("cache", help="prediction cache status")
    p.add_argument("action", choices=["status"], nargs="?", default="status")
    p.set_defaults(fn=cmd_cache)

    p = sub.add_parser("benchmark", help="cache-first benchmark (local inference for misses)")
    p.add_argument("model")
    _add_tier_args(p)
    _add_eval_args(p)
    p.add_argument("--no-infer", action="store_true",
                   help="analysis only; never run the model")
    p.set_defaults(fn=cmd_benchmark)

    p = sub.add_parser("compare", help="side-by-side metrics from cache (no inference)")
    p.add_argument("model_a")
    p.add_argument("model_b")
    _add_tier_args(p)
    _add_eval_args(p)
    p.set_defaults(fn=cmd_compare)

    p = sub.add_parser("oracle", help="per-note ensemble oracle from cache (no inference)")
    p.add_argument("model_a")
    p.add_argument("model_b")
    _add_tier_args(p, default_tier="validated100")
    _add_eval_args(p)
    p.set_defaults(fn=cmd_oracle)

    p = sub.add_parser("validate", help="reproduce validated benchmark numbers from cache")
    p.add_argument("models", nargs="*")
    p.set_defaults(fn=cmd_validate)

    p = sub.add_parser("pending-gpu", help="inspect/add pending GPU work (no provisioning)")
    p.add_argument("--add", metavar="MODEL")
    _add_tier_args(p)
    p.set_defaults(fn=cmd_pending_gpu)

    p = sub.add_parser("estimate-gpu", help="preflight cost estimate for a remote run")
    p.add_argument("model")
    _add_tier_args(p)
    p.add_argument("--gpu", default="RTX 4090")
    p.add_argument("--price", type=float, default=0.40, help="USD per hour")
    p.add_argument("--throughput", type=float, default=None,
                   help="audio seconds processed per wall second")
    p.set_defaults(fn=cmd_estimate_gpu)

    p = sub.add_parser("export-gpu-job", help="bundle ONLY missing predictions for remote run")
    p.add_argument("model")
    _add_tier_args(p)
    p.set_defaults(fn=cmd_export_gpu_job)

    p = sub.add_parser("import-results", help="validate + import a bundle's results into cache")
    p.add_argument("bundle")
    p.set_defaults(fn=cmd_import_results)

    p = sub.add_parser("experiments", help="experiment registry")
    p.add_argument("action", choices=["list"], nargs="?", default="list")
    p.add_argument("--limit", type=int, default=30)
    p.set_defaults(fn=cmd_experiments)

    p = sub.add_parser("tiers", help="show deterministic tier selection")
    _add_tier_args(p)
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(fn=cmd_tiers)

    p = sub.add_parser("query", help="DuckDB SQL over corpus/gt/predictions/matches")
    p.add_argument("sql")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(fn=cmd_query)

    p = sub.add_parser("import-legacy", help="rescue/import legacy experiment artifacts")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_import_legacy)

    return ap


def main(argv=None):
    config.ensure_dirs()
    args = build_parser().parse_args(argv)
    args.fn(args)
