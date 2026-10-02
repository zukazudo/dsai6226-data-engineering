-- ---------------------------------------------------------------------------
-- 03_load.sql
--
-- Transforms staged rows into the star. Every statement here is idempotent:
-- run it twice and the second run inserts nothing. This is where every Lab 1
-- finding is acted on, and it is the only place cleaning happens.
--
-- The ingester substitutes the current run id for $run_id before executing.
-- ---------------------------------------------------------------------------

-- ------------------------------------------------------------ 1. validation
-- Ten executable rules. Eight test validity, one tests completeness and one
-- tests uniqueness, which are the three dimensions Lab 6 asks for.
--
-- Rows that fail any rule are recorded with the reason, copied whole into
-- quarantine, and excluded from the fact load. A row can fail more than one
-- rule and gets one reject row per reason, which is why the primary key is
-- (record_key, reason).
--
-- The rules reject rows that are malformed. They do not reject rows that are
-- merely odd. The three records contradicting themselves on sex and
-- relationship, and the 2,399 records carrying '?', are real observations and
-- load normally.

INSERT INTO load_reject (load_run_id, record_key, source_file, source_row, reason, detail)
SELECT $run_id, record_key, source_file, source_row, reason, detail
FROM (
    SELECT record_key, source_file, source_row, 'age_not_an_integer' AS reason,
           'age = ' || coalesce(age, 'NULL') AS detail
    FROM stg_adult WHERE try_cast(age AS INTEGER) IS NULL

    UNION ALL
    SELECT record_key, source_file, source_row, 'age_out_of_range',
           'age = ' || age
    FROM stg_adult
    WHERE try_cast(age AS INTEGER) IS NOT NULL
      AND try_cast(age AS INTEGER) NOT BETWEEN 1 AND 120

    UNION ALL
    SELECT record_key, source_file, source_row, 'fnlwgt_not_a_positive_integer',
           'fnlwgt = ' || coalesce(fnlwgt, 'NULL')
    FROM stg_adult
    WHERE try_cast(fnlwgt AS BIGINT) IS NULL OR try_cast(fnlwgt AS BIGINT) <= 0

    UNION ALL
    SELECT record_key, source_file, source_row, 'hours_per_week_out_of_range',
           'hours.per.week = ' || coalesce(hours_per_week, 'NULL')
    FROM stg_adult
    WHERE try_cast(hours_per_week AS INTEGER) IS NULL
       OR try_cast(hours_per_week AS INTEGER) NOT BETWEEN 1 AND 99

    UNION ALL
    SELECT record_key, source_file, source_row, 'capital_value_invalid',
           'gain = ' || coalesce(capital_gain, 'NULL') || ', loss = ' || coalesce(capital_loss, 'NULL')
    FROM stg_adult
    WHERE try_cast(capital_gain AS INTEGER) IS NULL
       OR try_cast(capital_loss AS INTEGER) IS NULL
       OR try_cast(capital_gain AS INTEGER) < 0
       OR try_cast(capital_loss AS INTEGER) < 0

    UNION ALL
    SELECT record_key, source_file, source_row, 'income_not_recognised',
           'income = ' || coalesce(income, 'NULL')
    FROM stg_adult WHERE income NOT IN ('<=50K', '>50K') OR income IS NULL

    UNION ALL
    SELECT record_key, source_file, source_row, 'sex_not_recognised',
           'sex = ' || coalesce(sex, 'NULL')
    FROM stg_adult WHERE sex NOT IN ('Male', 'Female') OR sex IS NULL

    -- The education ladder is seeded in 02_schema.sql, so a pair the reference
    -- does not contain is a genuine integrity failure and not a new category.
    UNION ALL
    SELECT s.record_key, s.source_file, s.source_row, 'education_mapping_mismatch',
           'education = ' || coalesce(s.education, 'NULL') ||
           ', education.num = ' || coalesce(s.education_num, 'NULL')
    FROM stg_adult s
    WHERE NOT EXISTS (
        SELECT 1 FROM dim_education d
        WHERE d.education = s.education
          AND d.education_num = try_cast(s.education_num AS SMALLINT)
    )

    -- COMPLETENESS. Lab 6 rule 9.
    --
    -- An empty field is not the same thing as '?'. '?' is the source saying
    -- "asked, not answered", which Lab 2 models as a real dimension member and
    -- which 2,399 records legitimately carry. An empty or whitespace field is
    -- the source saying nothing at all, which is a delivery fault.
    --
    -- Rejecting '?' here would destroy 1,836 records and contradict two labs.
    -- The distinction is the whole point of the check.
    UNION ALL
    SELECT record_key, source_file, source_row, 'required_field_empty',
           'empty: ' || concat_ws(', ',
               CASE WHEN trim(coalesce(age, ''))            = '' THEN 'age' END,
               CASE WHEN trim(coalesce(workclass, ''))       = '' THEN 'workclass' END,
               CASE WHEN trim(coalesce(education, ''))       = '' THEN 'education' END,
               CASE WHEN trim(coalesce(marital_status, ''))  = '' THEN 'marital.status' END,
               CASE WHEN trim(coalesce(occupation, ''))      = '' THEN 'occupation' END,
               CASE WHEN trim(coalesce(relationship, ''))    = '' THEN 'relationship' END,
               CASE WHEN trim(coalesce(race, ''))            = '' THEN 'race' END,
               CASE WHEN trim(coalesce(sex, ''))             = '' THEN 'sex' END,
               CASE WHEN trim(coalesce(native_country, ''))  = '' THEN 'native.country' END,
               CASE WHEN trim(coalesce(income, ''))          = '' THEN 'income' END)
    FROM stg_adult
    WHERE trim(coalesce(age, ''))           = ''
       OR trim(coalesce(workclass, ''))      = ''
       OR trim(coalesce(education, ''))      = ''
       OR trim(coalesce(marital_status, '')) = ''
       OR trim(coalesce(occupation, ''))     = ''
       OR trim(coalesce(relationship, ''))   = ''
       OR trim(coalesce(race, ''))           = ''
       OR trim(coalesce(sex, ''))            = ''
       OR trim(coalesce(native_country, '')) = ''
       OR trim(coalesce(income, ''))         = ''

    -- UNIQUENESS. Lab 6 rule 10.
    --
    -- record_key is a content hash plus the occurrence number of that content,
    -- so two identical source rows get #1 and #2 and both load. A key that
    -- appears twice in staging therefore means the keying itself failed, not
    -- that the data contained a duplicate.
    --
    -- This dataset has no business key and its 47 duplicate rows are kept
    -- deliberately, so this is the only row-level uniqueness statement that is
    -- true of it. Uniqueness of the loaded star is asserted separately, by
    -- scripts/quality_checks.py.
    UNION ALL
    SELECT record_key, min(source_file), min(source_row), 'duplicate_record_key',
           'record_key staged ' || CAST(count(*) AS VARCHAR) || ' times'
    FROM stg_adult
    GROUP BY record_key
    HAVING count(*) > 1
) v
WHERE NOT EXISTS (
    SELECT 1 FROM load_reject r
    WHERE r.record_key = v.record_key AND r.reason = v.reason
);


-- --------------------------------------------------- 1b. quarantine the rows
-- load_reject now holds the verdicts for this run. Copy the refused records
-- themselves into the holding table, so the question "what was in that row?"
-- is answerable without staging still being around.
--
-- Driven off load_reject rather than repeating the rule predicates, so the two
-- tables cannot disagree about what was refused. Idempotent on
-- (record_key, reason), like everything else here.

INSERT INTO quarantine (
    load_run_id, record_key, reason, detail, quarantined_at,
    source_file, source_row,
    age, workclass, fnlwgt, education, education_num, marital_status,
    occupation, relationship, race, sex, capital_gain, capital_loss,
    hours_per_week, native_country, income
)
SELECT r.load_run_id, r.record_key, r.reason, r.detail, now(),
       r.source_file, r.source_row,
       s.age, s.workclass, s.fnlwgt, s.education, s.education_num,
       s.marital_status, s.occupation, s.relationship, s.race, s.sex,
       s.capital_gain, s.capital_loss, s.hours_per_week, s.native_country,
       s.income
FROM load_reject r
JOIN stg_adult s ON s.record_key = r.record_key
WHERE r.load_run_id = $run_id
  AND NOT EXISTS (
      SELECT 1 FROM quarantine q
      WHERE q.record_key = r.record_key AND q.reason = r.reason
  );


-- ---------------------------------------------------- 2. dimension upserts
-- New members only. Existing surrogate keys are never renumbered, so keys
-- stay stable across runs and any fact row already loaded keeps pointing at
-- the same member. dim_education is seeded and deliberately not widened here.

INSERT INTO dim_workclass (workclass_sk, workclass, is_unknown)
SELECT coalesce((SELECT max(workclass_sk) FROM dim_workclass WHERE workclass_sk > 0), 0)
         + CAST(row_number() OVER (ORDER BY s.workclass) AS INTEGER),
       s.workclass, FALSE
FROM (SELECT DISTINCT workclass FROM stg_adult WHERE workclass <> '?' AND workclass IS NOT NULL) s
WHERE NOT EXISTS (SELECT 1 FROM dim_workclass d WHERE d.workclass = s.workclass);

INSERT INTO dim_marital_status (marital_status_sk, marital_status)
SELECT coalesce((SELECT max(marital_status_sk) FROM dim_marital_status WHERE marital_status_sk > 0), 0)
         + CAST(row_number() OVER (ORDER BY s.marital_status) AS INTEGER),
       s.marital_status
FROM (SELECT DISTINCT marital_status FROM stg_adult WHERE marital_status IS NOT NULL) s
WHERE NOT EXISTS (SELECT 1 FROM dim_marital_status d WHERE d.marital_status = s.marital_status);

INSERT INTO dim_occupation (occupation_sk, occupation, is_unknown, is_not_applicable)
SELECT coalesce((SELECT max(occupation_sk) FROM dim_occupation WHERE occupation_sk > 0), 0)
         + CAST(row_number() OVER (ORDER BY s.occupation) AS INTEGER),
       s.occupation, FALSE, FALSE
FROM (SELECT DISTINCT occupation FROM stg_adult WHERE occupation <> '?' AND occupation IS NOT NULL) s
WHERE NOT EXISTS (SELECT 1 FROM dim_occupation d WHERE d.occupation = s.occupation);

INSERT INTO dim_relationship (relationship_sk, relationship)
SELECT coalesce((SELECT max(relationship_sk) FROM dim_relationship WHERE relationship_sk > 0), 0)
         + CAST(row_number() OVER (ORDER BY s.relationship) AS INTEGER),
       s.relationship
FROM (SELECT DISTINCT relationship FROM stg_adult WHERE relationship IS NOT NULL) s
WHERE NOT EXISTS (SELECT 1 FROM dim_relationship d WHERE d.relationship = s.relationship);

INSERT INTO dim_race (race_sk, race)
SELECT coalesce((SELECT max(race_sk) FROM dim_race WHERE race_sk > 0), 0)
         + CAST(row_number() OVER (ORDER BY s.race) AS INTEGER),
       s.race
FROM (SELECT DISTINCT race FROM stg_adult WHERE race IS NOT NULL) s
WHERE NOT EXISTS (SELECT 1 FROM dim_race d WHERE d.race = s.race);

INSERT INTO dim_sex (sex_sk, sex)
SELECT coalesce((SELECT max(sex_sk) FROM dim_sex WHERE sex_sk > 0), 0)
         + CAST(row_number() OVER (ORDER BY s.sex) AS INTEGER),
       s.sex
FROM (SELECT DISTINCT sex FROM stg_adult WHERE sex IN ('Male', 'Female')) s
WHERE NOT EXISTS (SELECT 1 FROM dim_sex d WHERE d.sex = s.sex);

INSERT INTO dim_native_country (native_country_sk, native_country, is_unknown)
SELECT coalesce((SELECT max(native_country_sk) FROM dim_native_country WHERE native_country_sk > 0), 0)
         + CAST(row_number() OVER (ORDER BY s.native_country) AS INTEGER),
       s.native_country, FALSE
FROM (SELECT DISTINCT native_country FROM stg_adult WHERE native_country <> '?' AND native_country IS NOT NULL) s
WHERE NOT EXISTS (SELECT 1 FROM dim_native_country d WHERE d.native_country = s.native_country);


-- --------------------------------------------------------- 3. fact insert
-- The anti-join on record_key at the bottom is what makes this idempotent.
-- A second run finds every key already present and inserts nothing.

INSERT INTO fact_person
WITH admissible AS (
    SELECT s.*
    FROM stg_adult s
    WHERE NOT EXISTS (SELECT 1 FROM load_reject r WHERE r.record_key = s.record_key)
      AND NOT EXISTS (SELECT 1 FROM fact_person f WHERE f.record_key = s.record_key)
),
-- Duplicate groups are computed over everything already modelled plus what is
-- arriving, so a duplicate spread across two file drops is still recognised.
universe AS (
    SELECT record_key, source_row,
           substr(record_key, 1, 32) AS content_hash
    FROM admissible
    UNION ALL
    SELECT record_key, source_row, substr(record_key, 1, 32)
    FROM fact_person
),
group_sizes AS (
    SELECT content_hash, count(*) AS group_size, min(source_row) AS first_row
    FROM universe GROUP BY content_hash
),
dup_groups AS (
    SELECT content_hash,
           CAST(row_number() OVER (ORDER BY first_row) AS INTEGER) AS dup_group
    FROM group_sizes WHERE group_size > 1
),
numbered AS (
    SELECT a.*,
           d.dup_group,
           CASE WHEN d.dup_group IS NOT NULL
                THEN CAST(row_number() OVER (PARTITION BY substr(a.record_key, 1, 32)
                                             ORDER BY a.source_row) AS SMALLINT)
           END AS seq_in_group,
           CAST(coalesce((SELECT max(person_sk) FROM fact_person), 0)
                + row_number() OVER (ORDER BY a.source_file, a.source_row) AS BIGINT) AS new_person_sk
    FROM admissible a
    LEFT JOIN dup_groups d ON d.content_hash = substr(a.record_key, 1, 32)
)
SELECT
    n.new_person_sk,
    n.record_key,
    n.load_run_id,
    n.source_file,
    n.source_row,

    coalesce(w.workclass_sk, -1),
    e.education_sk,
    ms.marital_status_sk,
    -- '?' with a real workclass of Never-worked is 'Not applicable' (-2).
    -- '?' otherwise is 'Unknown' (-1).
    CASE
        WHEN n.occupation <> '?'            THEN o.occupation_sk
        WHEN n.workclass  =  'Never-worked' THEN -2
        ELSE -1
    END,
    r.relationship_sk,
    ra.race_sk,
    sx.sex_sk,
    coalesce(nc.native_country_sk, -1),

    -- age 90 is a top code: 43 records sit at 90 and none at 89.
    CASE WHEN try_cast(n.age AS SMALLINT) = 90 THEN NULL ELSE try_cast(n.age AS SMALLINT) END,
    try_cast(n.age AS SMALLINT) = 90,

    -- hours.per.week 99 is a top code: 85 records at 99 against 11 at 98.
    CASE WHEN try_cast(n.hours_per_week AS SMALLINT) = 99 THEN NULL ELSE try_cast(n.hours_per_week AS SMALLINT) END,
    try_cast(n.hours_per_week AS SMALLINT) = 99,

    -- capital.gain 99999 is a censoring sentinel: 159 records, next real value
    -- 41,310, and every one of them in the high income bracket.
    CASE WHEN try_cast(n.capital_gain AS INTEGER) = 99999 THEN NULL ELSE try_cast(n.capital_gain AS INTEGER) END,
    try_cast(n.capital_gain AS INTEGER) = 99999,

    -- capital.loss has no sentinel. Its maximum of 4,356 sits in a smooth
    -- distribution, so it loads unaltered.
    try_cast(n.capital_loss AS INTEGER),

    try_cast(n.fnlwgt AS INTEGER),
    n.income = '>50K',

    n.dup_group,
    n.seq_in_group
FROM numbered n
LEFT JOIN dim_workclass      w  ON w.workclass       = n.workclass
JOIN      dim_education      e  ON e.education       = n.education
JOIN      dim_marital_status ms ON ms.marital_status = n.marital_status
LEFT JOIN dim_occupation     o  ON o.occupation      = n.occupation
JOIN      dim_relationship   r  ON r.relationship    = n.relationship
JOIN      dim_race           ra ON ra.race           = n.race
JOIN      dim_sex            sx ON sx.sex            = n.sex
LEFT JOIN dim_native_country nc ON nc.native_country = n.native_country;
