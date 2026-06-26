# AI Pseudonymizer — Stage 1 Technical Report

**Date:** 2026-06-26  
**Workflow:** `AI Pseudonymizer - Final.json`

---

## Overview

The pipeline processes German medical documents from the **GrAScCo corpus** (UIMA CAS JSON format). It extracts PII using two parallel strategies — regex rules and a local LLM — merges the results, pseudonymizes the document, feeds it to an external LLM for clinical summarization, and then de-pseudonymizes the summary so the final output contains real patient data. The output is rendered as a PDF.

---

## Workflow Architecture

```text
Start (Manual)
    │
    ▼
Load Input          Reads UIMA CAS JSON from disk, extracts sofaString (text),
                    doc_id (DocumentMetaData), and PHI annotations (ground truth)
    │
    ▼
A - Detect (regex) ──────────────────────────────────────┐
    │                                                     │
    │ (parallel)                                          ▼
    ▼                                          Evaluation After Regex
AI Agent (Ollama / Mistral local)                   [dead end — metrics only]
    │
    ▼
Merge Detections    Combines regex + LLM entities; splits titles; deduplicates
    │
    │ (parallel)
    ├──────────────────────────────────────────┐
    ▼                                          ▼
B - Pseudonymize               Evaluation After AI Agent
    │                               [dead end — metrics only]
    ▼
AI Agent1 (Mistral Cloud)   Writes clinical summary (Epikrise) in German
                             using the pseudonymized text as input
    │
    ▼
C - De-pseudonymize         Restores real PII in the AI summary using
                             the reverse mapping
    │
    ▼
D - Evaluate                Detection + restoration quality metrics
    │
    ▼
Convert to HTML
    │
    ▼
Generate PDF (Gotenberg)
```

---

## Node Details

### Load Input

- Reads all `.json` files from `/home/node/.n8n-files/grascco_pii_2_json` (currently capped at 1 file per run).
- Strips BOM if present and parses UIMA CAS format.
- Extracts:
  - `sofaString` → document text
  - `DocumentMetaData.documentTitle` → `doc_id`
  - `webanno.custom.PHI` annotations → ground truth list `{type, value}`

---

### A — Detect (regex)

Rule-based extraction covering:

| PII Type   | Pattern                                                                     |
|------------|-----------------------------------------------------------------------------|
| DATE       | `dd.mm.yyyy`, `dd.mm.`, standalone years (19xx/20xx)                        |
| EMAIL      | Standard email pattern                                                      |
| PHONE      | Digit sequences 9–15 digits (date-like values excluded)                     |
| AGE        | Digits before `-jährigen/-jährige/-jähriger` or `Jahre alt`                 |
| PROFESSION | Words following `ist` / `arbeitet als`                                      |
| NAME       | Words after `Patient/Patientin/Frau/Herr`; doctor names before role titles  |

Outputs a deduplicated entity list passed downstream and also branched to **Evaluation After Regex**.

---

### AI Agent (Ollama — Mistral local)

- Model: `mistral:latest` via local Ollama instance.
- Prompt instructs extraction of every PII entity occurrence (including repeats) with these types: `NAME_PATIENT`, `NAME_DOCTOR`, `NAME_TITLE`, `DATE_BIRTH`, `DATE`, `ADDRESS`, `PHONE`, `EMAIL`, `ID_NUMBER`.
- Key rules enforced in the system prompt:
  - Copy values verbatim — no normalization.
  - Split titles from names (e.g., `Dr.med. Bernwart Schulze` → one `NAME_TITLE` + one `NAME_DOCTOR`).
  - Extract every occurrence separately, even repeating surnames.
  - Output strict JSON only — no markdown, no explanation.

---

### Merge Detections

Combines regex and LLM entity lists with three post-processing steps:

1. **Title splitting** — `Prof. Dr. Norbert Breuer` → `NAME_TITLE: Prof. Dr.` + `NAME: Norbert Breuer`.
2. **Anti-hallucination guard** — DATE entities whose digit sequence does not appear in the source text are discarded.
3. **Deduplication** — by type family (NAME/DATE/EMAIL/PHONE) × normalized value; substring matches treated as identical.

---

### Evaluation After Regex / Evaluation After AI Agent

Both evaluation nodes are identical in logic and run at different checkpoints (regex-only vs. after merge). They compute micro-averaged metrics against the ground truth PHI annotations:

| Metric    | Formula                        |
|-----------|--------------------------------|
| Precision | TP / (TP + FP)                 |
| Recall    | TP / (TP + FN)                 |
| F1        | 2 · P · R / (P + R)            |

Matching is **type-family aware** and uses **substring matching** (either string contains the other, min. 3 chars). Ground truth is deduplicated by type family + normalized value before comparison.

Both nodes are dead ends — they output metrics for inspection only and do not feed further into the pipeline.

---

### B — Pseudonymize

Replaces each detected entity with realistic fake data. Supports German (`de`), French (`fr`), and Spanish (`es`) name/address pools, selected via the document's `language` field.

| PII Type   | Strategy                                                    |
|------------|-------------------------------------------------------------|
| NAME       | Random first + last name from language pool                 |
| ADDRESS    | Random street + number + ZIP + city from language pool      |
| DATE       | **Random offset ±1–90 days** (randomized per entity)        |
| EMAIL      | `<firstname><2-digit-number>@example.org`                   |
| Other      | Digits replaced with random digits (structure preserved)    |

Replacements are applied longest-first to prevent partial substitutions. The mapping and reverse mapping are stored for the de-pseudonymization step.

---

### AI Agent1 (Mistral Cloud)

- Model: `mistral-vibe-cli-latest` via Mistral Cloud API.
- Receives the **pseudonymized text** as input.
- Produces a structured German clinical summary (Epikrise/Verlaufsbericht) in bullet points.
- Strict rules: no hallucination, copy all names/dates/ages verbatim, professional Fachsprache.

---

### C — De-pseudonymize

Restores real PII in the AI-generated summary by applying the inverse mapping (fake → original), longest-first. The output contains real patient data within the clinically structured summary.

---

### D — Evaluate

Evaluates both detection quality and restoration quality after de-pseudonymization:

- **Detection** — compares detected entities against ground truth (same P/R/F1 logic as earlier eval nodes).
- **Restoration** — checks whether original values from the mapping appear in the restored text (TP) and whether any fake values leaked through (FP).

---

### Convert to HTML / Generate PDF

- The restored text is converted to HTML (bold, bullet points, line breaks).
- Sent to **Gotenberg** (`http://gotenberg:3000`) via a Chromium HTML-to-PDF conversion call.
- Output is a binary PDF file.

---

## Key Design Decisions

**Randomized date offset** — Unlike a fixed +2-year shift, each date is shifted by a random number of days (±1–90). This makes dates non-deterministically reversible without access to the mapping.

**Two-stage detection** — Running regex and LLM in parallel and merging results improves recall. The regex catches structural patterns (dates, phones) that the LLM may normalize; the LLM catches semantic entities (names, roles) that regex misses.

**Pseudonymize → LLM → De-pseudonymize pattern** — The external Mistral Cloud model never sees real patient data. PII is masked before the API call and restored afterwards using the local mapping.

**Two evaluation checkpoints** — Metrics are captured after regex-only and after the full merge, making it possible to quantify the LLM's contribution to recall independently.

---

## Known Limitations

1. **Single-file cap** — `files.slice(0, 1)` in Load Input means only one document is processed per run. Remove the slice to enable batch processing.

2. **Regex name detection is narrow** — Only captures names that follow explicit salutations (`Frau`, `Herr`, `Patient`) or precede role titles. Names in free-text sentences are missed without the LLM.

3. **Mistral Cloud receives pseudonymized text** — The summarization quality depends on the pseudonymization being complete. Any leaked PII in the pseudonymized text is sent to the external API.

4. **Evaluation nodes are dead ends** — The metrics from "Evaluation After Regex" and "Evaluation After AI Agent" are not persisted or surfaced in the final output. They require manual inspection of n8n execution logs.

5. **Language detection is static** — The `language` field must be set in the input JSON. There is no automatic language detection; the German pool is used as fallback.
