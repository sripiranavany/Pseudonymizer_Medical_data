# AI Pseudonymizer for Medical Data

A privacy-preserving system that pseudonymizes German medical documents before sending them to an external LLM for analysis, then restores real names in the output. Patient data never leaves the local environment in identifiable form.

---

## File Structure

```
project/
├── pseudonymization-stage/        # Stage 1 — PII extraction & pseudonymization service
│   ├── workflows/
│   │   └── AI Pseudonymizer - Final.json   # n8n workflow definition (import via UI)
│   ├── Dockerfile                 # Container image for the FastAPI service
│   ├── docker-compose.yml         # Orchestrates: FastAPI + Ollama + n8n + Gotenberg
│   ├── main.py                    # FastAPI app — /pseudonymize and /pseudonymize/upload endpoints
│   ├── pipeline.py                # LangGraph StateGraph — PII extraction, pseudonymization, evaluation
│   ├── evaluate.py                # Batch evaluation script against test documents
│   ├── requirements.txt           # Python dependencies
│   └── test_commands.md           # Example curl commands for manual testing
│
├── report/
│   ├── stage1_pseudonymization_report.md   # Evaluation results and analysis writeup
│   └── AI_Pseudonymizer_Implementation_EN.pptx  # Presentation slides
│
├── demo_video/
│   ├── BIP_Intelligent_System.mp4
│   └── full_video.mp4
│
├── .bip/                          # Python virtual environment (not committed)
├── .gitattributes                 # Git LFS tracking for large files
├── .gitignore
└── README.md
```

### Key files explained

| File | Purpose |
|---|---|
| `pseudonymization-stage/main.py` | Entry point. Exposes `/pseudonymize` (JSON body) and `/pseudonymize/upload` (file upload). Accepts PDF, DOCX, or TXT. |
| `pseudonymization-stage/pipeline.py` | Core LangGraph pipeline with nodes: `extract_pii` → `pseudonymize` → `evaluate_detection` → `check_leakage` → `assess_irreversibility`. |
| `pseudonymization-stage/evaluate.py` | Standalone batch runner. Reads documents from `test_documents/` and their ground truth, prints aggregate Precision / Recall / F1 and saves `eval_results.json`. |
| `pseudonymization-stage/docker-compose.yml` | Defines four services: `pseudonymization-stage` (FastAPI on 8001), `stage1-ollama` (Mistral 7B on 11435), `stage1-n8n` (workflow UI on 5679), `gotenberg` (PDF rendering). |
| `pseudonymization-stage/workflows/AI Pseudonymizer - Final.json` | Import this into n8n to get the pre-built workflow. |

---

## Project Setup

### Prerequisites

- Docker and Docker Compose installed
- At least 8 GB RAM (Mistral 7B runs on CPU inside Ollama)
- Python 3.12+ (only needed for running `evaluate.py` locally without Docker)

---

### Option A — Docker (recommended)

This starts the full Stage 1 stack: FastAPI service, local Ollama LLM, n8n, and Gotenberg.

```bash
cd pseudonymization-stage
docker compose up -d
```

Wait ~60 seconds for Ollama to pull the Mistral model on first run. Check progress:

```bash
docker logs stage1-ollama-init -f
```

**Service endpoints once running:**

| Service | URL | Credentials |
|---|---|---|
| FastAPI (pseudonymization API) | <http://localhost:8001/docs> | — |
| n8n (workflow UI) | <http://localhost:5679> | admin / admin |
| Ollama | <http://localhost:11435> | — |

**Import the n8n workflow:**

1. Open <http://localhost:5679>
2. Go to Workflows → Import from file
3. Select `pseudonymization-stage/workflows/AI Pseudonymizer - Final.json`

---

### Option B — Local Python (no Docker)

Use the `.bip` virtual environment that is already set up in the project root.

```bash
# Activate the environment
source .bip/bin/activate

# Install dependencies
pip install -r pseudonymization-stage/requirements.txt

# Start the API
cd pseudonymization-stage
uvicorn main:app --port 8001 --reload
```

API docs available at <http://localhost:8001/docs>.

> Note: In local mode the service expects Ollama to be running separately on `http://localhost:11434`.
> Start it with: `ollama serve` and `ollama pull mistral`

---

### Testing

**Single document via curl:**

```bash
curl -s -X POST http://localhost:8001/pseudonymize \
  -H "Content-Type: application/json" \
  -d '{
    "document_text": "Patient: Hans Müller, Geburtsdatum: 14.03.1958. Versicherungsnummer: A123456789. Arzt: Dr. Sabine Hoffmann.",
    "document_id": "test_01",
    "ground_truth": [
      {"entity": "Hans Müller",        "type": "PERSON_NAME"},
      {"entity": "14.03.1958",          "type": "DATE_OF_BIRTH"},
      {"entity": "A123456789",          "type": "HEALTH_INSURANCE_ID"},
      {"entity": "Dr. Sabine Hoffmann", "type": "PERSON_NAME"}
    ]
  }' | python3 -m json.tool
```

**File upload:**

```bash
curl -s -X POST http://localhost:8001/pseudonymize/upload \
  -F "file=@/path/to/document.pdf" | python3 -m json.tool
```

**Batch evaluation** (runs all test documents and prints metrics):

```bash
cd pseudonymization-stage
python evaluate.py
```

---

### Stopping the stack

```bash
cd pseudonymization-stage
docker compose down
```

To also remove the Ollama model volume (frees ~4 GB):

```bash
docker compose down -v
```

---

## Environment Variables

All defaults are set in `docker-compose.yml`. Override them by creating a `.env` file inside `pseudonymization-stage/`:

| Variable | Default | Description |
| --- | --- | --- |
| `OLLAMA_BASE_URL` | `http://stage1-ollama:11434` | Ollama endpoint for PII extraction |
| `POSTGRES_HOST` | `stage1-postgres` | PostgreSQL host (service disabled by default) |
| `POSTGRES_DB` | `stage1_db` | Database name |
| `POSTGRES_USER` | `stage1_user` | Database user |
| `POSTGRES_PASSWORD` | `stage1_pass` | Database password |

> PostgreSQL is defined in `docker-compose.yml` but commented out — the Stage 1 service runs without a database. Uncomment the `stage1-postgres` service block to enable persistent storage.
