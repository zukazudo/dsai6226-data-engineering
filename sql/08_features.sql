-- ---------------------------------------------------------------------------
-- 08_features.sql
--
-- Lab 8. The ML-ready feature table.
--
-- One row per person, one column per feature, one column holding the answer.
-- Built from analytic_person, which is built from the star, which is built
-- from a file whose sha256 is recorded. Rebuilt by scripts/ingest.py, so the
-- one-command rebuild Unit 8 asks for is the same command as everything else.
--
-- Three things in this file are decisions rather than transformations, and
-- each is argued in the README: what was cut for leakage, how the split is
-- assigned, and why the split is not time-based.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS feature_person (
    -- identity, not features. Present so a prediction can be traced back to a
    -- row, a file and a line, and excluded from any model by name.
    person_sk                       BIGINT    PRIMARY KEY,
    split                           VARCHAR   NOT NULL,
    duplicate_group_id              INTEGER,

    -- the answer
    income_gt_50k                   BOOLEAN   NOT NULL,

    -- engineered features
    age                             SMALLINT,
    age_band                        VARCHAR   NOT NULL,
    education_num                   SMALLINT,
    education_vs_occupation_median  SMALLINT,
    hours_per_week                  SMALLINT,
    hours_vs_occupation_median      DOUBLE,
    is_full_time                    BOOLEAN,
    workclass_group                 VARCHAR   NOT NULL,
    occupation                      VARCHAR   NOT NULL,
    occupation_is_assignable        BOOLEAN   NOT NULL,
    is_partnered                    BOOLEAN   NOT NULL,
    relationship                    VARCHAR   NOT NULL,
    race                            VARCHAR   NOT NULL,
    sex                             VARCHAR   NOT NULL,
    country_is_united_states        BOOLEAN   NOT NULL,

    -- censoring flags. Unit 8 is explicit that silently filling a gap teaches
    -- the model that gaps never happen, so the fact of censoring is a column.
    age_was_censored                BOOLEAN   NOT NULL,
    hours_was_censored              BOOLEAN   NOT NULL
);

DELETE FROM feature_person;

INSERT INTO feature_person
WITH occ_norms AS (
    -- Per-occupation norms, computed over the whole table. This is the one
    -- place the file touches aggregate statistics, and it is a deliberate
    -- compromise recorded in the README: computing them over train only would
    -- be stricter, but these are population norms of a 1994 census, not
    -- learned parameters, and the split is assigned from a hash rather than
    -- from anything these values influence.
    -- LAB 9. Read from the star directly rather than through analytic_person.
    --
    -- analytic_person is a view over an eight-way join producing 24 columns.
    -- This CTE needs three of them, so reading it through the view asked the
    -- engine to resolve seven dimension joins whose output is then discarded,
    -- and it did so on a second pass over the same rows the main SELECT below
    -- already scans. Going to fact_person and the two dimensions that actually
    -- supply these columns cut the feature build from 184 ms to 161 ms, and the
    -- fingerprint of the resulting table is unchanged.
    SELECT o.occupation,
           median(f.hours_per_week) AS med_hours,
           median(e.education_num)  AS med_education
    FROM fact_person f
    JOIN dim_occupation o USING (occupation_sk)
    JOIN dim_education  e USING (education_sk)
    GROUP BY o.occupation
)
SELECT
    a.person_sk,

    -- SPLIT ASSIGNMENT.
    --
    -- Deterministic from a hash, not from a shuffle, so it is reproducible in
    -- any engine and on any machine without carrying a seed around. The hash
    -- is taken over the duplicate group where there is one, so every member of
    -- a group lands on the same side: that is the group-aware requirement, and
    -- on this dataset it is the only defence against split leakage, because
    -- 47 rows are exact duplicates of one another.
    --
    -- Hashing is uniform over the key, and it is applied within each class of
    -- the target, so the 24.1 per cent base rate is preserved in all three
    -- parts without an explicit stratification step.
    CASE
        WHEN abs(hash(coalesce(CAST(a.duplicate_group_id AS VARCHAR),
                               CAST(a.person_sk AS VARCHAR))
                      || CAST(a.income_gt_50k AS VARCHAR))) % 100 < 60 THEN 'train'
        WHEN abs(hash(coalesce(CAST(a.duplicate_group_id AS VARCHAR),
                               CAST(a.person_sk AS VARCHAR))
                      || CAST(a.income_gt_50k AS VARCHAR))) % 100 < 80 THEN 'validate'
        ELSE 'test'
    END                                                     AS split,
    a.duplicate_group_id,

    a.income_gt_50k,

    -- FEATURE 1. age, kept raw alongside a band.
    a.age,

    -- FEATURE 2. age_band.
    -- Earnings rise with age and then plateau. A linear term cannot express a
    -- plateau, and the band lets a model treat the shape as what it is.
    CASE
        WHEN a.age IS NULL     THEN 'censored'
        WHEN a.age < 25        THEN 'under 25'
        WHEN a.age < 35        THEN '25 to 34'
        WHEN a.age < 45        THEN '35 to 44'
        WHEN a.age < 55        THEN '45 to 54'
        ELSE '55 and over'
    END                                                     AS age_band,

    -- FEATURE 3. education_num, the ordered ladder.
    -- The education label is dropped: it carries exactly the same information
    -- as this column, and two spellings of one fact is redundancy, not signal.
    a.education_num,

    -- FEATURE 4. education_vs_occupation_median.
    -- How far this person's schooling sits above or below the usual level for
    -- their trade. Being over-educated for an occupation is a different
    -- situation from simply being well educated, and the raw column cannot
    -- say which it is.
    CAST(a.education_num - n.med_education AS SMALLINT)      AS education_vs_occupation_median,

    -- FEATURE 5. hours_per_week, raw.
    a.hours_per_week,

    -- FEATURE 6. hours_vs_occupation_median.
    -- Forty hours means something different in an occupation whose median is
    -- 40 than in one whose median is 25. The ratio carries the comparison the
    -- raw number leaves to chance.
    CASE WHEN n.med_hours > 0
         THEN a.hours_per_week / n.med_hours END            AS hours_vs_occupation_median,

    -- FEATURE 7. is_full_time.
    -- The part-time boundary is a real threshold in employment terms and
    -- carries more signal than the exact count of hours either side of it.
    a.hours_per_week >= 35                                  AS is_full_time,

    -- FEATURE 8. workclass_group.
    -- Nine workclass values collapse to four that behave differently:
    -- employment sector is the distinction, not the administrative detail.
    CASE
        WHEN a.workclass IN ('Private')                              THEN 'private'
        WHEN a.workclass IN ('Federal-gov', 'State-gov', 'Local-gov') THEN 'government'
        WHEN a.workclass IN ('Self-emp-inc', 'Self-emp-not-inc')      THEN 'self-employed'
        ELSE 'other or unknown'
    END                                                     AS workclass_group,

    a.occupation,
    NOT (a.occupation_unknown OR a.occupation_not_applicable) AS occupation_is_assignable,

    -- FEATURE 9. is_partnered.
    -- Seven marital states collapse to the one distinction that matters for
    -- household income: whether there is a partner in the household now.
    a.marital_status IN ('Married-civ-spouse', 'Married-AF-spouse')
                                                            AS is_partnered,
    a.relationship,
    a.race,
    a.sex,

    -- FEATURE 10. country_is_united_states.
    -- dim_native_country has 42 members and a very long tail, most with a
    -- handful of records. Collapsing it keeps the distinction that carries
    -- signal and avoids forty categories a model would only memorise.
    a.native_country = 'United-States'                      AS country_is_united_states,

    a.age_is_topcoded                                       AS age_was_censored,
    a.hours_is_topcoded                                     AS hours_was_censored
FROM analytic_person a
JOIN occ_norms n USING (occupation);
