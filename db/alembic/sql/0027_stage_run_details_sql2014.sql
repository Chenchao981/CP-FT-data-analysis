-- Shared immutable measurements retain their IDs and evaluation foreign keys.
-- Run-scoped identity permits repeated uploads and real test attempts.
ALTER TABLE test.test_run ADD CONSTRAINT UQ_test_run_stage UNIQUE(run_id,test_stage);
ALTER TABLE mdm.spec_set ADD CONSTRAINT UQ_spec_set_stage UNIQUE(spec_set_id,test_stage);
GO
CREATE TABLE test.cp_run_detail (
    run_id bigint NOT NULL CONSTRAINT PK_cp_run_detail PRIMARY KEY,
    test_stage varchar(16) NOT NULL CONSTRAINT DF_cp_run_detail_stage DEFAULT('CP'),
    raw_wafer_id nvarchar(64) NULL,
    source_group nvarchar(128) NULL,
    source_lot_run nvarchar(128) NULL,
    source_spec_set_id bigint NULL,
    CONSTRAINT CK_cp_run_detail_stage CHECK(test_stage='CP'),
    CONSTRAINT FK_cp_run_detail_run FOREIGN KEY(run_id,test_stage)
        REFERENCES test.test_run(run_id,test_stage) ON DELETE CASCADE,
    CONSTRAINT FK_cp_run_detail_spec FOREIGN KEY(source_spec_set_id,test_stage) REFERENCES mdm.spec_set(spec_set_id,test_stage)
);
GO
CREATE TABLE test.ft_run_detail (
    run_id bigint NOT NULL CONSTRAINT PK_ft_run_detail PRIMARY KEY,
    test_stage varchar(16) NOT NULL CONSTRAINT DF_ft_run_detail_stage DEFAULT('FT'),
    source_id nvarchar(256) NOT NULL,
    source_file nvarchar(1024) NULL,
    manufacturing_lot nvarchar(128) NULL,
    test_tag nvarchar(128) NULL,
    test_file_name nvarchar(128) NULL,
    source_segment nvarchar(128) NULL,
    source_format nvarchar(64) NULL,
    metadata_lot nvarchar(128) NULL,
    source_spec_set_id bigint NULL,
    CONSTRAINT CK_ft_run_detail_stage CHECK(test_stage='FT'),
    CONSTRAINT CK_ft_run_detail_source CHECK(LEN(LTRIM(RTRIM(source_id)))>0 AND (source_file IS NULL OR LEN(LTRIM(RTRIM(source_file)))>0)),
    CONSTRAINT FK_ft_run_detail_run FOREIGN KEY(run_id,test_stage)
        REFERENCES test.test_run(run_id,test_stage) ON DELETE CASCADE,
    CONSTRAINT FK_ft_run_detail_spec FOREIGN KEY(source_spec_set_id,test_stage) REFERENCES mdm.spec_set(spec_set_id,test_stage)
);
GO
CREATE INDEX IX_ft_run_detail_source ON test.ft_run_detail(source_id,run_id)
    INCLUDE(manufacturing_lot,test_tag,source_spec_set_id);
CREATE INDEX IX_ft_run_detail_manufacturing ON test.ft_run_detail(manufacturing_lot,run_id)
    INCLUDE(source_id,test_tag) WHERE manufacturing_lot IS NOT NULL;
GO
CREATE VIEW test.v_cp_die AS
SELECT u.unit_id,u.run_id,r.processing_run_id,r.lot_id,u.wafer_id,
       d.raw_wafer_id,d.source_group,d.source_lot_run,d.source_spec_set_id,
       u.x_coord,u.y_coord,u.unit_sequence,u.attempt_no,u.logical_unit_key,
       u.soft_bin,u.hard_bin,u.overall_result,u.site_no,u.source_row_no
FROM test.unit_result u
JOIN test.test_run r ON r.run_id=u.run_id AND r.test_stage='CP'
JOIN test.cp_run_detail d ON d.run_id=r.run_id;
GO
CREATE VIEW test.v_ft_device AS
SELECT u.unit_id,u.run_id,r.processing_run_id,r.lot_id,d.source_id,d.source_file,
       d.manufacturing_lot,d.test_tag,d.test_file_name,d.source_segment,d.source_format,
       d.metadata_lot,d.source_spec_set_id,u.unit_sequence,u.attempt_no,u.logical_unit_key,
       u.vendor_unit_id,u.serial_no,u.site_no,u.soft_bin,u.hard_bin,u.overall_result,u.source_row_no
FROM test.unit_result u
JOIN test.test_run r ON r.run_id=u.run_id AND r.test_stage='FT'
JOIN test.ft_run_detail d ON d.run_id=r.run_id;
GO
CREATE VIEW test.v_cp_measurement AS
SELECT m.measurement_id,m.unit_id,m.test_item_id,m.value_numeric,m.value_text,
       m.raw_value,m.measurement_status,m.tester_pass_flag,m.source_column_index,
       d.run_id,d.processing_run_id,d.lot_id,d.wafer_id,d.x_coord,d.y_coord,d.source_spec_set_id
FROM test.measurement m JOIN test.v_cp_die d ON d.unit_id=m.unit_id;
GO
CREATE VIEW test.v_ft_measurement AS
SELECT m.measurement_id,m.unit_id,m.test_item_id,m.value_numeric,m.value_text,
       m.raw_value,m.measurement_status,m.tester_pass_flag,m.source_column_index,
       d.run_id,d.processing_run_id,d.lot_id,d.source_id,d.source_spec_set_id
FROM test.measurement m JOIN test.v_ft_device d ON d.unit_id=m.unit_id;
