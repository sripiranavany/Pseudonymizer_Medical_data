"""
Stage 1 batch evaluation: PII extraction + pseudonymization quality.

Sends each document through /pseudonymize and aggregates:
  - PII detection: precision, recall, F1 vs ground truth
  - Context leakage: how many documents had PII survive into the output
  - Irreversibility: counts of HIGH / MEDIUM / LOW risk entities

Usage:
    python evaluate.py --api http://localhost:8001/pseudonymize \\
                       --docs ../data/documents \\
                       --truth ../data/ground_truth \\
                       --output stage1_results.json
"""

import argparse
import json
import sys
import time
from pathlib import Path

import requests


# ── Helpers ────────────────────────────────────────────────────────────────────

def load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_ground_truth(path: Path) -> list:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("pii_entities", [])


def send(api_url: str, document_text: str, document_id: str,
         ground_truth: list, retries: int = 3) -> dict:
    payload = {
        "document_id":   document_id,
        "document_text": document_text,
        "ground_truth":  ground_truth,
    }
    for attempt in range(retries):
        try:
            resp = requests.post(api_url, json=payload, timeout=180)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            if attempt == retries - 1:
                raise
            print(f"  Retry {attempt + 1}/{retries}: {exc}")
            time.sleep(5)


# ── Per-document printer ───────────────────────────────────────────────────────

def print_result(doc_id: str, result: dict) -> None:
    det  = result.get("pii_detection_eval", {})
    leak = result.get("context_leakage", {})
    rev  = result.get("irreversibility", {})

    print(f"\n  [{doc_id}]")

    # Detection quality
    if det.get("precision") is not None:
        print(f"    PII detected:       {det.get('detected_count')}  "
              f"(GT: {det.get('ground_truth_count')})")
        print(f"    True Positives:     {det.get('true_positives')}")
        print(f"    False Positives:    {det.get('false_positives')}"
              + (f"  → {det.get('fp_entities')}" if det.get("fp_entities") else ""))
        print(f"    False Negatives:    {det.get('false_negatives')}"
              + (f"  → {det.get('fn_entities')}" if det.get("fn_entities") else ""))
        print(f"    Precision:          {det.get('precision')}%")
        print(f"    Recall:             {det.get('recall')}%")
        print(f"    F1:                 {det.get('f1_score')}%")
    else:
        print(f"    PII detected:       {det.get('detected_count')}  (no ground truth)")

    # Context leakage
    verdict = leak.get("verdict", "?")
    leaked  = leak.get("leaked_entities", [])
    print(f"    Context leakage:    {verdict}"
          + (f"  → {leaked}" if leaked else ""))

    # Irreversibility
    print(f"    Irreversibility:    {rev.get('verdict', '?')}  "
          f"(HIGH:{rev.get('high_risk_count',0)}  "
          f"MED:{rev.get('medium_risk_count',0)}  "
          f"LOW:{rev.get('low_risk_count',0)})")
    if rev.get("high_risk_entities"):
        print(f"      ↳ HIGH-risk:      {rev['high_risk_entities']}")
    if rev.get("medium_risk_entities"):
        print(f"      ↳ MEDIUM-risk:    {rev['medium_risk_entities']}")


# ── Aggregate metrics ──────────────────────────────────────────────────────────

def aggregate(results: list[dict]) -> dict:
    docs_with_gt = [r for r in results if r.get("pii_detection_eval", {}).get("precision") is not None]

    total_tp  = sum(r["pii_detection_eval"]["true_positives"]  for r in docs_with_gt)
    total_fp  = sum(r["pii_detection_eval"]["false_positives"] for r in docs_with_gt)
    total_fn  = sum(r["pii_detection_eval"]["false_negatives"] for r in docs_with_gt)

    precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 1.0
    recall    = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 1.0
    f1        = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    leaky_docs   = [r for r in results if not r.get("context_leakage", {}).get("leakage_free", True)]
    total_leaked = sum(r.get("context_leakage", {}).get("leaked_count", 0) for r in results)

    total_high   = sum(r.get("irreversibility", {}).get("high_risk_count",   0) for r in results)
    total_medium = sum(r.get("irreversibility", {}).get("medium_risk_count", 0) for r in results)
    total_low    = sum(r.get("irreversibility", {}).get("low_risk_count",    0) for r in results)

    return {
        "documents_evaluated":         len(results),
        "documents_with_ground_truth": len(docs_with_gt),
        # Detection
        "aggregate_true_positives":    total_tp,
        "aggregate_false_positives":   total_fp,
        "aggregate_false_negatives":   total_fn,
        "precision":                   round(precision * 100, 2),
        "recall":                      round(recall * 100, 2),
        "f1_score":                    round(f1 * 100, 2),
        # Leakage
        "documents_with_leakage":      len(leaky_docs),
        "total_leaked_entities":       total_leaked,
        "leakage_rate_pct":            round(len(leaky_docs) / len(results) * 100, 1) if results else 0,
        # Irreversibility
        "total_high_risk_entities":    total_high,
        "total_medium_risk_entities":  total_medium,
        "total_low_risk_entities":     total_low,
    }


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Batch evaluate Stage 1: pseudonymization quality"
    )
    parser.add_argument("--api",    default="http://localhost:8001/pseudonymize",
                        help="URL of the /pseudonymize endpoint")
    parser.add_argument("--docs",   default="../data/documents",
                        help="Directory with .txt medical documents")
    parser.add_argument("--truth",  default="../data/ground_truth",
                        help="Directory with ground-truth .json files")
    parser.add_argument("--output", default="stage1_results.json",
                        help="Output file for full results")
    parser.add_argument("--delay",  type=float, default=1.0,
                        help="Seconds between requests")
    args = parser.parse_args()

    docs_dir  = Path(args.docs)
    truth_dir = Path(args.truth)

    if not docs_dir.exists():
        print(f"Error: documents directory not found: {docs_dir}", file=sys.stderr)
        sys.exit(1)

    doc_files = sorted(docs_dir.glob("*.txt"))
    if not doc_files:
        print(f"Error: no .txt files in {docs_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Stage 1 Evaluation — Pseudonymization Quality")
    print(f"API:       {args.api}")
    print(f"Documents: {len(doc_files)}")
    print("=" * 60)

    all_results: list[dict] = []
    failed: list[dict] = []

    for doc_path in doc_files:
        doc_id     = doc_path.stem
        truth_path = truth_dir / f"{doc_id}.json"
        gt         = load_ground_truth(truth_path) if truth_path.exists() else []

        if not truth_path.exists():
            print(f"\nProcessing: {doc_id}  (no ground truth)")
        else:
            print(f"\nProcessing: {doc_id}  (GT: {len(gt)} entities)")

        try:
            text   = load_text(doc_path)
            result = send(args.api, text, doc_id, gt)
            print_result(doc_id, result)
            all_results.append(result)
        except Exception as exc:
            print(f"  FAILED: {exc}")
            failed.append({"document_id": doc_id, "error": str(exc)})

        if args.delay > 0:
            time.sleep(args.delay)

    # ── Aggregate ─────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("AGGREGATE — STAGE 1 RESULTS")
    print("=" * 60)

    if all_results:
        agg = aggregate(all_results)

        print("\n  PII Detection Quality (vs ground truth)")
        print(f"    Documents with GT:     {agg['documents_with_ground_truth']} / {agg['documents_evaluated']}")
        print(f"    True Positives:        {agg['aggregate_true_positives']}")
        print(f"    False Positives:       {agg['aggregate_false_positives']}")
        print(f"    False Negatives:       {agg['aggregate_false_negatives']}")
        print(f"    Precision:             {agg['precision']}%")
        print(f"    Recall:                {agg['recall']}%")
        print(f"    F1:                    {agg['f1_score']}%")

        print("\n  Context Leakage")
        print(f"    Documents with leak:   {agg['documents_with_leakage']} / {agg['documents_evaluated']}  ({agg['leakage_rate_pct']}%)")
        print(f"    Total leaked entities: {agg['total_leaked_entities']}")

        print("\n  Irreversibility Risk")
        print(f"    HIGH-risk entities:    {agg['total_high_risk_entities']}")
        print(f"    MEDIUM-risk entities:  {agg['total_medium_risk_entities']}")
        print(f"    LOW-risk entities:     {agg['total_low_risk_entities']}")
    else:
        print("  No successful results.")

    if failed:
        print(f"\n  Failed ({len(failed)}): {[f['document_id'] for f in failed]}")

    # ── Save ──────────────────────────────────────────────────────────────────
    output = {
        "aggregate":    aggregate(all_results) if all_results else {},
        "per_document": all_results,
        "failed":       failed,
    }
    out_path = Path(args.output)
    out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nFull results saved to: {out_path.resolve()}")


if __name__ == "__main__":
    main()
