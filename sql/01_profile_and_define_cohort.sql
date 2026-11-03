/*
  Optional SQL Server companion for the portfolio project.
  Point BULK INSERT at a local copy of UCI diabetic_data.csv.
  Keep this staging table local; never commit patient-level extracts.
  Tested syntax target: SQL Server 2017+ (CSV format support).
*/
IF OBJECT_ID(N'dbo.Diabetes130_Staging', N'U') IS NOT NULL
    DROP TABLE dbo.Diabetes130_Staging;
GO

CREATE TABLE dbo.Diabetes130_Staging (
    encounter_id NVARCHAR(100) NULL, patient_nbr NVARCHAR(100) NULL,
    race NVARCHAR(100) NULL, gender NVARCHAR(100) NULL, age NVARCHAR(100) NULL,
    weight NVARCHAR(100) NULL, admission_type_id NVARCHAR(100) NULL,
    discharge_disposition_id NVARCHAR(100) NULL, admission_source_id NVARCHAR(100) NULL,
    time_in_hospital NVARCHAR(100) NULL, payer_code NVARCHAR(100) NULL,
    medical_specialty NVARCHAR(100) NULL, num_lab_procedures NVARCHAR(100) NULL,
    num_procedures NVARCHAR(100) NULL, num_medications NVARCHAR(100) NULL,
    number_outpatient NVARCHAR(100) NULL, number_emergency NVARCHAR(100) NULL,
    number_inpatient NVARCHAR(100) NULL, diag_1 NVARCHAR(100) NULL,
    diag_2 NVARCHAR(100) NULL, diag_3 NVARCHAR(100) NULL,
    number_diagnoses NVARCHAR(100) NULL, max_glu_serum NVARCHAR(100) NULL,
    A1Cresult NVARCHAR(100) NULL, metformin NVARCHAR(100) NULL,
    repaglinide NVARCHAR(100) NULL, nateglinide NVARCHAR(100) NULL,
    chlorpropamide NVARCHAR(100) NULL, glimepiride NVARCHAR(100) NULL,
    acetohexamide NVARCHAR(100) NULL, glipizide NVARCHAR(100) NULL,
    glyburide NVARCHAR(100) NULL, tolbutamide NVARCHAR(100) NULL,
    pioglitazone NVARCHAR(100) NULL, rosiglitazone NVARCHAR(100) NULL,
    acarbose NVARCHAR(100) NULL, miglitol NVARCHAR(100) NULL,
    troglitazone NVARCHAR(100) NULL, tolazamide NVARCHAR(100) NULL,
    examide NVARCHAR(100) NULL, citoglipton NVARCHAR(100) NULL,
    insulin NVARCHAR(100) NULL, glyburide_metformin NVARCHAR(100) NULL,
    glipizide_metformin NVARCHAR(100) NULL, glimepiride_pioglitazone NVARCHAR(100) NULL,
    metformin_rosiglitazone NVARCHAR(100) NULL, metformin_pioglitazone NVARCHAR(100) NULL,
    change NVARCHAR(100) NULL, diabetesMed NVARCHAR(100) NULL,
    readmitted NVARCHAR(100) NULL
);
GO

-- Replace the path with a path readable by the SQL Server service account.
BULK INSERT dbo.Diabetes130_Staging
FROM 'C:\\path\\to\\diabetic_data.csv'
WITH (
    FORMAT = 'CSV',
    FIELDQUOTE = '"',
    FIRSTROW = 2,
    CODEPAGE = '65001',
    ROWTERMINATOR = '0x0a',
    TABLOCK
);
GO

-- Basic quality checks; these return aggregates only.
SELECT
    COUNT_BIG(*) AS encounter_rows,
    COUNT(DISTINCT patient_nbr) AS distinct_patients,
    SUM(CASE WHEN readmitted = '<30' THEN 1 ELSE 0 END) AS early_readmission_rows,
    SUM(CASE WHEN readmitted = '>30' THEN 1 ELSE 0 END) AS later_readmission_rows,
    SUM(CASE WHEN readmitted = 'NO' THEN 1 ELSE 0 END) AS no_readmission_rows,
    SUM(CASE WHEN readmitted IS NULL OR readmitted NOT IN ('<30', '>30', 'NO') THEN 1 ELSE 0 END) AS invalid_target_rows
FROM dbo.Diabetes130_Staging;
GO

-- The modeling cohort excludes death, hospice, and disposition 12 (still patient/
-- expected outpatient return). Transfers remain, but a transfer can continue care.
-- This view retains gender/race for subgroup audit only; exclude both from model features.
CREATE OR ALTER VIEW dbo.v_Diabetes130_ReadmissionCohort
AS
SELECT
    CASE WHEN readmitted = '<30' THEN CAST(1 AS bit) ELSE CAST(0 AS bit) END AS uci_label_lt30_early_readmission,
    TRY_CONVERT(int, time_in_hospital) AS time_in_hospital,
    TRY_CONVERT(int, num_lab_procedures) AS num_lab_procedures,
    TRY_CONVERT(int, num_procedures) AS num_procedures,
    TRY_CONVERT(int, num_medications) AS num_medications,
    TRY_CONVERT(int, number_outpatient) AS number_outpatient,
    TRY_CONVERT(int, number_emergency) AS number_emergency,
    TRY_CONVERT(int, number_inpatient) AS number_inpatient,
    race, gender, age, medical_specialty,
    admission_type_id, discharge_disposition_id, admission_source_id,
    diag_1, diag_2, diag_3, number_diagnoses,
    max_glu_serum, A1Cresult, metformin, repaglinide, nateglinide,
    chlorpropamide, glimepiride, acetohexamide, glipizide, glyburide,
    tolbutamide, pioglitazone, rosiglitazone, acarbose, miglitol,
    troglitazone, tolazamide, examide, citoglipton, insulin,
    glyburide_metformin, glipizide_metformin, glimepiride_pioglitazone,
    metformin_rosiglitazone, metformin_pioglitazone, [change], diabetesMed
FROM dbo.Diabetes130_Staging
WHERE readmitted IN ('<30', '>30', 'NO')
  AND TRY_CONVERT(int, discharge_disposition_id) NOT IN (11, 12, 13, 14, 19, 20, 21);
GO

-- Cohort size and class balance only; no row-level identifiers are returned.
SELECT
    COUNT_BIG(*) AS eligible_encounters,
    COUNT(DISTINCT patient_nbr) AS distinct_patients,
    SUM(CASE WHEN readmitted = '<30' THEN 1 ELSE 0 END) AS early_readmission_encounters,
    CAST(1.0 * SUM(CASE WHEN readmitted = '<30' THEN 1 ELSE 0 END) / NULLIF(COUNT_BIG(*), 0) AS DECIMAL(8,5)) AS early_readmission_rate
FROM dbo.Diabetes130_Staging
WHERE readmitted IN ('<30', '>30', 'NO')
  AND TRY_CONVERT(int, discharge_disposition_id) NOT IN (11, 12, 13, 14, 19, 20, 21);
GO
