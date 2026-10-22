# Surface Zero Label Advisor: Install Guide

## What it does
When someone opens an issue in your repository, Surface Zero reads the title and body and posts one comment suggesting one of your existing labels (once you turn dry run off; until then it only reports to the job summary). It never applies a label, closes, or edits anything. If no label clearly fits, it stays silent.

## What leaves your repository
The issue title and body are sent to Anthropic, under your own API key, for each new issue. Your repository's label names and descriptions are sent with them. Nothing is sent to the author of this Action. There is no telemetry.

## What you need
- A repository with at least two labels (GitHub's defaults qualify).

If your repository has no type labels yet, create two with the GitHub CLI (you can also do it on the Labels page):

```bash
gh label create bug --description "Something isn't working" --repo OWNER/REPO
gh label create enhancement --description "New feature or request" --repo OWNER/REPO
```

Surface Zero only suggests labels that already exist. It never creates one.

- An Anthropic API key, stored as a repository secret named `ANTHROPIC_API_KEY`.
- Linux runners (`ubuntu-24.04` recommended). Windows and macOS runners cannot run Docker actions.

## Install
Create `.github/workflows/surface-zero.yml`:

```yaml
name: Surface Zero
on:
  issues:
    types: [opened]
permissions:
  issues: write
jobs:
  advise:
    runs-on: ubuntu-24.04
    steps:
      - uses: cs-ai-pm-journey/surface-zero@v1
        with:
          anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}
          dry-run: 'true'
```

`issues: write` is the only permission needed. Add `contents: read` only if you also use `actions/checkout` in the same job.

## First run: dry run
Dry run is the default and is written out above so you can see it. Leave `dry-run: 'true'`, open a test issue, and read the job summary on the Actions tab. A good first test is a clear one, for example the title `App crashes when I tap Save with an empty name` with a few lines describing the steps and what happens. Then try a vague one (`It doesn't work properly`) to see it decline to guess. It reports what would have been suggested, and the cost. Nothing is posted. When you are satisfied, change it to `dry-run: 'false'` to start posting comments.

## Inputs
| Input | Default | Meaning |
|---|---|---|
| anthropic-api-key | required | Your key. |
| labels | all repo labels except duplicate, invalid, wontfix | Comma-separated labels it may suggest. |
| confidence-threshold | 0 | 0 to 1. Below it, nothing is posted. See below. |
| dry-run | true | Record the suggestion, post nothing. Set to `'false'` to post comments. |
| github-token | the workflow token | Leave at the default. |

`duplicates` and `summaries` exist but are not available in this version and fail with a clear message if set to true. Duplicate detection was built and measured and did not meet its bar (about one in four flags was a true duplicate by strict rating, and two independent raters disagreed on 29% of pairs), so it is deliberately not shipped.

## Confidence threshold (read before using)
The model reports how likely a maintainer would choose its label, and setting a threshold makes it stay silent below that number. Whether that helps depends on the project, so the default is 0 (always post).

On 440 closed issues from one project, 0.90 kept 82% of answers and raised agreement with the maintainers' labels from 97.5% to 99.2%, about 1.6 points, short of the 3 points I required in advance. On three other projects, 0.90 kept 53% of answers and raised agreement from 83% to 93%. By project the gain ranged from about 5 to 22 points and the share kept from 32% to 68%. On those three projects the number also ran a little high in the middle of the range: answers stated at 0.80 to 0.90 agreed with the maintainers about 78% of the time. Treat the confidence as a ranking, not a probability.

Because it varies by project, test it on yours. Run in dry-run mode, read the confidence in the job summary for a number of issues, and only then set a value. A higher threshold means more issues with no suggestion, and you label those by hand.

## Make your labels easy to read
The model reads each label's name and description. In testing, nearly all disagreements came from projects that use "enhancement" more broadly than GitHub's stock description ("New feature or request"), so the model called as bugs issues the maintainers had labelled enhancements. If your labels mean something specific, edit their descriptions on GitHub to say it in one sentence, for example: "bug: a defect in something we already support", "enhancement: anything new, including support for something we don't have yet". I have not yet tested whether this closes the gap.

## What a suggestion looks like
With `dry-run: 'false'`, one comment on the new issue naming the suggested label and a one-sentence reason, ending with: "Posted by the Surface Zero Action. It can't apply this label, only suggest it. To turn it off, disable or delete the Surface Zero workflow in this repository." It comments once per issue.

## If something goes wrong
If the model call fails (a bad key, a rate limit, an outage), the job is marked failed, nothing is posted, and the job summary says why. Re-running the job on the same issue is safe. If the model declines to suggest a label, the job succeeds, nothing is posted, and the summary says the model chose to abstain.

## Reading results
The job summary shows the result, model, tokens, cost, confidence, and whether silence came from the model or from your threshold. To keep the full record, add this step after the Action (verified in a tenant run 2026-10-14):

```yaml
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: surface-zero-run
          path: surface-zero-run.json
```

The record contains the issue title and body, the model's label or reason, token counts, cost, and timings. It contains no keys or tokens. Artifacts follow your repository's visibility and GitHub keeps them 90 days by default.

## Cost
Measured on small label sets: about $0.005 per issue in model cost (about 1,700 input tokens). Runner time is about 25 to 35 seconds per issue, mostly building the image. Larger label sets cost more. You pay Anthropic directly under your own key; this Action has no charge of its own.

## Turning it off
Disable or delete the workflow. The comment footer says the same.

## Limits, stated plainly
- Suggestions only, one per issue, no edits.
- Linux runners only.
- Results are not identical every time: the same issue has produced a suggestion on one day and no suggestion on another.
- It reads only the new issue's title and body, not comments, code, or past issues.
- On 440 closed issues from one open-source app (MedTimer, bug versus enhancement), the suggested label agreed with the maintainers' about 95 to 98% of the time when it answered. On three other projects it agreed 72 to 93% (83% pooled over 319 issues), and on one of them it scored below always guessing the most common label. Almost every disagreement was the model calling an issue a bug that the maintainers had called an enhancement. Ground truth is the maintainers' current labels, and it is a two-label choice. Issues with little or no text are more likely to get no suggestion. Treat it as an aid, not an authority. Full method and limits: the Calibration Report.

## Troubleshooting
| You see | Cause and fix |
|---|---|
| Setup error naming `ANTHROPIC_API_KEY` | The secret is missing or misnamed. |
| "permissions: issues: write" in an error | Add the permissions block shown above. |
| Labels "do not exist" | A name in `labels` does not match your repository exactly. |
| "at least 2" labels | Create a second label, or list more in `labels`. |
| Nothing happens on a new issue | The workflow must be on the default branch before the issue is opened. |
| Skipped, "already commented" | Normal. It comments once per issue. |
