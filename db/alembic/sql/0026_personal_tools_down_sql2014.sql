SET NOCOUNT ON;
SET XACT_ABORT ON;
GO

IF EXISTS(
    SELECT 1 FROM workspace.analysis_session
    WHERE analysis_type <> 'QUICK_PAT'
)
BEGIN
    RAISERROR('sql2014_0026 downgrade blocked: non-PAT personal analysis sessions exist.',16,1);
    RETURN;
END;
GO

ALTER TABLE workspace.analysis_session
DROP CONSTRAINT CK_analysis_session_type;
ALTER TABLE workspace.analysis_session WITH CHECK
ADD CONSTRAINT CK_analysis_session_type CHECK(analysis_type IN('QUICK_PAT'));
GO

SET NOCOUNT OFF;
GO
