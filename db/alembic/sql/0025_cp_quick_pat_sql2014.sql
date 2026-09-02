SET NOCOUNT ON;
SET XACT_ABORT ON;
GO

IF EXISTS(
    SELECT 1 FROM workspace.analysis_session
    WHERE test_stage NOT IN('CP','FT')
)
BEGIN
    RAISERROR('sql2014_0025 blocked: Quick Analysis contains an unsupported test stage.',16,1);
    RETURN;
END;
GO

ALTER TABLE workspace.analysis_session
DROP CONSTRAINT CK_analysis_session_stage;
ALTER TABLE workspace.analysis_session WITH CHECK
ADD CONSTRAINT CK_analysis_session_stage CHECK(test_stage IN('CP','FT'));
GO

SET NOCOUNT OFF;
GO
