import json
import logging
import os
import random
import re
from typing import Any, Dict, List, Optional, TypedDict

import httpx
from langgraph.graph import END, StateGraph

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("pseudonymization-stage")

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")


# ── State ──────────────────────────────────────────────────────────────────────

class PseudonymizationState(TypedDict):
    # Inputs
    document_id: str
    original_text: str
    pii_section: str
    ground_truth: Optional[list]  # list of {"entity": ..., "type": ...}
    # Node outputs
    pii_entities: List[dict]
    mapping: Dict[str, str]          # original → fake
    reverse_mapping: Dict[str, str]  # fake → original
    pseudonymized_text: str
    # Evaluation outputs
    pii_detection_eval: Dict[str, Any]   # FP / FN / TP vs ground truth
    context_leakage: Dict[str, Any]      # real PII still visible after pseudonymization
    irreversibility: Dict[str, Any]      # risk analysis: can attacker reverse without mapping?
    # Control
    retry_count: int
    error: Optional[str]


# ── Node 1: Extract PII via local Ollama ──────────────────────────────────────

def extract_pii(state: PseudonymizationState) -> dict:
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
        return {
            "pii_entities": [],
            "error": f"PII extraction failed: {exc}",
            "retry_count": state.get("retry_count", 0) + 1,
        }


def should_retry_pii(state: PseudonymizationState) -> str:
    if not state.get("pii_entities") and (state.get("retry_count", 0) < 1):
        log.warning("[1] extract_pii returned no entities — retrying once")
        return "extract_pii"
    return "pseudonymize"


# ── Node 2: Pseudonymize ──────────────────────────────────────────────────────

def pseudonymize(state: PseudonymizationState) -> dict:
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

    log.info("[2] pseudonymize done | replacements=%d", len(mapping))
    return {
        "mapping": mapping,
        "reverse_mapping": reverse_mapping,
        "pseudonymized_text": pseudonymized,
    }


# ── Node 3: Evaluate pseudonymization quality ─────────────────────────────────
#
# This node answers three questions WITHOUT involving the external LLM:
#
#   A) PII Detection Quality  — did the extractor catch everything?
#      Requires ground_truth to be provided. Computes TP / FP / FN,
#      precision, recall and F1 against the reference entity list.
#
#   B) Context Leakage        — does real PII still appear in the output?
#      Scans the pseudonymized text for any original entity string.
#      Even one hit means the substitution missed that occurrence.
#
#   C) Irreversibility Risk   — how hard is it for an attacker to undo?
#      DATE entities use a fixed +2 year offset → HIGH risk (deterministic).
#      NAME / ADDRESS / ID_NUMBER use random pools → LOW risk.
#      Any recognisable structural pattern (e.g. letter + 9 digits for IDs)
#      that overlaps a known national format is flagged as MEDIUM risk.

def evaluate_pseudonymization(state: PseudonymizationState) -> dict:
    log.info("[3] evaluate_pseudonymization start | doc=%s", state["document_id"])

    detected  = state.get("pii_entities", [])
    mapping   = state.get("mapping", {})
    pseudo    = state.get("pseudonymized_text", "")
    original  = state.get("original_text", "")
    gt        = state.get("ground_truth") or []

    detected_values = {e["entity"] for e in detected}
    detected_lookup = {e["entity"]: e["type"] for e in detected}

    # ── A: PII Detection Quality ──────────────────────────────────────────────

    if gt:
        gt_values = {e["entity"] for e in gt}

        tp_entities = [e for e in detected if e["entity"] in gt_values]
        fp_entities = [e for e in detected if e["entity"] not in gt_values]
        fn_entities = [e for e in gt     if e["entity"] not in detected_values]

        tp = len(tp_entities)
        fp = len(fp_entities)
        fn = len(fn_entities)

        precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
        recall    = tp / (tp + fn) if (tp + fn) > 0 else 1.0
        f1        = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

        pii_detection_eval = {
            "ground_truth_count": len(gt),
            "detected_count": len(detected),
            "true_positives": tp,
            "false_positives": fp,
            "false_negatives": fn,
            "precision": round(precision * 100, 2),
            "recall":    round(recall * 100, 2),
            "f1_score":  round(f1 * 100, 2),
            "fp_entities": [e["entity"] for e in fp_entities],
            "fn_entities": [e["entity"] for e in fn_entities],
            "tp_entities": [e["entity"] for e in tp_entities],
        }
    else:
        # No ground truth — report coverage as unknown
        pii_detection_eval = {
            "ground_truth_count": 0,
            "detected_count": len(detected),
            "true_positives": None,
            "false_positives": None,
            "false_negatives": None,
            "precision": None,
            "recall": None,
            "f1_score": None,
            "note": "No ground truth provided — FP/FN cannot be computed",
            "detected_entities": [e["entity"] for e in detected],
        }

    # ── B: Context Leakage ───────────────────────────────────────────────────
    # Check every known original entity (detected + ground truth) against
    # the pseudonymized text.

    all_originals = set(detected_values)
    for e in gt:
        all_originals.add(e["entity"])

    leaked = [orig for orig in all_originals if orig in pseudo]

    context_leakage = {
        "leakage_free": len(leaked) == 0,
        "leaked_count": len(leaked),
        "leaked_entities": leaked,
        "verdict": "CLEAN" if not leaked else "PII_LEAKED",
        "note": (
            "All detected PII has been replaced in the output."
            if not leaked
            else f"{len(leaked)} PII value(s) still appear verbatim in the pseudonymized text."
        ),
    }

    # ── C: Irreversibility Risk ───────────────────────────────────────────────
    risks = []
    for orig, fake in mapping.items():
        etype = detected_lookup.get(orig, "UNKNOWN")

        if etype == "DATE":
            risks.append({
                "entity": orig,
                "fake": fake,
                "type": etype,
                "risk_level": "HIGH",
                "reason": (
                    "Date shifted by a fixed +2-year offset. "
                    "An attacker who knows or guesses the shift can recover all dates exactly."
                ),
            })
        elif etype == "ID_NUMBER":
            # German insurance numbers: letter + 9 digits — same pattern kept
            if re.match(r"^[A-Z]\d{9}$", fake):
                risks.append({
                    "entity": orig,
                    "fake": fake,
                    "type": etype,
                    "risk_level": "MEDIUM",
                    "reason": (
                        "Fake ID preserves the structural pattern (letter + 9 digits). "
                        "An attacker may recognise this format and attempt enumeration."
                    ),
                })
            else:
                risks.append({
                    "entity": orig,
                    "fake": fake,
                    "type": etype,
                    "risk_level": "LOW",
                    "reason": "Random ID — not deterministically reversible.",
                })
        elif etype == "NAME":
            risks.append({
                "entity": orig,
                "fake": fake,
                "type": etype,
                "risk_level": "LOW",
                "reason": (
                    "Replaced with a random German name from a fixed pool. "
                    "Not deterministically reversible, but a small pool (~225 combinations) "
                    "could theoretically be brute-forced if pool is known."
                ),
            })
        elif etype == "ADDRESS":
            risks.append({
                "entity": orig,
                "fake": fake,
                "type": etype,
                "risk_level": "LOW",
                "reason": "Random address generated from fixed street/city pools — not reversible.",
            })
        else:
            risks.append({
                "entity": orig,
                "fake": fake,
                "type": etype,
                "risk_level": "LOW",
                "reason": "Generic token replacement — not deterministically reversible.",
            })

    high_risk   = [r for r in risks if r["risk_level"] == "HIGH"]
    medium_risk = [r for r in risks if r["risk_level"] == "MEDIUM"]

    irreversibility = {
        "total_entities": len(risks),
        "high_risk_count": len(high_risk),
        "medium_risk_count": len(medium_risk),
        "low_risk_count": len(risks) - len(high_risk) - len(medium_risk),
        "verdict": (
            "REVERSIBLE_RISK"   if high_risk else
            "PARTIAL_RISK"      if medium_risk else
            "IRREVERSIBLE"
        ),
        "risks": risks,
        "high_risk_entities": [r["entity"] for r in high_risk],
        "medium_risk_entities": [r["entity"] for r in medium_risk],
        "note": (
            "DATE entities use a deterministic +2-year shift and are recoverable by an attacker "
            "who knows the strategy." if high_risk else
            "No high-risk reversibility issues found."
        ),
    }

    log.info(
        "[3] evaluate done | detection=precision:%.1f%% recall:%.1f%% | leakage=%s | reversibility=%s",
        pii_detection_eval.get("precision") or 0,
        pii_detection_eval.get("recall") or 0,
        context_leakage["verdict"],
        irreversibility["verdict"],
    )

    return {
        "pii_detection_eval": pii_detection_eval,
        "context_leakage": context_leakage,
        "irreversibility": irreversibility,
    }


# ── Build & compile graph ─────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    g = StateGraph(PseudonymizationState)

    g.add_node("extract_pii",               extract_pii)
    g.add_node("pseudonymize",              pseudonymize)
    g.add_node("evaluate_pseudonymization", evaluate_pseudonymization)

    g.set_entry_point("extract_pii")

    g.add_conditional_edges("extract_pii", should_retry_pii, {
        "extract_pii":  "extract_pii",
        "pseudonymize": "pseudonymize",
    })

    g.add_edge("pseudonymize",              "evaluate_pseudonymization")
    g.add_edge("evaluate_pseudonymization", END)

    return g.compile()


pipeline = build_graph()
