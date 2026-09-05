IF EXISTS(SELECT 1 FROM ingestion.ftp_collection_state) OR EXISTS(SELECT 1 FROM ingestion.ftp_package) OR EXISTS(SELECT 1 FROM ingestion.ftp_collection_run)
    RAISERROR('FTP collection has history. Preserve its provenance; downgrade is blocked.',16,1);
ELSE BEGIN
    DROP TABLE ingestion.ftp_package;
    DROP TABLE ingestion.ftp_collection_run;
    DROP TABLE ingestion.ftp_collection_state;
END;
GO
