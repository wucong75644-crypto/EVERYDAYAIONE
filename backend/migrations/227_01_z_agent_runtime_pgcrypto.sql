-- Runtime catalog seeds use pgcrypto.digest for deterministic hashes.
CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;
