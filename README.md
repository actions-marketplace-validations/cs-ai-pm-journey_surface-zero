# Surface Zero Label Advisor

A GitHub Action that reads each new issue and posts one comment suggesting which of your existing labels fits. It only suggests. It never applies a label, closes an issue, or edits anything.

## Why this exists

Labeling issues is small work, but it comes back with every new issue. I wanted to find out how much of that first pass a model could handle without being handed the keys to the repository.

Surface Zero reads a new issue, looks at the labels your project already uses, and suggests one. That's all it does. It reads your labels and the issue, and the only thing it ever writes is a single comment. It can't apply the label, close the issue, or change anything else, and the code is small enough to read yourself. If the issue is unclear, it can say nothing instead of forcing an answer.

I kept the scope narrow on purpose. A bot that closes issues or makes a long list of decisions just because it technically can is a bot you have to supervise. This one is meant to save a little triage time while the person responsible for the project stays in charge.

I also wanted something another maintainer could install without me. It runs with your own Anthropic API key, so your issue text goes to Anthropic and nowhere to me, and there's no service of mine involved. You can run it in dry-run mode first, and it posts nothing until you turn that off.

I tested it before releasing it. On 440 closed issues from one open-source app, its label agreed with the maintainers' about 95 to 98% of the time when it answered, and it declined to answer on under 1%. It won't always be right, and the same issue can get a different outcome on a different day. I also built duplicate detection and left it out because it wasn't good enough, and I tested a confidence threshold that didn't help, so the default turns it off. The [Calibration Report](docs/calibration-report.md) has the method and the limits.

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

On 440 closed issues from one open-source app (MedTimer, bug versus enhancement), the suggested label agreed with the maintainers' own about 95 to 98% of the time when it answered, against 58% for always guessing the most common label. It declined to answer on under 1%. The ground truth is the maintainers' current labels, it is one project, and it is a two-label choice, so treat it as a smoke test, not a guarantee. Issues with little or no text are more likely to get no suggestion. Method, both runs and limits: [Calibration Report](docs/calibration-report.md).

Results are not deterministic. The same issue was suggested as `enhancement` one day and left without a suggestion the next.

## Options

| Input | Default | Meaning |
|---|---|---|
| `anthropic-api-key` | required | Your key. |
| `labels` | every label except duplicate, invalid, wontfix | Comma-separated labels it may suggest. |
| `confidence-threshold` | `0` | 0 to 1. Below it, nothing is posted. In testing it did not help enough to recommend (about 1.6 points of agreement for dropping a quarter of the answers), so leave it at 0 unless you have tested it on your own issues. |
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
