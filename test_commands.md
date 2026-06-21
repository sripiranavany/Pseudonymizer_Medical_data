# Test Commands — AI Pseudonymizer Pipeline

All services must be running: `cd n8n && docker compose up -d`

---

## Analyze Text (copy-paste)

```bash
curl -s -X POST http://localhost:5678/webhook/pseudonymize \
  -H "Content-Type: application/json" \
  -d '{
    "document_text": "Patient: Hans Müller, DOB: 14.03.1958, Address: Hauptstraße 5, 80333 München. Insurance No: A123456789. Physician: Dr. Sabine Hoffmann. Diagnosis: Type 2 Diabetes Mellitus.",
    "prompt_type": "clinical_summary",
    "document_id": "test_clinical_1"
  }' | python3 -m json.tool
```

## Analyze PDF via Angular UI backend

```bash
curl -s -X POST http://localhost:3000/api/analyze/file \
  -F "file=@files/01_ClinicalSummarization_DE_Mueller.pdf" \
  -F "prompt_type=clinical_summary" \
  | python3 -m json.tool
```

## Diagnosis support prompt

```bash
curl -s -X POST http://localhost:5678/webhook/pseudonymize \
  -H "Content-Type: application/json" \
  -d '{
    "document_text": "Patient: Klara Zimmermann, DOB: 30.08.1985. Acute chest pain, ST-elevation leads II/III/aVF. Troponin I: 0.82 ng/mL. Physician: Dr. Felix Braun. Insurance: D234567890.",
    "prompt_type": "diagnosis_support",
    "document_id": "test_diagnosis_1"
  }' | python3 -m json.tool
```

## ICD coding prompt

```bash
curl -s -X POST http://localhost:5678/webhook/pseudonymize \
  -H "Content-Type: application/json" \
  -d '{
    "document_text": "Patient: Karl-Heinz Brandt, DOB: 28.02.1946. Total right hip arthroplasty. Primary: M16.1. Comorbidities: E11.9, I10, M81.0. Physician: Dr. Luise Kessler. Insurance: G567890123.",
    "prompt_type": "icd_coding",
    "document_id": "test_icd_1"
  }' | python3 -m json.tool
```

## View analysis history

```bash
curl -s http://localhost:3000/api/history | python3 -m json.tool
```

## View result for a specific document

```bash
curl -s http://localhost:3000/api/history/test_clinical_1 | python3 -m json.tool
```

## Check all service health

```bash
curl -s http://localhost:8000/health | python3 -m json.tool
```

## Print only the analysis text

```bash
curl -s -X POST http://localhost:5678/webhook/pseudonymize \
  -H "Content-Type: application/json" \
  -d '{"document_text":"Patient: Hans Müller, DOB: 14.03.1958. Type 2 Diabetes. Physician: Dr. Sabine Hoffmann.","prompt_type":"clinical_summary","document_id":"quick_test"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['result']['analysis'])"
```

## Print only evaluation scores

```bash
curl -s -X POST http://localhost:5678/webhook/pseudonymize \
  -H "Content-Type: application/json" \
  -d '{"document_text":"Patient: Hans Müller, DOB: 14.03.1958. Type 2 Diabetes. Physician: Dr. Sabine Hoffmann.","prompt_type":"clinical_summary","document_id":"eval_test"}' \
  | python3 -c "import sys,json; e=json.load(sys.stdin)['evaluation']; print(f'Precision: {e[\"precision\"]}%  Recall: {e[\"recall\"]}%  F1: {e[\"f1_score\"]}%')"
```

## Run batch evaluation across all 9 documents

```bash
cd evaluation && pip install requests && python evaluate.py
```

## PostgreSQL — check stored results

```bash
docker exec postgres psql -U pseudo_user -d pseudonymizer \
  -c "SELECT d.filename, r.pii_entity_count, e.f1_score FROM results r JOIN documents d ON d.id=r.document_id JOIN evaluations e ON e.document_id=r.document_id ORDER BY r.created_at DESC LIMIT 10;"
```
