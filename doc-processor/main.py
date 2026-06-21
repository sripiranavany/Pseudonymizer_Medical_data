import io
import json
import logging
import os
import uuid
from datetime import datetime
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

from langgraph_pipeline import pipeline as lg_pipeline

app = FastAPI(title="Doc Processor", description="Document extraction + DB bridge for AI Pseudonymizer")

# ── Connections ────────────────────────────────────────────────────────────────

def get_pg():
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "postgres"),
        port=int(os.getenv("POSTGRES_PORT", 5432)),
        dbname=os.getenv("POSTGRES_DB", "pseudonymizer"),
        user=os.getenv("POSTGRES_USER", "pseudo_user"),
        password=os.getenv("POSTGRES_PASSWORD", "pseudo_pass")
    )

# ── Models ─────────────────────────────────────────────────────────────────────

class ResultStore(BaseModel):
    document_id: str
    pseudonymized_text: Optional[str] = None
    raw_llm_output: Optional[str] = None
    restored_analysis: Optional[str] = None
    mapping: Optional[dict] = None
    pii_entity_count: Optional[int] = None

class EvaluationStore(BaseModel):
    document_id: str
    precision_score: float
    recall_score: float
    f1_score: float
    true_positives: int
    false_negatives: int
    residual_fakes: int
    total_pii: int
    appeared_in_llm: int
    details: Optional[dict] = None

class DocumentStore(BaseModel):
    document_id: Optional[str] = None
    filename: Optional[str] = None
    file_type: str = "text"
    raw_text: str
    prompt_type: str = "clinical_summary"

# ── Helpers ────────────────────────────────────────────────────────────────────

def extract_pdf(content: bytes) -> str:
    reader = PdfReader(io.BytesIO(content))
    pages = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            pages.append(text.strip())
    return "\n\n".join(pages)

def extract_docx(content: bytes) -> str:
    doc = DocxDocument(io.BytesIO(content))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n".join(paragraphs)

def normalize(text: str) -> str:
    # Strip NUL bytes (PostgreSQL rejects them) and normalize line endings
    return (text
        .replace("\x00", "")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .strip())

# ── Document Extraction ────────────────────────────────────────────────────────

@app.post("/extract")
async def extract_text(
    file: Optional[UploadFile] = File(None),
    prompt_type: Optional[str] = Form("clinical_summary"),
    document_id: Optional[str] = Form(None),
):
    """
    Accept a PDF, DOCX, or TXT file upload and return extracted plain text.
    """
    if file is None:
        raise HTTPException(status_code=400, detail="No file provided. Use /extract-text for plain text input.")

    content = await file.read()
    filename = file.filename or "unknown"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "txt"

    try:
        if ext == "pdf":
            text = extract_pdf(content)
            file_type = "pdf"
        elif ext in ("docx", "doc"):
            text = extract_docx(content)
            file_type = "docx"
        elif ext in ("txt", "text", "md"):
            text = content.decode("utf-8", errors="replace")
            file_type = "txt"
        else:
            # Try plain text as fallback
            text = content.decode("utf-8", errors="replace")
            file_type = "txt"
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Failed to extract text: {str(e)}")

    if not text.strip():
        raise HTTPException(status_code=422, detail="No text could be extracted from the file.")

    doc_id = document_id or f"doc_{uuid.uuid4().hex[:8]}"

    return {
        "document_id": doc_id,
        "filename": filename,
        "file_type": file_type,
        "text": normalize(text),
        "char_count": len(text),
        "prompt_type": prompt_type,
    }


# ── PostgreSQL — Document Store ────────────────────────────────────────────────

@app.post("/db/documents")
def store_document(body: DocumentStore):
    """
    Store a document record in PostgreSQL.
    """
    doc_id = body.document_id or f"doc_{uuid.uuid4().hex[:8]}"
    # Strip NUL bytes — PostgreSQL cannot store them in text columns
    safe_text = body.raw_text.replace("\x00", "") if body.raw_text else ""
    conn = get_pg()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO documents (id, filename, file_type, raw_text, prompt_type, char_count)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        filename = EXCLUDED.filename,
                        file_type = EXCLUDED.file_type,
                        raw_text = EXCLUDED.raw_text,
                        prompt_type = EXCLUDED.prompt_type,
                        char_count = EXCLUDED.char_count
                """, (doc_id, body.filename, body.file_type, safe_text,
                      body.prompt_type, len(safe_text)))
    finally:
        conn.close()
    return {"status": "stored", "document_id": doc_id}


@app.post("/db/results")
def store_result(body: ResultStore):
    """
    Store analysis result in PostgreSQL.
    """
    conn = get_pg()
    try:
        with conn:
            with conn.cursor() as cur:
                # Ensure document row exists — n8n may execute this branch before 0b. Save Document
                cur.execute("""
                    INSERT INTO documents (id, filename, file_type, raw_text, prompt_type, char_count)
                    VALUES (%s, %s, 'unknown', '', 'unknown', 0)
                    ON CONFLICT (id) DO NOTHING
                """, (body.document_id, body.document_id))
                cur.execute("""
                    INSERT INTO results
                        (document_id, pseudonymized_text, raw_llm_output,
                         restored_analysis, mapping, pii_entity_count)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (
                    body.document_id,
                    body.pseudonymized_text,
                    body.raw_llm_output,
                    body.restored_analysis,
                    json.dumps(body.mapping) if body.mapping else None,
                    body.pii_entity_count,
                ))
    finally:
        conn.close()
    return {"status": "stored", "document_id": body.document_id}


@app.post("/db/evaluations")
def store_evaluation(body: EvaluationStore):
    """
    Store evaluation metrics in PostgreSQL.
    """
    conn = get_pg()
    try:
        with conn:
            with conn.cursor() as cur:
                # Ensure document row exists — n8n may execute this branch before 0b. Save Document
                cur.execute("""
                    INSERT INTO documents (id, filename, file_type, raw_text, prompt_type, char_count)
                    VALUES (%s, %s, 'unknown', '', 'unknown', 0)
                    ON CONFLICT (id) DO NOTHING
                """, (body.document_id, body.document_id))
                cur.execute("""
                    INSERT INTO evaluations
                        (document_id, precision_score, recall_score, f1_score,
                         true_positives, false_negatives, residual_fakes,
                         total_pii, appeared_in_llm, details)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    body.document_id,
                    body.precision_score,
                    body.recall_score,
                    body.f1_score,
                    body.true_positives,
                    body.false_negatives,
                    body.residual_fakes,
                    body.total_pii,
                    body.appeared_in_llm,
                    json.dumps(body.details) if body.details else None,
                ))
    finally:
        conn.close()
    return {"status": "stored", "document_id": body.document_id}


@app.get("/db/results")
def list_results():
    """
    List all results with evaluation scores from PostgreSQL.
    """
    conn = get_pg()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT d.id, d.filename, d.file_type, d.prompt_type, d.created_at,
                       r.restored_analysis, r.pii_entity_count,
                       e.precision_score, e.recall_score, e.f1_score
                FROM documents d
                LEFT JOIN results r ON r.document_id = d.id
                LEFT JOIN evaluations e ON e.document_id = d.id
                ORDER BY d.created_at DESC
            """)
            rows = cur.fetchall()
            cols = [desc[0] for desc in cur.description]
    finally:
        conn.close()

    return {"results": [dict(zip(cols, row)) for row in rows]}


@app.get("/db/results/{document_id}")
def get_result(document_id: str):
    """
    Get full result for a specific document from PostgreSQL.
    """
    conn = get_pg()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT d.id, d.filename, d.file_type, d.prompt_type, d.raw_text, d.created_at,
                       r.pseudonymized_text, r.restored_analysis, r.mapping, r.pii_entity_count,
                       e.precision_score, e.recall_score, e.f1_score, e.details
                FROM documents d
                LEFT JOIN results r ON r.document_id = d.id
                LEFT JOIN evaluations e ON e.document_id = d.id
                WHERE d.id = %s
            """, (document_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail=f"Document {document_id} not found")
            cols = [desc[0] for desc in cur.description]
    finally:
        conn.close()

    return dict(zip(cols, row))


class LangGraphRequest(BaseModel):
    document_id: str
    document_text: str
    prompt_type: Optional[str] = "clinical_summary"
    pii_section: Optional[str] = None
    custom_prompt: Optional[str] = None
    ground_truth: Optional[list] = None


@app.post("/langgraph/analyze")
def langgraph_analyze(body: LangGraphRequest):
    """
    Run the full pseudonymization pipeline as a LangGraph state graph.
    Returns the complete result including evaluation metrics and pre-built DB payloads.
    """
    log.info("=== pipeline start | doc=%s | prompt=%s | chars=%d ===",
             body.document_id, body.prompt_type, len(body.document_text))
    text = normalize(body.document_text)
    pii_section = body.pii_section or text[:3000]

    initial_state = {
        "document_id": body.document_id,
        "prompt_type": body.prompt_type or "clinical_summary",
        "original_text": text,
        "pii_section": pii_section,
        "custom_prompt": body.custom_prompt,
        "ground_truth": body.ground_truth,
        "pii_entities": [],
        "mapping": {},
        "reverse_mapping": {},
        "pseudonymized_text": "",
        "filled_prompt": "",
        "raw_llm_output": "",
        "restored_analysis": "",
        "restored_tokens": [],
        "evaluation": {},
        "retry_count": 0,
        "error": None,
    }

    final_state = lg_pipeline.invoke(initial_state)

    if final_state.get("error"):
        log.error("=== pipeline FAILED | doc=%s | error=%s ===", body.document_id, final_state["error"])
        raise HTTPException(status_code=500, detail=final_state["error"])

    log.info("=== pipeline done | doc=%s | pii=%d | f1=%.1f%% ===",
             body.document_id,
             len(final_state.get("mapping", {})),
             final_state.get("evaluation", {}).get("f1_score", 0))

    mapping = final_state.get("mapping", {})
    evaluation = final_state.get("evaluation", {})

    # Pre-build DB payloads so n8n storage nodes use a simple body reference
    db_result_body = json.dumps({
        "document_id": body.document_id,
        "pseudonymized_text": final_state.get("pseudonymized_text"),
        "raw_llm_output": final_state.get("raw_llm_output"),
        "restored_analysis": final_state.get("restored_analysis"),
        "mapping": mapping,
        "pii_entity_count": len(mapping),
    })
    db_eval_body = json.dumps({
        "document_id": body.document_id,
        "precision_score": evaluation.get("precision", 0),
        "recall_score": evaluation.get("recall", 0),
        "f1_score": evaluation.get("f1_score", 0),
        "true_positives": evaluation.get("true_positives", 0),
        "false_negatives": evaluation.get("false_negatives", 0),
        "residual_fakes": evaluation.get("residual_fakes_in_output", 0),
        "total_pii": evaluation.get("total_pii_mapped", 0),
        "appeared_in_llm": evaluation.get("appeared_in_llm_output", 0),
        "details": evaluation,
    })

    return {
        "document_id": body.document_id,
        "prompt_type": body.prompt_type,
        "original_text": text,
        "pii_entities": final_state.get("pii_entities", []),
        "mapping": mapping,
        "reverse_mapping": final_state.get("reverse_mapping", {}),
        "mapping_size": len(mapping),
        "pseudonymized_text": final_state.get("pseudonymized_text"),
        "filled_prompt": final_state.get("filled_prompt"),
        "raw_llm_output": final_state.get("raw_llm_output"),
        "restored_analysis": final_state.get("restored_analysis"),
        "restored_tokens": final_state.get("restored_tokens", []),
        "restored_token_count": len(final_state.get("restored_tokens", [])),
        "evaluation": evaluation,
        "ground_truth": body.ground_truth,
        "db_result_body": db_result_body,
        "db_eval_body": db_eval_body,
    }


@app.get("/health")
def health():
    status = {"api": "ok", "postgres": "unknown"}
    try:
        conn = get_pg()
        conn.close()
        status["postgres"] = "ok"
    except Exception as e:
        status["postgres"] = str(e)
    return status
