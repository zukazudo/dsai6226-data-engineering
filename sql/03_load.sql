-- ---------------------------------------------------------------------------
-- 03_load.sql
--
-- Transforms stg_adult into the star. This is where every Lab 1 finding is
-- acted on, and it is the only place cleaning happens.
-- ---------------------------------------------------------------------------

DELETE FROM fact_person;
DELETE FROM dim_workclass;
DELETE FROM dim_education;
DELETE FROM dim_marital_status;
DELETE FROM dim_occupation;
DELETE FROM dim_relationship;
DELETE FROM dim_race;
DELETE FROM dim_sex;
DELETE FROM dim_native_country;

-- --------------------------------------------------------- dimension loads
-- Surrogate key -1 is 'Unknown' in every dimension. Real members are numbered
-- from 1 in alphabetical order so the keys are stable across rebuilds.

INSERT INTO dim_workclass (workclass_sk, workclass, is_unknown)
SELECT -1, 'Unknown', TRUE
UNION ALL
SELECT CAST(row_number() OVER (ORDER BY workclass) AS INTEGER), workclass, FALSE
FROM (SELECT DISTINCT workclass FROM stg_adult WHERE workclass <> '?');

INSERT INTO dim_education (education_sk, education, education_num, education_group)
SELECT
    CAST(row_number() OVER (ORDER BY education_num) AS INTEGER),
    education,
    education_num,
    CASE
        WHEN education_num <= 8  THEN 'No high school diploma'
        WHEN education_num  = 9  THEN 'High school graduate'
        WHEN education_num = 10  THEN 'Some college'
        WHEN education_num IN (11, 12) THEN 'Associate degree'
        WHEN education_num = 13  THEN 'Bachelors degree'
        ELSE 'Postgraduate'
    END
FROM (
    SELECT DISTINCT education, CAST(education_num AS SMALLINT) AS education_num
    FROM stg_adult
);

INSERT INTO dim_marital_status (marital_status_sk, marital_status)
SELECT CAST(row_number() OVER (ORDER BY marital_status) AS INTEGER), marital_status
FROM (SELECT DISTINCT marital_status FROM stg_adult);

-- occupation carries a third state. '?' means one of two different things and
-- the source file cannot tell them apart on its own: the seven records whose
-- workclass is Never-worked have no occupation to record, everyone else with
-- '?' declined to answer. Splitting them here is the whole argument for a
-- dimension table.
INSERT INTO dim_occupation (occupation_sk, occupation, is_unknown, is_not_applicable)
SELECT -1, 'Unknown', TRUE, FALSE
UNION ALL
SELECT -2, 'Not applicable', FALSE, TRUE
UNION ALL
SELECT CAST(row_number() OVER (ORDER BY occupation) AS INTEGER), occupation, FALSE, FALSE
FROM (SELECT DISTINCT occupation FROM stg_adult WHERE occupation <> '?');

INSERT INTO dim_relationship (relationship_sk, relationship)
SELECT CAST(row_number() OVER (ORDER BY relationship) AS INTEGER), relationship
FROM (SELECT DISTINCT relationship FROM stg_adult);

INSERT INTO dim_race (race_sk, race)
SELECT CAST(row_number() OVER (ORDER BY race) AS INTEGER), race
FROM (SELECT DISTINCT race FROM stg_adult);

INSERT INTO dim_sex (sex_sk, sex)
SELECT CAST(row_number() OVER (ORDER BY sex) AS INTEGER), sex
FROM (SELECT DISTINCT sex FROM stg_adult);

INSERT INTO dim_native_country (native_country_sk, native_country, is_unknown)
SELECT -1, 'Unknown', TRUE
UNION ALL
SELECT CAST(row_number() OVER (ORDER BY native_country) AS INTEGER), native_country, FALSE
FROM (SELECT DISTINCT native_country FROM stg_adult WHERE native_country <> '?');

-- -------------------------------------------------------------- fact load

INSERT INTO fact_person
WITH keyed AS (
    -- A duplicate is a row identical to another on all 15 source columns.
    -- source_row is excluded from the hash, since it is ours and not the
    -- source's.
    SELECT
        s.*,
        md5(concat_ws('|',
            age, workclass, fnlwgt, education, education_num, marital_status,
            occupation, relationship, race, sex, capital_gain, capital_loss,
            hours_per_week, native_country, income)) AS row_hash
    FROM stg_adult s
),
group_sizes AS (
    SELECT row_hash, count(*) AS group_size, min(source_row) AS first_row
    FROM keyed
    GROUP BY row_hash
),
dup_groups AS (
    -- Only groups with more than one member get an id, numbered 1..n in order
    -- of first appearance in the file.
    SELECT
        row_hash,
        CAST(row_number() OVER (ORDER BY first_row) AS INTEGER) AS dup_group
    FROM group_sizes
    WHERE group_size > 1
),
grouped AS (
    SELECT
        k.*,
        d.dup_group,
        CASE WHEN d.dup_group IS NOT NULL
             THEN CAST(row_number() OVER (PARTITION BY k.row_hash ORDER BY k.source_row) AS SMALLINT)
        END AS seq_in_group
    FROM keyed k
    LEFT JOIN dup_groups d USING (row_hash)
)
SELECT
    CAST(g.source_row AS BIGINT)                    AS person_sk,
    g.source_row,

    COALESCE(w.workclass_sk, -1),
    e.education_sk,
    ms.marital_status_sk,
    -- '?' with a real workclass of Never-worked is 'Not applicable' (-2).
    -- '?' otherwise is 'Unknown' (-1).
    CASE
        WHEN g.occupation <> '?'            THEN o.occupation_sk
        WHEN g.workclass  =  'Never-worked' THEN -2
        ELSE -1
    END                                             AS occupation_sk,
    r.relationship_sk,
    ra.race_sk,
    sx.sex_sk,
    COALESCE(nc.native_country_sk, -1),

    -- age 90 is a top code: 43 records sit at 90 and none at 89.
    CASE WHEN CAST(g.age AS SMALLINT) = 90 THEN NULL ELSE CAST(g.age AS SMALLINT) END,
    CAST(g.age AS SMALLINT) = 90,

    -- hours.per.week 99 is a top code: 85 records at 99 against 11 at 98.
    CASE WHEN CAST(g.hours_per_week AS SMALLINT) = 99 THEN NULL ELSE CAST(g.hours_per_week AS SMALLINT) END,
    CAST(g.hours_per_week AS SMALLINT) = 99,

    -- capital.gain 99999 is a censoring sentinel: 159 records, next real value
    -- 41,310, and every one of them in the high income bracket.
    CASE WHEN CAST(g.capital_gain AS INTEGER) = 99999 THEN NULL ELSE CAST(g.capital_gain AS INTEGER) END,
    CAST(g.capital_gain AS INTEGER) = 99999,

    -- capital.loss has no sentinel. Its maximum of 4,356 sits in a smooth
    -- distribution, so it loads unaltered.
    CAST(g.capital_loss AS INTEGER),

    CAST(g.fnlwgt AS INTEGER),
    g.income = '>50K',

    g.dup_group,
    g.seq_in_group
FROM grouped g
LEFT JOIN dim_workclass      w  ON w.workclass           = g.workclass
JOIN      dim_education      e  ON e.education           = g.education
JOIN      dim_marital_status ms ON ms.marital_status     = g.marital_status
LEFT JOIN dim_occupation     o  ON o.occupation          = g.occupation
JOIN      dim_relationship   r  ON r.relationship        = g.relationship
JOIN      dim_race           ra ON ra.race               = g.race
JOIN      dim_sex            sx ON sx.sex                = g.sex
LEFT JOIN dim_native_country nc ON nc.native_country     = g.native_country;
