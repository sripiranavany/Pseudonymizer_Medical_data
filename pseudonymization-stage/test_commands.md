# Test Commands — Stage 1: Pseudonymization Evaluation

All services must be running:
```bash
cd pseudonymization-stage && docker compose up -d
```

---

## 1. Health check

```bash
curl -s http://localhost:8001/health | python3 -m json.tool
```

Expected:
```json
{ "api": "ok", "postgres": "ok" }
```

---

## 2. Direct API — plain text (no ground truth)

Tests the full Stage 1 pipeline with a German medical text.
Returns the pseudonymized text, mapping, and all three evaluations.
No ground truth → FP/FN cannot be computed (precision/recall will be null).

```bash
curl -s -X POST http://localhost:8001/pseudonymize \
  -H "Content-Type: application/json" \
  -d '{
    "document_text": "Patient: Hans Müller, DOB: 14.03.1958, Address: Hauptstraße 5, 80333 München. Insurance No: A123456789. Physician: Dr. Sabine Hoffmann. Diagnosis: Type 2 Diabetes Mellitus.",
    "document_id": "test_stage1_1"
  }' | python3 -m json.tool
```

---

## 3. Direct API — with ground truth (enables FP/FN evaluation)

Provide `ground_truth` to get real precision, recall, and F1 for PII detection.
Any entity the LLM missed shows up as a false negative; any extra entity it tagged shows up as a false positive.

```bash
curl -s -X POST http://localhost:8001/pseudonymize \
  -H "Content-Type: application/json" \
  -d '{
    "document_text": "Patient: Hans Müller, DOB: 14.03.1958, Address: Hauptstraße 5, 80333 München. Insurance No: A123456789. Physician: Dr. Sabine Hoffmann. Diagnosis: Type 2 Diabetes Mellitus.",
    "document_id": "test_stage1_gt",
    "ground_truth": [
      {"entity": "Hans Müller",       "type": "NAME"},
      {"entity": "14.03.1958",         "type": "DATE"},
      {"entity": "Hauptstraße 5, 80333 München", "type": "ADDRESS"},
      {"entity": "A123456789",         "type": "ID_NUMBER"},
      {"entity": "Dr. Sabine Hoffmann","type": "NAME"}
    ]
  }' | python3 -m json.tool
```

---

## 4. Print only the pseudonymized text

```bash
curl -s -X POST http://localhost:8001/pseudonymize \
  -H "Content-Type: application/json" \
  -d '{
    "document_text": "Patient: Hans Müller, DOB: 14.03.1958. Insurance No: A123456789. Physician: Dr. Sabine Hoffmann.",
    "document_id": "quick_pseudo"
  }' | python3 -c "import sys,json; print(json.load(sys.stdin)['pseudonymized_text'])"
```

---

## 5. Print only the three evaluation verdicts

```bash
curl -s -X POST http://localhost:8001/pseudonymize \
  -H "Content-Type: application/json" \
  -d '{
    "document_text": "Patient: Hans Müller, DOB: 14.03.1958. Insurance No: A123456789. Physician: Dr. Sabine Hoffmann.",
    "document_id": "verdicts_test",
    "ground_truth": [
      {"entity": "Hans Müller",        "type": "NAME"},
      {"entity": "14.03.1958",          "type": "DATE"},
      {"entity": "A123456789",          "type": "ID_NUMBER"},
      {"entity": "Dr. Sabine Hoffmann", "type": "NAME"}
    ]
  }' | python3 -c "
import sys, json
r = json.load(sys.stdin)
det  = r['pii_detection_eval']
leak = r['context_leakage']
rev  = r['irreversibility']
print(f\"Detection  : precision={det.get('precision')}%  recall={det.get('recall')}%  f1={det.get('f1_score')}%\")
print(f\"             TP={det.get('true_positives')}  FP={det.get('false_positives')}  FN={det.get('false_negatives')}\")
print(f\"             FP entities : {det.get('fp_entities')}\")
print(f\"             FN entities : {det.get('fn_entities')}\")
print(f\"Leakage    : {leak.get('verdict')}  (leaked={leak.get('leaked_count')}  entities={leak.get('leaked_entities')})\")
print(f\"Reversible : {rev.get('verdict')}  (HIGH={rev.get('high_risk_count')}  MED={rev.get('medium_risk_count')}  LOW={rev.get('low_risk_count')})\")
print(f\"             HIGH-risk: {rev.get('high_risk_entities')}\")
"
```

---

## 6. Via n8n webhook

First import the workflow:
1. Open http://localhost:5679
2. Log in: `admin / admin`
3. Workflows → Import from file → select `workflows/stage1_pseudonymization.json`
4. Activate the workflow (toggle top-right)

Then call the webhook:

```bash
curl -s -X POST http://localhost:5679/webhook/stage1-pseudonymize \
  -H "Content-Type: application/json" \
  -d '{
    "document_text": "Patient: Klara Zimmermann, DOB: 30.08.1985. Acute chest pain, ST-elevation. Troponin I: 0.82 ng/mL. Physician: Dr. Felix Braun. Insurance: D234567890.",
    "document_id": "test_n8n_stage1",
    "ground_truth": [
      {"entity": "Klara Zimmermann", "type": "NAME"},
      {"entity": "30.08.1985",       "type": "DATE"},
      {"entity": "Dr. Felix Braun",  "type": "NAME"},
      {"entity": "D234567890",       "type": "ID_NUMBER"}
    ]
  }' | python3 -m json.tool
```

The n8n response includes formatted `pii_detection`, `context_leakage`, `irreversibility`, and `privacy_summary` sections.

---

## 7. Upload a PDF file

```bash
curl -s -X POST http://localhost:8001/pseudonymize/upload \
  -F "file=@../files/01_ClinicalSummarization_DE_Mueller.pdf" \
  -F "document_id=mueller_stage1" \
  | python3 -m json.tool
```

---

## 8. Upload each PDF and show only the verdict

```bash
for pdf in ../files/*.pdf; do
  name=$(basename "$pdf" .pdf)
  echo "=== $name ==="
  curl -s -X POST http://localhost:8001/pseudonymize/upload \
    -F "file=@$pdf" \
    -F "document_id=$name" \
    | python3 -c "
import sys, json
r = json.load(sys.stdin)
leak = r['context_leakage']
rev  = r['irreversibility']
print(f\"  PII detected : {len(r.get('pii_entities', []))}\")
print(f\"  Leakage      : {leak.get('verdict')}\")
print(f\"  Reversibility: {rev.get('verdict')}  (HIGH={rev.get('high_risk_count')} MED={rev.get('medium_risk_count')})\")
"
done
```

---

## 9. Batch evaluation with evaluate.py

Requires `.txt` documents and optional `.json` ground-truth files.
The `evaluate.py` script calls `/pseudonymize` for each document and aggregates metrics.

```bash
# Install deps (if running outside Docker)
pip install requests

# Run against the running Stage 1 API
cd pseudonymization-stage
python evaluate.py \
  --api  http://localhost:8001/pseudonymize \
  --docs ../data/documents \
  --truth ../data/ground_truth \
  --output stage1_results.json
```

---

## 10. Inspect the mapping (what was replaced with what)

```bash
curl -s -X POST http://localhost:8001/pseudonymize \
  -H "Content-Type: application/json" \
  -d '{
    "document_text": "Patient: Karl-Heinz Brandt, DOB: 28.02.1946. Insurance: G567890123. Physician: Dr. Luise Kessler.",
    "document_id": "mapping_test"
  }' | python3 -c "
import sys, json
r = json.load(sys.stdin)
print('MAPPING (original → fake):')
for orig, fake in r['mapping'].items():
    print(f'  {orig!r:40s} → {fake!r}')
"
```

---

## What to look for in results

| Field | Good sign | Concern |
|---|---|---|
| `pii_detection_eval.recall` | ≥ 90% | Low recall = PII missed → false negatives in LLM output |
| `pii_detection_eval.precision` | ≥ 90% | Low precision = non-PII tagged → unnecessary replacements |
| `context_leakage.verdict` | `CLEAN` | `PII_LEAKED` = a replacement was missed |
| `irreversibility.verdict` | `IRREVERSIBLE` | `REVERSIBLE_RISK` = DATE shift is deterministic |
| `irreversibility.high_risk_entities` | empty | Non-empty = attacker can recover dates with fixed -2yr shift |
