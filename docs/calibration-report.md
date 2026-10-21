# Calibration Report: Surface Zero v1 label advisor

Date: 2026-10-14. Code under test: commit 0a12710c3ad8456c7b5618bf15fca5043618b8cd (222 tests passing). Pass and fail criteria were written down before the data was collected. Two things changed afterwards and are disclosed below: a bug fix after the first run (and a full re-run), and clarifications to how some counts were defined.

## What was measured
Background: an earlier test ("replay 1") ran an older version of the advisor on 69 real issues from the same project and matched the maintainers' labels on 59 of the 67 it answered (88%). The criteria below compare against that figure.

The advisor, at confidence threshold 0, on 440 closed MedTimer issues that carry exactly one of bug or enhancement (255 enhancement, 185 bug), plus the 69 replay-1 issues twice (noise floor and paired comparison), plus 14 Nuts issues, titles only. "Correct" means the advisor's label equals the maintainer's current label on the original repository. That is weak ground truth: labels can be changed after the fact, and titles and bodies may have been edited.

## Headline (MedTimer, 440 issues, fixed commit)
- Accuracy among answered: 97.5% (426 of 437; 95% interval 96 to 99%). Always-enhancement baseline: 58%.
- Abstained: 3 (0.7%). Failed runs: 0. Accuracy counting abstentions as misses: 96.8% (426 of 440).
- Excluding the 15 type-tagged titles: 97.4%.
- Issues scored on filing-time titles (121): 95.0%. Issues scored on current titles (316): 98.4%. This split was looked at after seeing the data. The filing-time figure is the one to quote.
- By body length (descriptive only): 2 empty-body issues, too few to read; 52 short (96.0%); 386 longer (97.7%).

## Verdicts against the frozen criteria
1. Regression, within 3 points of the old 88%: not met as written, because the result is nearly 10 points higher. It is not a regression. The old figure came from 67 answered issues (uncertainty roughly 78 to 94%) with candidate issues in the prompt, so the comparison is soft in both directions. The honest summary: no sign of a drop, and likely better than the old estimate, with the title-edit caveat above.
2. Usefulness of a threshold: not met. At 0.90 the advisor keeps 82% of issues at 99.2% accuracy; at 0.95, 74% at 99.1%. That is a lift of about 1.6 points over 97.5%, short of the required 3. The Install Guide says the threshold is not shown to help and recommends 0. A ceiling effect is the likely reason; on a harder repository the threshold may matter more, and this data cannot say.
3. Reporting: MedTimer and Nuts are in separate tables below and never pooled.

## Paired comparison with replay 1 (69 issues)
- Same answered-or-abstained status and same label as replay 1: 66 of 69 (95.7%) in both new runs. Frozen prediction was at least 90%: held.
- Run 1 and run 2 (same code) disagree on 0 of 69 (noise floor 0). Each differs from replay 1 on 3. Because the prompt schema, model version and candidate context all changed since replay 1, these 3 differences cannot be attributed to any one cause at n = 69.
- Accuracy against the weak truth on the 66 labelled issues: 61 of 66 (92.4%) for both runs; replay 1 was 59 of 66 (89%) on the same denominator.

## First run versus fixed run
The first run (commit 190551b) had 5 of 440 calls fail: with no candidate issues supplied, the model returned a duplicate_of pointing at a neighbouring issue number, and the validator correctly refused it. The fix removes duplicate fields and guidance when there are no candidates and ignores a stray value. The first run's accuracy was 97.7% (422 of 432). The fix changed the label on 3 issues that were answered in both runs (of roughly 435) (793, 1057, 1469), all enhancement to bug, so the prompt change moved 0.7% of answers. The 5 previously failed issues now all answer correctly. Both runs are kept in the vault.

## Nuts (14 cases, titles only, anecdote, separate from MedTimer)
9 of 14 exact (old backtest 10 of 14); 3 of 8 excluding the six app rows (old 4 of 8). Four abstentions (#320, #312, #277, #116). When it answered, it was right on 9 of 10. #168 (enhancement) still lands on api-feature. All 14 had no body text.

## Replication on three other projects (2026-10-21)
The headline above comes from one project. To test whether it holds elsewhere I wrote down the design, the pass bars and my predictions before pulling any data, then ran the same advisor (threshold 0, labels only, nothing posted) on issues from three other open-source projects. Only issues filed by someone other than the project's maintainers were scored, the model could choose only bug or enhancement, and each issue was run once. Projects are described, not named, because I have not asked their maintainers.

| project | issues | agreement among answered (95% interval) | always-majority baseline | abstained |
|---|---|---|---|---|
| Ruby web application | 130 | 93.1% (87 to 96%) | 65% | 0 |
| R package | 106 | 72.0% (63 to 80%) | 76% | 6 |
| C compiler | 83 | 79.3% (69 to 87%) | 61% | 1 |
| pooled | 319 | 82.7% (78 to 86%) | 50% | 7 |

Verdicts against the pre-registered bars. Pooled agreement of at least 90%: failed (82.7%; I had predicted 92%). Every project at least 85%: failed (two projects were below). Every project beating its own baseline by 10 points: failed (the R package was below its baseline). Abstention at most 10% everywhere: passed. A threshold keeping at least half the answers should not lift agreement by 3 points: failed in the other direction, because on these projects it did (see below). Mean stated confidence below agreement: failed, because here it ran slightly above it.

What this changes. The 97.5% figure describes one project and should not be read as a general accuracy. Looked at afterwards (not pre-registered): of the 54 disagreements in the three projects, 52 were the model saying bug where the maintainers said enhancement, and the same direction accounted for 8 of 11 on the first project. In the R package and the C compiler, the maintainers' "enhancement" appears to cover things like requests for missing support and users' own errors. All runs used GitHub's stock label descriptions, so that convention was not written anywhere the model could read it. This is a reading of the disagreements, not a tested explanation.

Confidence and thresholds. Agreement rose with stated confidence: below 0.70 the answers agreed 65% of the time (48 answers), and at 0.95 and above 96% (117 answers). On the first project a threshold added about 1.6 points. Across these three it added 10: a setting of 0.90 kept 53% of answers at 92.7% agreement. By project the gain at 0.90 was about 5, 22 and 5 points, keeping 58%, 32% and 68% of the answers. A threshold's value therefore depends on the project, and I did not change the default (0) from this data: choosing a value on the data that shows it helps would overfit, so changing the default needs a new pre-registered test.

Four labels instead of two. A second run offered bug, enhancement, documentation and question. Strict agreement on the same 319 issues fell from 80.9% to 71.2%, and 14.8% of answers went to question or documentation. Most of that was in the R package, where questions were filed under enhancement, and some of the "misses" may be better labels than the maintainers' own. The test cannot credit them, because issues the maintainers labelled question or documentation are not in the set. It shows that the labels offered move the result by up to about 10 points.

Recent issues scored higher than older ones overall (88% against 77%), but within each project there was no such gap, so that split reflects which project the issues came from.

Additional limits: one run per arm, three primary projects, maintainers' labels as the answer key, and a rule that counts an occasional maintainer-filed issue as a stranger's when that person filed under a fifth of a project's issues.

## Limits to state plainly
- One repository for the headline number (the replication above found 72 to 93% on three others); a bug/enhancement choice only, which is the easiest labelling task a repo has.
- Ground truth is the maintainer's current label, not a blind second review.
- Zero run-to-run disagreement within a day, but the same input gave different outcomes on different days in the tenant tests (2026-10-13 and 2026-10-14). Do not describe the model as deterministic.
- Empty and very short bodies: the 440 set has almost none (2 empty). On titles-only Nuts runs, 4 of 14 abstained, against 0.7% on MedTimer. That is an anecdote pointing the same way as the tenant observation: expect more abstentions on issues with little text. Whether it is worth a dedicated test is a post-release question.
- Cost: about $0.004 per call; the whole fixed run (578 calls) cost $2.31.
