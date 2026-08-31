SET NOCOUNT ON;
SET XACT_ABORT ON;
GO

IF OBJECT_ID(N'evaluation.rule_set', N'U') IS NULL
    RAISERROR('sql2014_0022 blocked: evaluation.rule_set is missing.',16,1);
IF COL_LENGTH(N'delivery.export_job', N'attempt_count') IS NULL
    RAISERROR('sql2014_0022 blocked: sql2014_0021 analytics export lifecycle is missing.',16,1);
GO

/*
  Forward-only governance extension. Existing rows remain valid. New candidate
  types still require a DRAFT Version, B/T/Q approvals, and an explicit
  Activation before any formal calculation can run.
*/
IF EXISTS(
    SELECT 1 FROM sys.check_constraints
    WHERE parent_object_id=OBJECT_ID(N'evaluation.rule_set')
      AND name=N'CK_evaluation_rule_type'
)
    ALTER TABLE evaluation.rule_set DROP CONSTRAINT CK_evaluation_rule_type;
GO

ALTER TABLE evaluation.rule_set WITH CHECK ADD CONSTRAINT CK_evaluation_rule_type CHECK(
    evaluation_type IN(
        'SPEC','BIN','PAT','SBL','SYL','CPK','SPC','HISTOGRAM','BOX_PLOT',
        'NORMAL_FIT','CORRELATION','MARGIN','ZONE','BIN_COOCCURRENCE',
        'PASS_FAIL_DISTRIBUTION','OTHER'
    )
);
GO

ALTER TABLE evaluation.rule_set CHECK CONSTRAINT CK_evaluation_rule_type;
GO

/* Alembic verifies its version-row UPDATE through pyodbc rowcount. */
SET NOCOUNT OFF;
GO
