SET NOCOUNT ON;
SET XACT_ABORT ON;
GO

IF EXISTS(
    SELECT 1 FROM workspace.analysis_session
    WHERE test_stage='CP'
)
BEGIN
    RAISERROR('sql2014_0025 downgrade blocked: CP Quick Analysis sessions exist.',16,1);
    RETURN;
END;
GO

ALTER TABLE workspace.analysis_session
DROP CONSTRAINT CK_analysis_session_stage;
ALTER TABLE workspace.analysis_session WITH CHECK
ADD CONSTRAINT CK_analysis_session_stage CHECK(test_stage IN('FT'));
GO

SET NOCOUNT OFF;
GO
