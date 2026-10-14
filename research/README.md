# Research

Evaluation code and data behind Surface Zero. None of this is shipped in the GitHub Action: the Action's image copies only `src/`, `pyproject.toml`, `uv.lock` and the README.

Run everything from the repository root so relative paths (such as `data/label_sets/...`) resolve.

## scripts/

| Script | What it does |
|---|---|
| `medtimer_pull.py`, `medtimer_events_pull.py`, `medtimer_titles_pull.py` | Pull a real project's issues, label events and rename history for an offline replay. |
| `medtimer_import.py` | Seed a fork and replay issues through the advisor (plan, seed, replay). |
| `medtimer_score.py` | Score a replay against its frozen pre-registration. |
| `dup_eval.py`, `dup_eval_packets.py`, `dup_eval_score.py` | The duplicate-detection experiment: three prompt arms, blind two-rater packets, scoring. |
| `emb_separation.py` | The embeddings retrieval and separation test (pre-registered, one run). |
| `nuts_backtest.py`, `app_deliveries.py` | Earlier backtests on other projects. |

## blind_test/

Sixteen synthetic issues written for an early blind test of the advisor, with the model's responses. The text is invented; it is not taken from anyone's repository.

## Outcomes in one paragraph

Label suggestion matched the project's own labels on 59 of 67 answered issues (88%) against about 64% for always guessing the most common label, with a weak ground truth (labels mostly came from the reporter's issue form). Duplicate detection was built and measured and did not meet its bar: about one in four flags was a true duplicate under strict two-rater rating, and the raters themselves disagreed on 29% of pairs. A TF-IDF prefilter did not help, and a small embedding model (all-MiniLM-L6-v2) failed its pre-registered retrieval and separation bars (recall@20 9 of 12 against TF-IDF's 11 of 12; AUC 0.73 against a 0.80 bar). Duplicate detection is therefore not part of the Action.
