#!/usr/bin/env python3
"""Embeddings retrieval and separation test, exactly as frozen in
prereg-embeddings-separation.md (block-12 folder). Frozen 2026-10-14T08:42:33Z.

Real run (one run, one model, no tuning):
    uv run --with sentence-transformers python research/scripts/emb_separation.py FORK_DIR --write

Pipeline check only (hashed bag-of-words vectors, NOT evidence, writes nothing):
    uv run python research/scripts/emb_separation.py FORK_DIR --embedder stub

Test R: rank of the true target in the issue's time-ordered pool, 12 pairs (8 true + 4 known).
Test S: AUC of cosine, 8 true pairs vs 16 clear negatives (neither rater D). Split pairs reported apart.
Text per issue: "{title}. {body[:1500]}", title as at filing. TF-IDF baseline is dup_eval.tfidf_top.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dup_eval as de  # noqa: E402

MODEL = "all-MiniLM-L6-v2"  # frozen
BODY_CHARS = 1500  # frozen
AUC_PASS = 0.80  # frozen
R_PASS_K, R_PASS_MIN = 20, 11  # frozen: at least 11 of 12 in the top 20, and >= TF-IDF


def text_of(v: dict) -> str:
    return f"{v['title']}. {(v['body'] or '')[:BODY_CHARS]}"


def embed_real(texts):
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(MODEL)
    return np.asarray(model.encode(texts, normalize_embeddings=True, batch_size=64, show_progress_bar=True))


def embed_stub(texts):
    dim = 256
    out = np.zeros((len(texts), dim), dtype="float32")
    for i, t in enumerate(texts):
        for tok in de.tokens(t):
            out[i, int(hashlib.md5(tok.encode()).hexdigest(), 16) % dim] += 1
    n = np.linalg.norm(out, axis=1, keepdims=True)
    n[n == 0] = 1
    return out / n


def auc(pos, neg):
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("fork_dir")
    ap.add_argument("--embedder", choices=["real", "stub"], default="real")
    ap.add_argument("--write", action="store_true", help="write results files (real embedder only)")
    args = ap.parse_args()
    if args.write and args.embedder != "real":
        sys.exit("--write is only allowed with the real embedder; the stub is not evidence")

    fd = Path(args.fork_dir)
    d = fd / "dup-eval-2"
    issues, renames, _ = de.load(fd)
    by_num = {i["number"]: i for i in issues}
    key = json.loads((d / "rating-key.json").read_text())

    def read(name):
        return {r["pair_id"]: (r["rating"] or "?").strip().upper() for r in csv.DictReader((d / name).open())}

    C, A = read("ratings-claude.csv"), read("ratings-adam.csv")
    true_p, split_p, neg_p = [], [], []
    for p, k in key.items():
        if C[p] == "?" or A[p] == "?":
            continue
        pair = (p, k["issue"], k["target"])
        if C[p] == "D" and A[p] == "D":
            true_p.append(pair)
        elif C[p] == "D" or A[p] == "D":
            split_p.append(pair)
        else:
            neg_p.append(pair)
    counts = (len(true_p), len(split_p), len(neg_p))
    if counts != (8, 10, 16):
        sys.exit(f"rating data does not match the frozen counts (8, 10, 16): got {counts}")
    known = [(f"K{c}", c, o) for c, o in de.DUP_PAIRS.items()]

    # One embedding per issue, from the same filing-time view the TF-IDF baseline uses.
    nums = sorted(by_num)
    views = {n: de.view(by_num[n], renames, by_num[n]["created_at"]) for n in nums}
    texts = [text_of(views[n]) for n in nums]
    embed = embed_real if args.embedder == "real" else embed_stub
    mat = embed(texts)
    row = {n: i for i, n in enumerate(nums)}

    def rank_pair(issue, target):
        at = by_num[issue]["created_at"]
        pool = [de.view(i, renames, at) for i in issues if i["created_at"] < at and i["number"] != issue]
        pool_nums = [p["number"] for p in pool]
        if target not in pool_nums:
            return None, None, len(pool)
        scores = mat[[row[n] for n in pool_nums]] @ mat[row[issue]]
        st = scores[pool_nums.index(target)]
        emb_rank = int(1 + (scores > st).sum())
        order = [p["number"] for p in de.tfidf_top(views[issue] | {"number": issue}, pool, len(pool))]
        return emb_rank, order.index(target) + 1, len(pool)

    r_pairs = true_p + known
    r_rows = []
    for pid, i, t in r_pairs:
        e, f, n = rank_pair(i, t)
        r_rows.append({"pair": pid, "issue": i, "target": t, "pool": n, "emb_rank": e, "tfidf_rank": f})

    def recall(field, k):
        return sum(1 for r in r_rows if r[field] is not None and r[field] <= k)

    cos = lambda i, t: float(mat[row[i]] @ mat[row[t]])
    s_true = [cos(i, t) for _, i, t in true_p]
    s_neg = [cos(i, t) for _, i, t in neg_p]
    s_split = [cos(i, t) for _, i, t in split_p]
    a = auc(s_true, s_neg)

    e20, f20 = recall("emb_rank", R_PASS_K), recall("tfidf_rank", R_PASS_K)
    r_pass = e20 >= R_PASS_MIN and e20 >= f20
    s_pass = a >= AUC_PASS
    verdict = (
        "PASS R and S: embeddings viable, run the packaging check next" if r_pass and s_pass
        else "PASS R, FAIL S: prefilter only if packaging passes, no confidence claim" if r_pass
        else "FAIL R: no embeddings; Adam decides between 'not available' and LLM-only opt-in"
    )

    lines = [
        "# Embeddings separation test results" + ("" if args.embedder == "real" else " (STUB: pipeline check, NOT evidence)"),
        f"Model: {MODEL}; embedder: {args.embedder}; issues embedded: {len(nums)}; body chars: {BODY_CHARS}",
        "Pre-registration: prereg-embeddings-separation.md, frozen 2026-10-14T08:42:33Z",
        "",
        "## Test R: retrieval, true target rank in the pool (12 pairs)",
        "| pair | issue | target | pool | embedding rank | TF-IDF rank |", "|---|---|---|---|---|---|",
        *[f"| {r['pair']} | {r['issue']} | {r['target']} | {r['pool']} | {r['emb_rank']} | {r['tfidf_rank']} |" for r in r_rows],
        "",
        f"Embeddings recall@10 = {recall('emb_rank', 10)}/12, recall@20 = {e20}/12. "
        f"TF-IDF recall@10 = {recall('tfidf_rank', 10)}/12, recall@20 = {f20}/12.",
        f"Pass rule (>= {R_PASS_MIN}/12 at top {R_PASS_K} AND >= TF-IDF): **{'PASS' if r_pass else 'FAIL'}**",
        "",
        "## Test S: separation among LLM-flagged pairs",
        f"AUC (8 true vs 16 clear negatives) = {a:.3f}; pass at >= {AUC_PASS}: **{'PASS' if s_pass else 'FAIL'}**. "
        "Standard error is about 0.11 at this size; a pass is evidence, not proof.",
        f"Mean cosine: true {np.mean(s_true):.3f}, clear negative {np.mean(s_neg):.3f}, split (10, reported apart) {np.mean(s_split):.3f}.",
        f"True range {min(s_true):.3f} to {max(s_true):.3f}; negative range {min(s_neg):.3f} to {max(s_neg):.3f}.",
        "",
        f"## Verdict by the frozen decision rule\n{verdict}",
        "Frozen predictions: R pass 55%, S pass 45%, embeddings beat TF-IDF on recall@20 70%.",
        f"Embeddings beat TF-IDF on recall@20: {'yes' if e20 > f20 else 'no'} ({e20} vs {f20}).",
    ]
    out = "\n".join(lines)
    print(out)
    if args.write:
        (d / "embeddings-results.md").write_text(out + "\n")
        (d / "embeddings-results.json").write_text(json.dumps(
            {"model": MODEL, "R": r_rows, "S": {"true": s_true, "neg": s_neg, "split": s_split, "auc": a}}, indent=2))
        print(f"\nwrote {d / 'embeddings-results.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
