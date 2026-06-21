import json
import logging
import os
import random
import re
import time
from typing import Any, Dict, List, Optional, TypedDict

import httpx
from langgraph.graph import END, StateGraph

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("pipeline")

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
EXTERNAL_LLM_BASE_URL = os.getenv("EXTERNAL_LLM_BASE_URL", "http://ollama:11434/v1")
EXTERNAL_LLM_API_KEY = os.getenv("EXTERNAL_LLM_API_KEY", "ollama")
EXTERNAL_LLM_MODEL = os.getenv("EXTERNAL_LLM_MODEL", "mistral")

log.info("LangGraph pipeline loaded | ollama=%s | external=%s | model=%s",
         OLLAMA_BASE_URL, EXTERNAL_LLM_BASE_URL, EXTERNAL_LLM_MODEL)


# ── State ─────────────────────────────────────────────────────────────────────

class PseudonymizerState(TypedDict):
    # Inputs
    document_id: str
    prompt_type: str
    original_text: str
    pii_section: str
    custom_prompt: Optional[str]
    ground_truth: Optional[list]
    # Node outputs
    pii_entities: List[dict]
    mapping: Dict[str, str]          # original → fake
    reverse_mapping: Dict[str, str]  # fake → original
    pseudonymized_text: str
    filled_prompt: str
    raw_llm_output: str
    restored_analysis: str
    restored_tokens: List[str]
    evaluation: Dict[str, Any]
    # Control
    retry_count: int
    error: Optional[str]


# ── Node 1: Extract PII via local Ollama ──────────────────────────────────────

def extract_pii(state: PseudonymizerState) -> dict:
    retry = state.get("retry_count", 0)
    log.info("[1] extract_pii start | doc=%s | attempt=%d | chars=%d",
             state["document_id"], retry + 1, len(state.get("pii_section", "")))
    payload = {
        "model": "mistral",
        "stream": False,
        "options": {"num_predict": 512, "temperature": 0},
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a PII extraction specialist. "
                    "Return ONLY a valid JSON array. No explanation, no markdown, just the array."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Extract all PII from the text below. Return ONLY a JSON array like:\n"
                    '[{"entity":"Hans Mueller","type":"NAME"},{"entity":"14.03.1958","type":"DATE"}]\n\n'
                    "Types: NAME (patient and physician names), ADDRESS, DATE (all dates), "
                    "ID_NUMBER (insurance/patient IDs).\n\n"
                    f"Text:\n{state['pii_section']}"
                ),
            },
        ],
    }
    try:
        with httpx.Client(timeout=300.0) as client:
            resp = client.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload)
            resp.raise_for_status()
            result = resp.json()

        content = (
            result.get("message", {}).get("content")
            or (result.get("choices") or [{}])[0].get("message", {}).get("content")
            or ""
        )
        entities = []
        m = re.search(r"\[[\s\S]*?\]", content)
        if m:
            parsed = json.loads(m.group(0))
            if isinstance(parsed, list):
                seen: set = set()
                for e in parsed:
                    if e.get("entity") and e.get("type") and e["entity"] not in seen:
                        entities.append(e)
                        seen.add(e["entity"])
        log.info("[1] extract_pii done | entities=%d | values=%s",
                 len(entities), [e["entity"] for e in entities])
        return {"pii_entities": entities, "error": None}
    except Exception as exc:
        log.error("[1] extract_pii failed | %s", exc)
        return {"pii_entities": [], "error": f"PII extraction failed: {exc}", "retry_count": state.get("retry_count", 0) + 1}


def should_retry_pii(state: PseudonymizerState) -> str:
    """Retry once if Ollama returned no entities and we haven't already retried."""
    if not state.get("pii_entities") and (state.get("retry_count", 0) < 1):
        log.warning("[1] extract_pii returned no entities — retrying once")
        return "extract_pii"
    return "pseudonymize"


# ── Node 2: Pseudonymize ──────────────────────────────────────────────────────

def pseudonymize(state: PseudonymizerState) -> dict:
    log.info("[2] pseudonymize start | entities=%d", len(state.get("pii_entities", [])))
    text = state["original_text"]
    entities = state.get("pii_entities", [])

    fake_first_names = ["Erik","Anna","Klaus","Maria","Thomas","Lisa","Stefan","Julia","Michael","Sandra","Peter","Claudia","Jürgen","Heike","Ralf"]
    fake_last_names  = ["Bauer","Schmidt","Weber","Fischer","Wagner","Maier","Schulz","Koch","Richter","Klein","Wolff","Neuer","Gruber","Haas","Berger"]
    fake_titles      = ["Dr.", "Prof. Dr.", "Dr. med."]
    fake_streets     = ["Gartenweg","Birkenstraße","Lindenallee","Rosenweg","Kirchstraße","Bergstraße","Waldweg","Schulstraße","Bahnhofstraße","Parkweg"]
    fake_cities      = ["Musterstadt","Neuburg an der Donau","Kleinbach","Waldheim","Bergdorf","Neustadt","Altenberg"]
    fake_zip_pfx     = ["10","20","30","40","50","60","70","80","90"]

    used_last: List[str] = []
    used_streets: List[str] = []
    counter = [0]

    def unused(pool: list, used: list) -> str:
        avail = [x for x in pool if x not in used]
        return random.choice(avail) if avail else random.choice(pool) + f"_{len(used)}"

    def fake_name(has_title: bool) -> str:
        last = unused(fake_last_names, used_last); used_last.append(last)
        first = random.choice(fake_first_names)
        return f"{random.choice(fake_titles)} {first} {last}" if has_title else f"{first} {last}"

    def fake_address() -> str:
        street = unused(fake_streets, used_streets); used_streets.append(street)
        zip_ = random.choice(fake_zip_pfx) + str(random.randint(0, 999)).zfill(3)
        return f"{street} {random.randint(1,80)}, {zip_} {random.choice(fake_cities)}"

    def shift_date(d: str) -> str:
        return re.sub(r"(\d{4})", lambda m: str(int(m.group(1)) + 2) if 1900 <= int(m.group(1)) <= 2100 else m.group(1), d)

    def fake_id() -> str:
        return random.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ") + str(random.randint(100_000_000, 999_999_999))

    mapping: Dict[str, str] = {}
    reverse_mapping: Dict[str, str] = {}

    for e in sorted(entities, key=lambda x: len(x["entity"]), reverse=True):
        ent, etype = e["entity"], e["type"]
        if ent in mapping:
            continue
        if etype == "NAME":
            fake = fake_name(ent.startswith("Dr.") or ent.startswith("Prof."))
        elif etype == "ADDRESS":
            fake = fake_address()
        elif etype == "DATE":
            fake = shift_date(ent)
        elif etype == "ID_NUMBER":
            fake = fake_id()
        elif etype == "PHONE":
            fake = f"+49 {random.randint(100,999)} {random.randint(1_000_000,9_999_999)}"
        elif etype == "EMAIL":
            counter[0] += 1
            fake = f"patient{counter[0]}.anon@klinik-example.de"
        else:
            counter[0] += 1
            fake = f"[{etype}_{counter[0]}]"
        mapping[ent] = fake
        reverse_mapping[fake] = ent

    pseudonymized = text
    for orig in sorted(mapping, key=len, reverse=True):
        pseudonymized = pseudonymized.replace(orig, mapping[orig])

    log.info("[2] pseudonymize done | replacements=%d | mapping=%s", len(mapping), list(mapping.items())[:3])
    return {"mapping": mapping, "reverse_mapping": reverse_mapping, "pseudonymized_text": pseudonymized}


# ── Node 3: Prepare Prompt ────────────────────────────────────────────────────

PROMPT_TEMPLATES = {
    "clinical_summary": """You are a clinical documentation assistant. Your task is to produce a structured medical summary from the patient document provided below.

Your summary must include the following sections:
1. **Patient Overview** – age, gender, relevant demographics
2. **Chief Complaint** – the primary reason for the visit or admission
3. **Medical History** – relevant past diagnoses, surgeries, allergies
4. **Current Medications** – list all medications with dosage if mentioned
5. **Key Clinical Findings** – vital signs, lab results, imaging findings
6. **Assessment** – clinical impression or working diagnosis
7. **Plan & Follow-up** – recommended next steps, referrals, monitoring

Be concise, factual, and use standard medical terminology.

--- DOCUMENT START ---
{document}
--- DOCUMENT END ---""",

    "diagnosis_support": """You are a clinical decision support assistant. Based on the patient document provided below, perform a systematic diagnostic analysis.

Your response must include:
1. **Differential Diagnoses** – list at least 3 possible diagnoses ranked by likelihood with rationale
2. **Supporting Evidence** – key symptoms, signs, or test results
3. **Recommended Diagnostic Workup** – specific labs, imaging, or specialist referrals
4. **Red Flags** – features requiring urgent attention
5. **Clinical Reasoning** – brief explanation of your diagnostic approach

--- DOCUMENT START ---
{document}
--- DOCUMENT END ---""",

    "icd_coding": """You are a certified medical coding specialist. Analyze the clinical document below and assign accurate ICD-10-GM codes.

Your response must include:
1. **Primary Diagnosis Code** – ICD-10-GM code + description + rationale
2. **Secondary Diagnosis Codes** – comorbidities and complications
3. **Procedure Codes (OPS)** – if any procedures are documented
4. **Coding Notes** – ambiguities or physician queries
5. **DRG Grouping Hint** – likely DRG category

--- DOCUMENT START ---
{document}
--- DOCUMENT END ---""",
}


def prepare_prompt(state: PseudonymizerState) -> dict:
    log.info("[3] prepare_prompt | type=%s | custom=%s", state.get("prompt_type"), bool(state.get("custom_prompt")))
    doc = state["pseudonymized_text"]
    if state.get("custom_prompt"):
        prompt = state["custom_prompt"].replace("{document}", doc)
    else:
        template = PROMPT_TEMPLATES.get(state["prompt_type"], PROMPT_TEMPLATES["clinical_summary"])
        prompt = template.replace("{document}", doc)
    log.info("[3] prepare_prompt done | prompt_chars=%d", len(prompt))
    return {"filled_prompt": prompt}


# ── Node 4: Analyze via External LLM ─────────────────────────────────────────

def analyze(state: PseudonymizerState) -> dict:
    payload = {
        "model": EXTERNAL_LLM_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a highly experienced medical professional assistant. "
                    "Provide accurate, structured, and evidence-based analysis. "
                    "Never reveal or guess patient identities."
                ),
            },
            {"role": "user", "content": state["filled_prompt"]},
        ],
        "temperature": 0.3,
        "max_tokens": 800,
    }
    headers = {
        "Authorization": f"Bearer {EXTERNAL_LLM_API_KEY}",
        "Content-Type": "application/json",
    }
    log.info("[4] analyze start | model=%s | url=%s | prompt_chars=%d",
             EXTERNAL_LLM_MODEL, EXTERNAL_LLM_BASE_URL, len(state.get("filled_prompt", "")))
    for attempt in range(4):
        try:
            with httpx.Client(timeout=600.0) as client:
                resp = client.post(f"{EXTERNAL_LLM_BASE_URL}/chat/completions", json=payload, headers=headers)
                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", 10)) + 2
                    log.warning("[4] analyze 429 rate limit | attempt=%d | waiting=%ds", attempt + 1, retry_after)
                    time.sleep(retry_after)
                    continue
                resp.raise_for_status()
                result = resp.json()
            content = result["choices"][0]["message"]["content"]
            log.info("[4] analyze done | response_chars=%d", len(content))
            return {"raw_llm_output": content, "error": None}
        except httpx.HTTPStatusError as exc:
            log.error("[4] analyze HTTP error | attempt=%d | status=%s", attempt + 1, exc.response.status_code)
            if attempt == 3:
                return {"raw_llm_output": "", "error": "LLM analysis failed after retries: rate limit"}
        except Exception as exc:
            log.error("[4] analyze exception | %s", exc)
            return {"raw_llm_output": "", "error": f"LLM analysis failed: {exc}"}
    return {"raw_llm_output": "", "error": "LLM analysis failed: rate limit exhausted"}


# ── Node 5: De-pseudonymize ───────────────────────────────────────────────────

def depseudonymize(state: PseudonymizerState) -> dict:
    log.info("[5] depseudonymize start | reverse_map_size=%d", len(state.get("reverse_mapping", {})))
    analysis = state.get("raw_llm_output", "")
    reverse_mapping = state.get("reverse_mapping", {})

    restored = analysis
    restored_tokens: List[str] = []

    for fake in sorted(reverse_mapping, key=len, reverse=True):
        if fake in restored:
            restored = restored.replace(fake, reverse_mapping[fake])
            restored_tokens.append(fake)

    log.info("[5] depseudonymize done | restored_tokens=%d", len(restored_tokens))
    return {"restored_analysis": restored, "restored_tokens": restored_tokens}


# ── Node 6: Evaluate ──────────────────────────────────────────────────────────

def evaluate(state: PseudonymizerState) -> dict:
    log.info("[6] evaluate start | mapping_size=%d", len(state.get("mapping", {})))
    mapping = state.get("mapping", {})
    raw_output = state.get("raw_llm_output", "")
    restored = state.get("restored_analysis", "")

    true_positives, false_negatives, not_in_output = [], [], []
    residual_fakes = [f for f in mapping.values() if f in restored]

    for original, fake in mapping.items():
        if fake in raw_output:
            if original in restored:
                true_positives.append({"original": original, "fake": fake, "status": "correctly_restored"})
            else:
                false_negatives.append({"original": original, "fake": fake, "status": "missed_in_restoration"})
        else:
            not_in_output.append({"original": original, "fake": fake, "status": "not_in_llm_output"})

    tp = len(true_positives)
    fn = len(false_negatives)
    fp = len(residual_fakes)
    appeared = tp + fn

    precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
    recall    = tp / appeared  if appeared > 0      else 1.0
    f1        = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    evaluation = {
        "document_id": state["document_id"],
        "total_pii_mapped": len(mapping),
        "appeared_in_llm_output": appeared,
        "true_positives": tp,
        "false_negatives": fn,
        "residual_fakes_in_output": fp,
        "precision": round(precision * 10000) / 100,
        "recall":    round(recall    * 10000) / 100,
        "f1_score":  round(f1        * 10000) / 100,
        "correctly_restored": true_positives,
        "missed": false_negatives,
        "not_referenced_by_llm": not_in_output,
        "residual_fakes": residual_fakes,
    }
    log.info("[6] evaluate done | precision=%.1f%% recall=%.1f%% f1=%.1f%% tp=%d fn=%d",
             evaluation["precision"], evaluation["recall"], evaluation["f1_score"], tp, fn)
    return {"evaluation": evaluation}


# ── Build & compile graph ─────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    g = StateGraph(PseudonymizerState)

    g.add_node("extract_pii",    extract_pii)
    g.add_node("pseudonymize",   pseudonymize)
    g.add_node("prepare_prompt", prepare_prompt)
    g.add_node("analyze",        analyze)
    g.add_node("depseudonymize", depseudonymize)
    g.add_node("evaluate",       evaluate)

    g.set_entry_point("extract_pii")

    # Retry once if Ollama returns empty entity list
    g.add_conditional_edges("extract_pii", should_retry_pii, {
        "extract_pii": "extract_pii",
        "pseudonymize": "pseudonymize",
    })

    g.add_edge("pseudonymize",   "prepare_prompt")
    g.add_edge("prepare_prompt", "analyze")
    g.add_edge("analyze",        "depseudonymize")
    g.add_edge("depseudonymize", "evaluate")
    g.add_edge("evaluate",       END)

    return g.compile()


pipeline = build_graph()
