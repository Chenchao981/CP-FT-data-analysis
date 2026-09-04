SET NOCOUNT ON;
SET XACT_ABORT ON;
GO

IF EXISTS(
    SELECT 1 FROM workspace.analysis_session
    WHERE analysis_type NOT IN(
        'QUICK_PAT','QUICK_CLEAN','QUICK_CHART','QUICK_SYL_SBL'
    )
)
BEGIN
    RAISERROR('sql2014_0026 blocked: Quick Analysis contains an unsupported analysis type.',16,1);
    RETURN;
END;
GO

ALTER TABLE workspace.analysis_session
DROP CONSTRAINT CK_analysis_session_type;
ALTER TABLE workspace.analysis_session WITH CHECK
ADD CONSTRAINT CK_analysis_session_type CHECK(analysis_type IN(
    'QUICK_PAT','QUICK_CLEAN','QUICK_CHART','QUICK_SYL_SBL'
));
GO

SET NOCOUNT OFF;
GO
