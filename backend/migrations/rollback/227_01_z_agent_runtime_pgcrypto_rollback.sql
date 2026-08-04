-- Drop only when PostgreSQL confirms no remaining object depends on pgcrypto.
DROP EXTENSION IF EXISTS pgcrypto;
