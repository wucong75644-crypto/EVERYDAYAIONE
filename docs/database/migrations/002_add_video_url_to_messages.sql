-- Add video_url column to messages table for video generation support
-- Migration: 002_add_video_url_to_messages
-- Created: 2026-01-24

-- Add video_url column to messages table
ALTER TABLE messages
ADD COLUMN IF NOT EXISTS video_url TEXT;

-- Add comment to document the column purpose
COMMENT ON COLUMN messages.video_url IS 'URL of AI-generated video (image-to-video or text-to-video)';
