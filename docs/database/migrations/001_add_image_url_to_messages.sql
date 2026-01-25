-- ========================================
-- Migration: 001_add_image_url_to_messages
-- Date: 2026-01-23
-- Description: Add image_url column to messages table
-- ========================================

-- Add image_url column to messages table
ALTER TABLE messages
ADD COLUMN IF NOT EXISTS image_url VARCHAR(500);

-- Add comment for documentation
COMMENT ON COLUMN messages.image_url IS 'URL of uploaded or generated image associated with this message';
