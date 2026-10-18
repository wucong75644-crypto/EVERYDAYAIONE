-- Required real columns: final types are explicitly declared by 094_varchar_to_text.sql.
ALTER TABLE erp_document_items ADD COLUMN supplier_code text;
ALTER TABLE erp_document_items_archive ADD COLUMN supplier_code text;
