SET XACT_ABORT ON;
GO

/* Route A uses one explicit first-batch Spec for the whole Dataset Version. */
ALTER TABLE dataset.dataset_version ADD spec_set_id bigint NULL;
GO
ALTER TABLE dataset.dataset_version ADD CONSTRAINT FK_dataset_version_spec_set
    FOREIGN KEY(spec_set_id) REFERENCES mdm.spec_set(spec_set_id);
GO

/* Let the upload result open the automatically published Dataset directly. */
ALTER TABLE ingestion.processing_result_summary ADD dataset_id bigint NULL;
ALTER TABLE ingestion.processing_result_summary ADD dataset_version_no int NULL;
GO
ALTER TABLE ingestion.processing_result_summary ADD CONSTRAINT FK_result_summary_dataset
    FOREIGN KEY(dataset_id) REFERENCES dataset.dataset(dataset_id);
ALTER TABLE ingestion.processing_result_summary ADD CONSTRAINT CK_result_summary_dataset_version
    CHECK(dataset_version_no IS NULL OR dataset_version_no>0);
GO
CREATE NONCLUSTERED INDEX IX_result_summary_dataset
ON ingestion.processing_result_summary(dataset_id,dataset_version_no)
WHERE dataset_id IS NOT NULL;
GO
