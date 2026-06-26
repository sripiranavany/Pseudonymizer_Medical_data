import io
import json
import logging
import os
import uuid
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("main")

import psycopg2
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from pypdf import PdfReader
from docx import Document as DocxDocument

from pipeline import pipeline as pseudo_pipeline

app = FastAPI(
    title="Pseudonymization Stage",
    description=(
        "Stage 1 of the AI Pseudonymizer: PII extraction + pseudonymization. "
        "Evaluates detection quality (FP/FN vs ground truth), context leakage, "
        "and irreversibility risk — without invoking the external LLM."
    ),
)


# ── DB connection ──────────────────────────────────────────────────────────────

def get_pg():
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "postgres"),
        port=int(os.getenv("POSTGRES_PORT", 5432)),
        dbname=os.getenv("POSTGRES_DB", "pseudonymizer"),
        user=os.getenv("POSTGRES_USER", "pseudo_user"),
        password=os.getenv("POSTGRES_PASSWORD", "pseudo_pass"),
    )


# ── Helpers ────────────────────────────────────────────────────────────────────

def extract_pdf(content: bytes) -> str:
    reader = PdfReader(io.BytesIO(content))
    return "\n\n".join(p.extract_text().strip() for p in reader.pages if p.extract_text())


def extract_docx(content: bytes) -> str:
    doc = DocxDocument(io.BytesIO(content))
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())


def normalize(text: str) -> str:
    return text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n").strip()


# ── Request model ──────────────────────────────────────────────────────────────

class PseudonymizeRequest(BaseModel):
    document_id: Optional[str] = None
    document_text: str
    pii_section: Optional[str] = None       # defaults to first 3000 chars
    ground_truth: Optional[list] = None     # list of {"entity":..., "type":...}


# ── Main endpoint ──────────────────────────────────────────────────────────────

@app.post("/pseudonymize")
def pseudonymize(body: PseudonymizeRequest):
    """
    Run Stage 1: extract PII, pseudonymize, then evaluate.

    Returns:
    - pii_entities       : entities found by the LLM extractor
    - mapping            : original → fake substitution table
    - pseudonymized_text : document with all PII replaced
    - pii_detection_eval : precision / recall / F1 vs ground_truth (if provided)
    - context_leakage    : whether any original PII survived into the output
    - irreversibility    : per-entity risk level (HIGH / MEDIUM / LOW) with reasoning
    """
    doc_id = body.document_id or f"doc_{uuid.uuid4().hex[:8]}"
    text   = normalize(body.document_text)
    log.info("=== stage1 start | doc=%s | chars=%d ===", doc_id, len(text))

    initial_state = {
        "document_id":      doc_id,
        "original_text":    text,
        "pii_section":      body.pii_section or text[:3000],
        "ground_truth":     body.ground_truth,
        "pii_entities":     [],
        "mapping":          {},
        "reverse_mapping":  {},
        "pseudonymized_text": "",
        "pii_detection_eval": {},
        "context_leakage":  {},
        "irreversibility":  {},
        "retry_count":      0,
        "error":            None,
    }

    final = pseudo_pipeline.invoke(initial_state)

    if final.get("error"):
        log.error("=== stage1 FAILED | doc=%s | %s ===", doc_id, final["error"])
        raise HTTPException(status_code=500, detail=final["error"])

    log.info(
        "=== stage1 done | doc=%s | pii=%d | leakage=%s | reversibility=%s ===",
        doc_id,
        len(final.get("mapping", {})),
        final.get("context_leakage", {}).get("verdict", "?"),
        final.get("irreversibility", {}).get("verdict", "?"),
    )

    return {
        "document_id":        doc_id,
        "original_text":      text,
        "pii_entities":       final.get("pii_entities", []),
        "mapping":            final.get("mapping", {}),
        "reverse_mapping":    final.get("reverse_mapping", {}),
        "pseudonymized_text": final.get("pseudonymized_text", ""),
        "pii_detection_eval": final.get("pii_detection_eval", {}),
        "context_leakage":    final.get("context_leakage", {}),
        "irreversibility":    final.get("irreversibility", {}),
    }


# ── File upload convenience endpoint ──────────────────────────────────────────

@app.post("/pseudonymize/upload")
async def pseudonymize_upload(
    file: UploadFile = File(...),
    document_id: Optional[str] = Form(None),
):
    """
    Upload a PDF / DOCX / TXT file and run Stage 1 on the extracted text.
    Ground truth cannot be provided via this endpoint; use /pseudonymize for that.
    """
    content  = await file.read()
    filename = file.filename or "unknown"
    ext      = filename.rsplit(".", 1)[-1].lower() if "." in filename else "txt"

    try:
        if ext == "pdf":
            text = extract_pdf(content)
        elif ext in ("docx", "doc"):
            text = extract_docx(content)
        else:
            text = content.decode("utf-8", errors="replace")
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Failed to extract text: {exc}")

    if not text.strip():
        raise HTTPException(status_code=422, detail="No text could be extracted from the file.")

    req = PseudonymizeRequest(
        document_id=document_id or f"doc_{uuid.uuid4().hex[:8]}",
        document_text=text,
    )
    return pseudonymize(req)


# ── Health ─────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    status: dict = {"api": "ok", "postgres": "unknown"}
    try:
        get_pg().close()
        status["postgres"] = "ok"
    except Exception as exc:
        status["postgres"] = str(exc)
    return status
