# ADR: Quiz v2 — balanced dataset and scoring history

- **Status:** Data repair and rebalance implemented in private submodule commit `1225a56`. The original scoring decision was superseded on 2026-11-10; quiz scoring now uses the LLM judge, with the parser as an audit cross-check.
- **Date:** 2026-11-05
- **Scope:** `quiz` category only

## Revision (2026-11-10)

The original decision D2, no LLM judge for quiz, was superseded. The deterministic parser mis-scores noisy answers that contain reasoning or discuss multiple letters. The LLM judge is now the scoring authority: it selects a letter from the candidate answer, and NicheBench compares that letter with the gold. The parser still runs on the same answer and records `selected`, `parse_reason`, and `agrees` for audit; it does not affect the score.

Judge calls retry up to three times after transient or parse failures. Exhausted judge failures are reported as errors and excluded from quiz averages. The data repair and rebalance described in this ADR remain in effect.

An explicit abstention is scored as zero; invalid quiz data is a `config_error` and is excluded from model averages.

## Problem

The quiz dataset and its scoring path had four coupled defects:

1. **The gold answer is not balanced.** 85 of 145 questions (58.6%) have gold
   answer `B`; `E` and `F` are never correct. A model can score well by always
   answering `B`, and the dataset cannot measure position bias.
2. **Option text carries redundant letter prefixes.** The option text already
   starts with `A) `, `B) ` and so on, while the prompt composer adds its own
   letter. The rendered prompt shows `A) A) ...`.
3. **Scoring goes through an LLM judge.** The task is a known-letter exact
   match. The judge ignores its own `selected` field, and a judge API error
   becomes a score of `0` — a transient outage is indistinguishable from a
   wrong answer.
4. **The prompt and the data disagree.** The system prompt advertises
   "A, B, C, D, or E" while every question has 6 options.

Together these make quiz scores non-reproducible, position-biased, and
dependent on network availability.

## Decision

**Original decision (superseded): one-time data migration plus deterministic scoring.**

- **Data.** A migration script normalizes the dataset once, in place, under an
  explicit approval gate. It strips the redundant letter prefix from every
  option and reorders the options so the gold answer spreads evenly across
  A–F. Target counts for 145 six-option questions are 25/24/24/24/24/24. The
  target positions come from a fixed, recorded seed, and the shuffle of the
  remaining options is seeded per question, so the result is reproducible.
- **Scoring (superseded by the 2026-11-10 revision).** Quiz scoring becomes a pure function: parse the model's answer
  into a letter, compare it to the gold letter, emit 1.0 or 0.0. There is no
  judge model, no judge API call, and no network dependency in the quiz path.
  Unparseable answers are a failure with an explicit reason, never a silent
  guess.
- **Harness.** The harness stays dumb. There is no runtime shuffling; the
  balanced order lives in the data, so a question always renders the same way.

## Content-preservation guarantee

The migration may **only reorder existing options and strip the redundant
letter prefix**. It must never invent, delete, or rewrite option text.

The guarantee is mechanical, not a promise: for every question the migration
computes a SHA-256 over the sorted multiset of its option texts before and
after, and refuses to write unless the two match. The manifest records both
hashes per question, so a reviewer can verify content preservation without
re-running the migration. A `--verify` mode re-reads the manifest and asserts
every hash and every new gold letter.

## Dataset version

The loader reads `dataset_version` from each quiz row, defaults missing values
to `quiz-v1`, and propagates the version to results. The private data migration
was applied in submodule commit `1225a56`; current quiz rows use `quiz-v2`.

## Comparability

Pre-migration (quiz-v1) and post-migration (quiz-v2) scores are **not
directly comparable**:

- quiz-v1 scores depend on an LLM judge and on a prompt that showed doubled
  letters and advertised only five options;
- quiz-v2 uses a rendered prompt with single letters and all options shown;
- the scoring authority changed on 2026-11-10 from the parser to the LLM judge;
- the gold letter moved for every question, so "same question" does not mean
  "same answer key".

Baseline and post-migration runs must be reported side by side and labelled
with their dataset version. `reports/quiz_v2_baseline.json` holds the
pre-migration data audit. The dry-run migration manifest is generated at
`reports/quiz_v2_manifest.json` by `scripts/quiz_v2_rebalance.py --dry-run`;
`reports/` is gitignored. The dry-run reported distribution A=25 B=24 C=24
D=24 E=24 F=24 and preserved all 145/145 content multiset hashes. The
submodule write was later applied in commit `1225a56`.

## Implementation status

Q0–Q7 and the data tasks Q1.3a (repairing malformed YAML choices) and Q1.4
(writing the balanced data) are complete. The private data changes are in
submodule commit `1225a56`.

The CI data gate audits a sanitized, committed copy of the quiz-v2 shape at
`tests/fixtures/quiz/ci_dataset` (12 items, 6 plain-text choices each, gold
balanced over A–F). It is synthetic fixture data, not the private dataset, so
the gate runs on every CI checkout even when the private data submodule is
absent. The private dataset is audited separately when the submodule is
checked out.

The old `openspec/changes/archive/**` records describe historical quiz behavior
and may mention the removed quiz metrics or judge path. They are not current
implementation guidance.

## Rollback

- **Data.** The pre-migration state is the previous commit of the private
  quiz-data submodule. Reverting that commit restores the original option
  order and gold letters.
- **Code.** The quiz-specific code is isolated in the quiz scoring module, the
  judge runner's quiz branch, the prompt composer, and the summary. Reverting
  those files leaves the code and bug-fixing paths untouched.
- **Comparability.** Because results carry `dataset_version`, runs taken
  before and after a rollback remain distinguishable.

## Baseline

`reports/quiz_v2_baseline.json` records 145 files with 6 options each, gold
distribution A=20 B=85 C=32 D=8 E=0 F=0, all 870 options carrying a letter
prefix, 10 questions without `context`, no repeated option text inside a
question, and no duplicated question identity.

A naive scan of the parsed YAML reports 858 of 870 prefixed options and 140
of 145 fully prefixed files. That undercount comes from 12 options across 5
files whose text contains an unquoted `key: value`, which YAML parses as a
mapping rather than a string. The audit renders those back to their original
wording, so the true figure is 870 of 870. The same quirk means the migration
must not assume every choice is a string.
