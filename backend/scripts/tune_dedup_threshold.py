"""Empirical tuning of DEFAULT_CLUSTER_DISTANCE_THRESHOLD (dedup.py,
Section 2 section 5's open question).

Methodology (per user request): generate a real batch of questions from
real Section 1 chunks, construct verified near-duplicate pairs (LLM
paraphrases of a subset — ground truth by construction, since we know
exactly which question each paraphrase came from) and known-distinct
pairs (different facts/topics, including the harder same-topic-different-
sub-fact case), compute cosine similarity for every pair using the exact
same embedding model/field as production (sentence-transformers
all-MiniLM-L6-v2, encoding just the question text), and report where the
two distributions separate.

Run: .venv/Scripts/python scripts/tune_dedup_threshold.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from sentence_transformers import SentenceTransformer

from app.generation import get_grounding_chunks
from app.llm_client import OpenAIProvider, generate_with_reliability
from app.prompts import build_fill_in_blank_prompt, build_mcq_prompt, parse_and_validate

TOPICS = [
    ("10", "Science", "CHEMICAL EQUATIONS"),
    ("10", "Science", "TYPES OF CHEMICAL REACTIONS"),
    ("10", "Science", "HAVE YOU OBSERVED THE EFFECTS OF OXIDATION REACTIONS IN EVERYDAY LIFE?"),
    ("6", "Maths", "Patterns in Numbers"),
]

PARAPHRASE_PROMPT = """Reword the following question so it tests the EXACT SAME fact, in different wording — a genuine paraphrase a student would recognize as asking the same thing, not a different question. Respond with ONLY the reworded question text, nothing else.

ORIGINAL QUESTION:
{question}"""


def cosine_sim(a, b) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    provider = OpenAIProvider()
    embed_model = SentenceTransformer("all-MiniLM-L6-v2")

    # --- Step 1: generate a real batch of questions across diverse topics ---
    originals = []  # [{"text":..., "type":..., "topic":...}]
    for class_, subject, topic in TOPICS:
        chunks = get_grounding_chunks(class_, subject, topic)
        if not chunks:
            print(f"WARNING: no chunks for {class_}/{subject}/{topic}, skipping")
            continue
        chunk = chunks[0]
        for builder, qtype in [(build_mcq_prompt, "MCQ"), (build_fill_in_blank_prompt, "fill_in_blank")]:
            for difficulty in ["easy", "medium", "hard", "medium"]:  # 4 per (topic, type) = 32 total across 4 topics x 2 types
                prompt = builder(chunk["text"], difficulty)
                raw = generate_with_reliability(prompt, primary=provider, max_retries=1, max_tokens=400)
                try:
                    payload = parse_and_validate(qtype, raw)
                    originals.append({"text": payload.question, "type": qtype, "topic": topic})
                except Exception as e:
                    print(f"  (skipped one malformed generation: {e})")

    print(f"\n{len(originals)} original questions generated\n")
    for o in originals:
        print(f"  [{o['topic'][:30]:<30}][{o['type']:<13}] {o['text'][:80]}")

    # --- Step 2: create verified near-duplicate pairs via LLM paraphrase ---
    paraphrase_subset = originals[: min(20, len(originals))]
    near_dup_pairs = []  # (text_a, text_b, topic)
    for o in paraphrase_subset:
        prompt = PARAPHRASE_PROMPT.format(question=o["text"])
        paraphrase = generate_with_reliability(prompt, primary=provider, max_retries=1, max_tokens=200).strip()
        near_dup_pairs.append((o["text"], paraphrase, o["topic"]))

    # --- Step 3: known-distinct pairs -- different facts, including the
    # harder same-topic-different-sub-fact case, from the ALREADY-generated
    # originals (no extra API calls needed) ---
    distinct_pairs = []
    for i in range(len(originals)):
        for j in range(i + 1, len(originals)):
            a, b = originals[i], originals[j]
            if a["text"] == b["text"]:
                continue
            distinct_pairs.append((a["text"], b["text"], a["topic"] == b["topic"]))

    # --- Step 4: embed (same model + field as production: question text only) ---
    print(f"\nEmbedding {len(near_dup_pairs)} near-dup pairs and {len(distinct_pairs)} distinct pairs...")

    near_dup_sims = []
    for a, b, topic in near_dup_pairs:
        ea, eb = embed_model.encode([a, b])
        near_dup_sims.append(cosine_sim(ea, eb))

    distinct_sims_same_topic = []
    distinct_sims_diff_topic = []
    for a, b, same_topic in distinct_pairs:
        ea, eb = embed_model.encode([a, b])
        sim = cosine_sim(ea, eb)
        (distinct_sims_same_topic if same_topic else distinct_sims_diff_topic).append(sim)

    # --- Step 5: report distributions and the gap ---
    def stats(name, values):
        if not values:
            print(f"{name}: (no pairs)")
            return
        arr = np.array(values)
        print(f"{name}: n={len(arr)}  min={arr.min():.3f}  p25={np.percentile(arr,25):.3f}  "
              f"median={np.median(arr):.3f}  p75={np.percentile(arr,75):.3f}  max={arr.max():.3f}")

    print("\n=== Cosine SIMILARITY distributions ===")
    stats("Near-duplicate pairs (verified paraphrases)", near_dup_sims)
    stats("Distinct pairs, SAME topic (harder case)    ", distinct_sims_same_topic)
    stats("Distinct pairs, DIFFERENT topic (easier case)", distinct_sims_diff_topic)

    all_distinct = distinct_sims_same_topic + distinct_sims_diff_topic
    if near_dup_sims and all_distinct:
        min_near_dup = min(near_dup_sims)
        max_distinct = max(all_distinct)
        print(f"\nLowest near-duplicate similarity:  {min_near_dup:.3f}")
        print(f"Highest distinct-pair similarity:  {max_distinct:.3f}")
        if min_near_dup > max_distinct:
            gap_mid = (min_near_dup + max_distinct) / 2
            print(f"CLEAN SEPARATION — gap: [{max_distinct:.3f}, {min_near_dup:.3f}]")
            print(f"Recommended similarity threshold (gap midpoint): {gap_mid:.3f}")
            print(f"=> DEFAULT_CLUSTER_DISTANCE_THRESHOLD = {1 - gap_mid:.3f}")
        else:
            print("OVERLAP — no clean gap; showing sorted values for manual inspection:")
            print("  near-dup sims:", sorted(round(s, 3) for s in near_dup_sims))
            print("  distinct sims:", sorted(round(s, 3) for s in all_distinct))

    # dump raw pairs for manual spot-checking
    print("\n=== Near-duplicate pairs (lowest similarity first) ===")
    for (a, b, topic), sim in sorted(zip(near_dup_pairs, near_dup_sims), key=lambda x: x[1]):
        print(f"  sim={sim:.3f}  [{topic[:20]}]")
        print(f"    A: {a[:90]}")
        print(f"    B: {b[:90]}")

    print("\n=== Distinct pairs (highest similarity first, top 10) ===")
    combined = []
    si, di = 0, 0
    for a, b, same_topic in distinct_pairs:
        if same_topic:
            combined.append((a, b, same_topic, distinct_sims_same_topic[si]))
            si += 1
        else:
            combined.append((a, b, same_topic, distinct_sims_diff_topic[di]))
            di += 1
    for a, b, same_topic, sim in sorted(combined, key=lambda x: -x[3])[:10]:
        print(f"  sim={sim:.3f}  same_topic={same_topic}")
        print(f"    A: {a[:90]}")
        print(f"    B: {b[:90]}")


if __name__ == "__main__":
    main()
