SET XACT_ABORT ON;
GO

ALTER TABLE mdm.test_program DROP CONSTRAINT UQ_test_program;
GO
ALTER TABLE mdm.test_program DROP CONSTRAINT FK_test_program_product;
GO
ALTER TABLE mdm.test_program ALTER COLUMN product_id bigint NULL;
GO
ALTER TABLE mdm.test_program ADD CONSTRAINT FK_test_program_product
    FOREIGN KEY(product_id) REFERENCES mdm.product(product_id);
GO
ALTER TABLE mdm.test_program ADD CONSTRAINT UQ_test_program
    UNIQUE(supplier_id,product_id,test_stage,program_code);
GO
ALTER TABLE mdm.test_program ADD CONSTRAINT CK_test_program_stage_identity
    CHECK(test_stage<>'FT' OR product_id IS NOT NULL);
GO

DROP INDEX IX_test_run_lot_wafer ON test.test_run;
GO
ALTER TABLE test.test_run DROP CONSTRAINT FK_test_run_product;
GO
ALTER TABLE test.test_run ALTER COLUMN product_id bigint NULL;
GO
ALTER TABLE test.test_run ALTER COLUMN lot_id nvarchar(128) NULL;
GO
ALTER TABLE test.test_run ADD CONSTRAINT FK_test_run_product
    FOREIGN KEY(product_id) REFERENCES mdm.product(product_id);
GO
ALTER TABLE test.test_run ADD CONSTRAINT CK_test_run_stage_identity
    CHECK(
        (test_stage='CP' AND NULLIF(LTRIM(RTRIM(lot_id)),'') IS NOT NULL)
        OR (test_stage='FT' AND product_id IS NOT NULL)
        OR test_stage NOT IN('CP','FT')
    );
GO
CREATE NONCLUSTERED INDEX IX_test_run_lot_wafer
    ON test.test_run(lot_id,wafer_id)
    INCLUDE(product_id,test_stage,started_at_utc,run_attempt_no);
GO
