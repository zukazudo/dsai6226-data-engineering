# Datasheet: Adult census income extract

Following the structure of Gebru et al., *Datasheets for Datasets* (CACM, 2021), which Unit 8
sets as the standard. The purpose of a datasheet is to let someone decide whether this data is
suitable for their task without having to rediscover its limits the hard way.

| | |
|---|---|
| Dataset | Adult census income extract (`adult.csv`) |
| Records | 32,561 |
| Columns | 15 as published; 24 after modelling; 21 in the feature table |
| Size | 4,104,734 bytes |
| sha256 | `250e154ed75714ae57a564926d66c6319cd6aac1bcd32774cc76841a88d74e53` |
| Snapshot | the file is immutable and never edited in place; the hash is checked by `scripts/validate_labs.py` |
| Maintainer | Team E, DSAI 6226, NM-AIST |
| This document | v1.0, 2 October 2026 |

## Motivation

**Why was the dataset created?** Not by us. It was extracted from the 1994 United States Census
Bureau Current Population Survey by Barry Becker and published to the UCI Machine Learning
Repository as a benchmark for income classification. We adopted it in Week 1 as the team
dataset for this course.

**What are we using it for?** Two things, and they are different. For Labs 1 to 7 it stands in
for an operational dataset behind an allocation decision: who is eligible for support, by
occupation and education. For Lab 8 it is a classification problem: predict whether a person's
income exceeds 50,000 dollars.

## Composition

**What does an instance represent?** One survey respondent. One row per person, no nesting.

**What is in it?** Demographics (age, sex, race, native country), household position (marital
status, relationship), employment (workclass, occupation, hours per week), education (label
and an ordered level), two financial columns (capital gain, capital loss), a survey weight
(`fnlwgt`), and the income band that is the prediction target.

**Is anything missing?** Yes, and it is not recorded as null. Missingness is the literal string
`?`, which means `isna()` reports zero nulls across all 15 columns and any validation built on
it passes this file as clean. 2,399 records are affected:

| Column | Records with `?` |
|---|---|
| `workclass` | 1,836 |
| `occupation` | 1,836 |
| `native.country` | 583 |

Seven of the `occupation` cases are **not** missing. Those respondents have `workclass` of
`Never-worked`, so they genuinely have no occupation to record. The warehouse keeps the two
states apart as `Unknown` and `Not applicable`; collapsing them loses a real distinction.

**Are there duplicates?** 47 records sit in 23 groups of identical rows, of which 24 are what
`pandas.duplicated()` reports. They are retained and marked rather than dropped, because the
dataset has no key of any kind and nothing proves they are errors rather than two respondents
who answered identically. `fnlwgt` looks like an identifier and is not: 21,648 distinct values
over 32,561 rows, because it is a survey weight and collision is its intended behaviour.

**Are values censored?** Three columns are top-coded, which matters more than it first looks:

| Column | Sentinel | Records |
|---|---|---|
| `age` | 90 | 43 (with **zero** records at 89) |
| `capital.gain` | 99999 | 159 |
| `hours.per.week` | 99 | 85 |

These are not data errors. Top-coding is statistical disclosure control: the tails are
compressed into one bucket so an unusual respondent cannot be identified from a combination of
otherwise innocuous fields. What Lab 1 recorded as a quality defect is the publisher
protecting people. The warehouse stores them as NULL beside a boolean flag, so an average
excludes them while the censoring stays visible and countable.

## Collection

**How was it collected?** By the US Census Bureau, through the Current Population Survey,
in 1994. We did not collect it, did not sample it and cannot extend it.

**Is it a sample?** Yes. The extract applies filters to the full CPS, and `fnlwgt` is the
post-stratification weight that maps each respondent to a number of people in the population.
Nothing in this repository uses that weight, so **every count in this project is a count of
rows, not an estimate of people.** Treating a row count as a population estimate would be
wrong.

## Preprocessing and labelling

The raw file is never edited. Everything below happens in the pipeline and is reproducible
with one command.

- `?` resolves to reserved dimension members rather than NULL, so a group-by accounts for all
  32,561 rows instead of silently dropping the gaps.
- Top-coded measures load as NULL beside a flag.
- Duplicates are marked with a group id and a sequence number, never removed.
- Ten quality rules refuse malformed rows into a `quarantine` table with a reason code.

`python scripts/ingest.py` rebuilds everything, including the feature table.

## Uses

**What is it suitable for?** Teaching data engineering, which is what it is doing here.
Benchmarking. Income classification as a modelling exercise.

**What is it not suitable for?**

- **Any decision about a real person.** It describes a 1994 US population. It says nothing
  about anyone alive today and nothing at all about Tanzania.
- **Population estimates**, unless `fnlwgt` is used as a weight, which this project does not do.
- **Fairness claims presented as findings about the world.** The data encodes the inequalities
  of 1994 US society, including in `race`, `sex` and `native.country`. A model trained on it
  will reproduce them. That makes it a reasonable subject for studying bias and an
  unreasonable basis for asserting anything about how the world is now.
- **Small-segment conclusions.** 20 of the 87 published segments hold fewer than thirty people.
  The serving table flags them for exactly this reason.

## Personal data and PDPA status

**Does it contain personal data? No.** The four-step walkthrough from Unit 6, run honestly:

1. **Is there personal data?** No direct identifier: no name, address, contact detail or
   identity number. The columns are quasi-identifiers in combination, but the publisher
   applied disclosure control before release, and the top-coding described above is the
   visible evidence of it. No data subject in the file is Tanzanian.
2. **Purpose and lawful basis.** Not engaged, because step 1 answered no.
3. **Protection and borders.** Not engaged for this file. The Lab 5 cloud upload still chose
   `africa-south1` deliberately rather than accepting a default, because practising the
   decision is the point.
4. **Documentation.** This file, plus the PDPA section of the README.

**Where the law would apply.** The pipeline this project rehearses would receive outcome
records about identifiable living people in Tanzania. For that system the Personal Data
Protection Act, No. 11 of 2022 applies in full: registration with the Personal Data Protection
Commission before processing begins, a written purpose and lawful basis, minimisation, and a
prior permit for any cross-border transfer. Claiming compliance obligations over a public 1994
teaching file would misread the law; assuming the real system inherits this file's freedom
would be worse.

## Known gaps, in one list

- Missingness is `?`, not null, and `isna()` will lie to you.
- `fnlwgt` is not a key and is not used as a weight here.
- Three columns are top-coded; averages must exclude them.
- `capital.gain` is **not usable as a feature** for the income target. See the leakage section
  of the README: it is a component of the income that defines the answer, and at the
  top-coded value it predicts the target with 100 per cent accuracy.
- A single 1994 snapshot with no date column, so nothing here supports a time-based split, a
  trend, or a partition key.
- 20 of 87 published segments hold fewer than thirty people.

## Maintenance

The file is immutable and verified by hash on every validation run. It will not be updated,
because the source is a fixed historical extract. If a future lab needs a second file, the
ingester already accepts a directory and will stage it idempotently alongside this one.

**An incident worth recording.** On 15 September 2026 the file was opened in a spreadsheet and
saved, which stripped the quoting, changed the line endings and appended one row with an age
of 10 and an education level inconsistent with its label. The pipeline rejected that row on
its own, with reason `education_mapping_mismatch`, and the star never saw it. The file was
restored from version control and the hash re-verified. Inspect this data with `head`, pandas
or DuckDB, never with a spreadsheet.
