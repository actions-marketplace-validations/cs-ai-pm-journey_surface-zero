# Surface Zero Label Advisor

A GitHub Action that reads each new issue and posts one comment suggesting which of your existing labels fits. It only suggests. It never applies a label, closes an issue, or edits anything.

<!-- RELEASE: rewrite the opening and "Why this exists" in Adam's own words before the repo goes public. -->

## Install

Add `.github/workflows/surface-zero.yml`:

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

Store your Anthropic key as a repository secret named `ANTHROPIC_API_KEY`. Start with `dry-run: 'true'`, open a test issue, and read the job summary on the Actions tab. Remove the `dry-run` line when you are happy with what it would have said. The full walkthrough is in [docs/install-guide.md](docs/install-guide.md).

## What leaves your repository

For each new issue, the title and body go to Anthropic under your own API key, along with your label names and descriptions. Nothing is sent to me. There is no telemetry.

## What it costs

On small label sets I measured about $0.005 per issue in model cost (around 1,700 input tokens) and 25 to 35 seconds of runner time, most of it building the image. A larger label set costs more.

## How well it works

On a replay of 69 real issues from one open-source project, the suggested label matched the project's own labels on 59 of the 67 issues it answered (88%), against about 64% for always guessing the most common label. It abstained on 2. The ground truth is weak, because most of those labels came from the reporter's issue form, and it is one project with two labels in play. That run was before the optional confidence setting was added. <!-- RELEASE: replace with the Friday calibration numbers and link the report. -->

Results are not deterministic. The same issue was suggested as `enhancement` one day and left without a suggestion the next.

## Options

| Input | Default | Meaning |
|---|---|---|
| `anthropic-api-key` | required | Your key. |
| `labels` | every label except duplicate, invalid, wontfix | Comma-separated labels it may suggest. |
| `confidence-threshold` | `0` | 0 to 1. Below it, nothing is posted. The model reports the number itself, and I have not shown that it matches real accuracy, so leave it at 0 unless you have tested it on your own issues. |
| `dry-run` | `false` | Record the suggestion, post nothing. |

`duplicates` and `summaries` are reserved and fail with a clear message if set to true.

## What it does not do

It does not detect duplicates. I built that, measured it against bars I set in advance, and it did not meet them: about one in four flags was a true duplicate under strict rating by two raters (me and an AI model) who themselves disagreed on 29% of pairs. The write-up is in [research/](research/).

It also reads only the new issue's title and body, not comments, code, or past issues. It runs on Linux runners only.

## Hosted version

This repository also contains the hosted service I used for my own testing, a GitHub App behind a webhook. That is a separate thing and not what the Action runs. It still includes duplicate flagging. See [docs/hosted-service.md](docs/hosted-service.md).

## Research

Evaluation scripts, pre-registrations and outcomes are under [research/](research/).

## License

MIT.
