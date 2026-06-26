# AI Pseudonymizer for Medical Data — System Architecture

## Overview

The system pseudonymizes German medical documents before sending them to an external LLM for analysis, then restores real names in the output. Patient data never leaves the local environment in identifiable form.

---

## Components

| Container | Role | Port |
|---|---|---|
| `ui-frontend` | Angular UI — upload files, view results | 4200 |
| `ui-backend` | Express.js — API bridge between UI and n8n/doc-processor | 3000 |
| `n8n` | Workflow orchestration — triggers pipeline, stores results | 5678 |
| `doc-processor` | FastAPI — text extraction, LangGraph pipeline, DB bridge | 8000 |
| `ollama` | Local LLM (Mistral 7B) — PII detection only, never external | 11434 |
| `postgres` | Persistent storage — documents, results, evaluations | 5432 |
| `pgadmin` | PostgreSQL GUI | 5050 |

---

## Full Pipeline Flow

```
User (UI / curl)
      │
      ▼
[ui-backend :3000]
      │
      ├─ PDF/DOCX/TXT ──► POST /extract ──► [doc-processor :8000]
      │                                          extracts plain text
      │
      ▼
[n8n Webhook :5678/webhook/pseudonymize]
      │
      ▼
┌─────────────────────────────────────────────────────────────┐
│  n8n Workflow                                               │
│                                                             │
│  Node 1: Normalize Text                                     │
│    └─ strips whitespace, normalizes input                   │
│                                                             │
│  Node 2: LangGraph Pipeline                                 │
│    └─ POST /langgraph/analyze → [doc-processor :8000]       │
│                                                             │
│  Node 9: Format Response                                    │
│    └─ builds final JSON for the UI                          │
│                                                             │
│  ┌── Parallel storage branches ──────────────────────────┐  │
│  │  0b. Save Document    → POST /db/documents            │  │
│  │  7b. Store Result     → POST /db/results              │  │
│  │  8b. Store Evaluation → POST /db/evaluations          │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
      │
      ▼
Response → [ui-backend] → [ui-frontend]
```

---

## LangGraph Pipeline — 6 Nodes

The core logic runs as a `StateGraph` inside `doc-processor/langgraph_pipeline.py`.

```
original_text
      │
      ▼
[1. extract_pii]
  Sends text to local Ollama (Mistral 7B)
  Detects: names, dates, addresses, insurance numbers, physician names
  Output: list of PII entities + mapping { real → fake }
      │
      ▼ (retry once if no PII found)
[2. pseudonymize]
  Replaces real values with fake German data
  Names → random German first+last names
  Dates → shifted by ±2 years
  Addresses → fake German street/city
  Output: pseudonymized_text (safe to send externally)
      │
      ▼
[3. prepare_prompt]
  Selects prompt template by prompt_type:
    • clinical_summary   → structured medical summary (7 sections)
    • diagnosis_support  → differential diagnoses + workup
    • icd_coding         → ICD-10-GM codes + OPS + DRG hint
  Inserts pseudonymized_text into {document} placeholder
      │
      ▼
[4. analyze]
  Sends filled prompt to EXTERNAL_LLM (Mistral API / OpenRouter)
  Real patient data is NOT present — only pseudonyms
  Output: raw_llm_output (analysis with fake names)
      │
      ▼
[5. depseudonymize]
  Replaces fake names back to real names in the LLM output
  Uses reverse_mapping { fake → real }, longest-first to avoid partial matches
  Output: restored_analysis (analysis with real names)
      │
      ▼
[6. evaluate]
  Checks how many PII entities the LLM referenced in its output
  Calculates: Precision, Recall, F1-score
  Output: evaluation metrics
```

---

## Privacy Architecture

```
┌─────────────────────────────────────────────────┐
│              LOCAL ENVIRONMENT                  │
│                                                 │
│  Original document (real patient data)          │
│       │                                         │
│       ▼                                         │
│  [Ollama — Mistral 7B on CPU]                   │
│  PII Detection — stays 100% local               │
│       │                                         │
│       ▼                                         │
│  Pseudonymized document (fake names only)       │
│       │                                         │
└───────┼─────────────────────────────────────────┘
        │  ← only pseudonymized text crosses here
        ▼
┌─────────────────────────────────────────────────┐
│           EXTERNAL LLM (Mistral API)            │
│  Receives: document with fake names only        │
│  Returns:  analysis with fake names             │
└───────┼─────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────┐
│              LOCAL ENVIRONMENT                  │
│  De-pseudonymize: restore real names in output  │
│  Store: results + evaluations in PostgreSQL     │
└─────────────────────────────────────────────────┘
```

---

## External LLM Configuration

Configured via `n8n/.env` (gitignored — never committed):

```env
# Currently active: Mistral La Plateforme
EXTERNAL_LLM_BASE_URL=https://api.mistral.ai/v1
EXTERNAL_LLM_API_KEY=<your-key>
EXTERNAL_LLM_MODEL=mistral-small-latest
```

Switch providers without rebuilding — just update `.env` and run:
```bash
docker compose up -d doc-processor
```

Supported providers:
- **Mistral API** — EU servers, GDPR-friendly, 500k tokens/month free
- **OpenRouter** — access to many models, strict free tier rate limits
- **Local Ollama** — no rate limits, slower on CPU

---

## Data Storage (PostgreSQL)

```
documents
  id, filename, file_type, raw_text, prompt_type, char_count, created_at

results
  document_id (FK), pseudonymized_text, raw_llm_output,
  restored_analysis, mapping (JSON), pii_entity_count, created_at

evaluations
  document_id (FK), precision_score, recall_score, f1_score,
  true_positives, false_negatives, residual_fakes,
  total_pii, appeared_in_llm, details (JSON), created_at
```

---

## Starting the System

```bash
cd n8n
docker compose up -d
```

Access points:
- UI: http://localhost:4200
- n8n: http://localhost:5678 (admin / admin)
- pgAdmin: http://localhost:5050
- doc-processor API: http://localhost:8000/docs

---

## Stage 1 — spaCy Pseudonymization Evaluation (no Docker, no LLM)

A standalone stage that runs **only PII extraction + pseudonymization** using spaCy
and evaluates three things without calling any external LLM:

| Evaluation | What it checks |
|---|---|
| PII Detection Quality | Precision / Recall / F1 vs ground truth (TP / FP / FN) |
| Context Leakage | Did any real PII survive into the pseudonymized output? |
| Irreversibility Risk | Can an attacker reverse the pseudonymization without the mapping? |

PII definitions and detection rules are configured in `spacy-pseudonymization-stage/gdpr_pii_config.yaml`
(GDPR article references, regex patterns, context keywords, fake strategies).

### Step 1 — Install and start

```bash
cd spacy-pseudonymization-stage
pip install -r requirements.txt
python -m spacy download de_core_news_lg
uvicorn main:app --port 8002 --reload
```

API is now available at `http://localhost:8002`.
Interactive docs at `http://localhost:8002/docs`.

### Step 2 — Test a single document with ground truth

```bash
curl -s -X POST http://localhost:8002/pseudonymize \
  -H "Content-Type: application/json" \
  -d '{
    "document_text": "Patient: Hans Müller, Geburtsdatum: 14.03.1958. Versicherungsnummer: A123456789. Arzt: Dr. Sabine Hoffmann.",
    "document_id": "quick_test",
    "ground_truth": [
      {"entity": "Hans Müller",        "type": "PERSON_NAME"},
      {"entity": "14.03.1958",          "type": "DATE_OF_BIRTH"},
      {"entity": "A123456789",          "type": "HEALTH_INSURANCE_ID"},
      {"entity": "Dr. Sabine Hoffmann", "type": "PERSON_NAME"}
    ]
  }' | python3 -m json.tool
```

### Step 3 — Batch evaluation against all test documents

```bash
python evaluate.py
```

Runs all three documents in `test_documents/` against their ground truth files
in `test_documents/ground_truth/` and prints aggregate metrics:

```
  PII Detection Quality
    Precision:           xx%
    Recall:              xx%
    F1:                  xx%

  Detection Layers (total entities by source)
    regex          18  ████████████████████
    spacy_ner       9  █████████
    spacy_ruler     4  ████
    context_kw      2  ██

  Context Leakage
    Documents with leak: 0 / 3  (0.0%)

  Irreversibility Risk
    HIGH  risk entities: x   ← DATE shift is deterministic
    MEDIUM risk entities: x
    LOW   risk entities: x
```

Full results are saved to `eval_results.json`.

### Test documents

| File | Case | PII covered |
|---|---|---|
| `test_doc_01_clinical_de.txt` | Diabetes + gallstones discharge report | Name, DOB, address, insurance ID, phone, email, dates |
| `test_doc_02_diagnosis_de.txt` | Cardiology referral letter (STEMI) | Two physicians, patient, insurance ID, postal codes, dates |
| `test_doc_03_icd_coding_de.txt` | Hip replacement ICD coding sheet | Patient ID, three physicians, address, phone, multiple dates |
