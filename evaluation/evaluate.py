"""
Batch evaluation script for the AI Pseudonymizer pipeline.

Usage:
    python evaluate.py --api http://localhost:5678/webhook/pseudonymize
                       --docs ../data/documents
                       --truth ../data/ground_truth
                       --output results.json

Sends each document through the pipeline and computes aggregate
precision, recall, and F1 for PII restoration across all documents.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests


PROMPT_TYPE_MAP = {
    "clinical_summary": ["clinical_summary_1", "clinical_summary_2", "clinical_summary_3"],
    "diagnosis_support": ["diagnosis_support_1", "diagnosis_support_2", "diagnosis_support_3"],
    "icd_coding": ["icd_coding_1", "icd_coding_2", "icd_coding_3"],
}


def load_document(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_ground_truth(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def determine_prompt_type(doc_id: str) -> str:
    for prompt_type, ids in PROMPT_TYPE_MAP.items():
        if doc_id in ids:
            return prompt_type
    return "clinical_summary"


def send_to_pipeline(api_url: str, document_text: str, prompt_type: str,
                     document_id: str, ground_truth_pii: list, retries: int = 3) -> dict:
    payload = {
        "document_text": document_text,
        "prompt_type": prompt_type,
        "document_id": document_id,
        "ground_truth_pii": ground_truth_pii,
    }
    for attempt in range(retries):
        try:
            resp = requests.post(api_url, json=payload, timeout=180)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            if attempt == retries - 1:
                raise
            print(f"  Retry {attempt + 1}/{retries} after error: {e}")
            time.sleep(5)


def compute_aggregate_metrics(results: list[dict]) -> dict:
    total_tp = sum(r["evaluation"]["true_positives"] for r in results)
    total_fn = sum(r["evaluation"]["false_negatives"] for r in results)
    total_fp = sum(r["evaluation"]["residual_fakes_in_output"] for r in results)
    total_pii = sum(r["evaluation"]["total_pii_mapped"] for r in results)
    total_appeared = sum(r["evaluation"]["appeared_in_llm_output"] for r in results)

    precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 1.0
    recall = total_tp / total_appeared if total_appeared > 0 else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    return {
        "documents_evaluated": len(results),
        "total_pii_entities": total_pii,
        "total_appeared_in_llm_output": total_appeared,
        "aggregate_true_positives": total_tp,
        "aggregate_false_negatives": total_fn,
        "aggregate_residual_fakes": total_fp,
        "precision": round(precision * 100, 2),
        "recall": round(recall * 100, 2),
        "f1_score": round(f1 * 100, 2),
    }


def print_result(doc_id: str, result: dict) -> None:
    ev = result.get("evaluation", {})
    print(f"  [{doc_id}]")
    print(f"    PII mapped:       {ev.get('total_pii_mapped', '?')}")
    print(f"    Appeared in LLM:  {ev.get('appeared_in_llm_output', '?')}")
    print(f"    True Positives:   {ev.get('true_positives', '?')}")
    print(f"    False Negatives:  {ev.get('false_negatives', '?')}")
    print(f"    Residual fakes:   {ev.get('residual_fakes_in_output', '?')}")
    print(f"    Precision:        {ev.get('precision', '?')}%")
    print(f"    Recall:           {ev.get('recall', '?')}%")
    print(f"    F1 Score:         {ev.get('f1_score', '?')}%")
    if ev.get("missed"):
        print(f"    Missed tokens:    {[m['original'] for m in ev['missed']]}")


def main():
    parser = argparse.ArgumentParser(description="Batch evaluate the AI Pseudonymizer pipeline")
    parser.add_argument("--api", default="http://localhost:5678/webhook/pseudonymize",
                        help="Webhook URL of the n8n pipeline")
    parser.add_argument("--docs", default="../data/documents",
                        help="Directory containing .txt medical documents")
    parser.add_argument("--truth", default="../data/ground_truth",
                        help="Directory containing ground truth .json files")
    parser.add_argument("--output", default="results.json",
                        help="Output file for full results")
    parser.add_argument("--delay", type=float, default=2.0,
                        help="Seconds to wait between requests (rate limiting)")
    args = parser.parse_args()

    docs_dir = Path(args.docs)
    truth_dir = Path(args.truth)

    if not docs_dir.exists():
        print(f"Error: documents directory not found: {docs_dir}", file=sys.stderr)
        sys.exit(1)

    doc_files = sorted(docs_dir.glob("*.txt"))
    if not doc_files:
        print(f"Error: no .txt files found in {docs_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(doc_files)} documents. Starting evaluation...\n")
    print(f"Pipeline URL: {args.api}\n")

    all_results = []
    failed = []

    for doc_path in doc_files:
        doc_id = doc_path.stem
        truth_path = truth_dir / f"{doc_id}.json"

        print(f"Processing: {doc_id}")

        ground_truth_pii = []
        if truth_path.exists():
            gt = load_ground_truth(truth_path)
            ground_truth_pii = gt.get("pii_entities", [])
        else:
            print(f"  Warning: no ground truth file for {doc_id}")

        try:
            document_text = load_document(doc_path)
            prompt_type = determine_prompt_type(doc_id)

            result = send_to_pipeline(
                api_url=args.api,
                document_text=document_text,
                prompt_type=prompt_type,
                document_id=doc_id,
                ground_truth_pii=ground_truth_pii,
            )

            print_result(doc_id, result)
            all_results.append(result)

        except Exception as e:
            print(f"  FAILED: {e}")
            failed.append({"document_id": doc_id, "error": str(e)})

        if args.delay > 0:
            time.sleep(args.delay)

    # Aggregate metrics
    print("\n" + "=" * 60)
    print("AGGREGATE RESULTS")
    print("=" * 60)

    if all_results:
        agg = compute_aggregate_metrics(all_results)
        for key, val in agg.items():
            label = key.replace("_", " ").title()
            suffix = "%" if key in ("precision", "recall", "f1_score") else ""
            print(f"  {label:<35} {val}{suffix}")
    else:
        print("  No successful results to aggregate.")

    if failed:
        print(f"\n  Failed documents ({len(failed)}): {[f['document_id'] for f in failed]}")

    # Save full results
    output = {
        "aggregate": compute_aggregate_metrics(all_results) if all_results else {},
        "per_document": all_results,
        "failed": failed,
    }
    output_path = Path(args.output)
    output_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nFull results saved to: {output_path.resolve()}")


if __name__ == "__main__":
    main()
