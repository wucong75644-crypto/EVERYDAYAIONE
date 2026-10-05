-- Retain lifecycle RPCs/index while accepted, uncertain or unsettled tasks exist.
-- Application rollback must preserve completion, refunds and platform cost reporting.
SELECT 'Disable new acceptance; retain in-flight settlement and cost facts';
