import { useCallback, useMemo } from 'react';
import { useFileUpload } from '../../../hooks/useFileUpload';
import { useImageUpload } from '../../../hooks/useImageUpload';
import { categorize } from '../../../utils/fileCategory';
import { fromUploadedFile, fromUploadedImage, fromWorkspaceFile } from './attachmentAdapters';
import { createAttachmentSubmissionSnapshot } from './attachmentSubmission';
import type {
  AttachmentConstraints,
  AttachmentDraftTransaction,
  ChatAttachment,
  QuotedImageInput,
} from './ChatAttachment.types';
import { useWorkspaceAttachmentState } from './useWorkspaceAttachmentState';

export function useChatAttachments() {
  const {
    images, uploadError: imageUploadError,
    handleImageFiles, handleRemoveImage, handleRemoveAllImages,
    detachImagesForSubmission, addQuotedImage: addQuotedImageToUpload,
    clearUploadError: clearImageUploadError,
  } = useImageUpload();
  const {
    files, uploadError: fileUploadError,
    handleFileUpload, handleRemoveFile, detachFilesForSubmission,
    clearUploadError: clearFileUploadError,
  } = useFileUpload();
  const {
    workspaceFiles, addWorkspaceFile, removeWorkspaceFile,
    clearWorkspaceImages, detachWorkspaceFiles,
  } = useWorkspaceAttachmentState();

  const attachments = useMemo<ChatAttachment[]>(() => [
    ...images.map(fromUploadedImage),
    ...files.map(fromUploadedFile),
    ...workspaceFiles.map(fromWorkspaceFile),
  ], [files, images, workspaceFiles]);

  const addLocalFiles = useCallback(async (
    incoming: File[],
    constraints: AttachmentConstraints = {},
  ) => {
    if (incoming.length === 0) return;
    const images = incoming.filter((file) => categorize(file) === 'image');
    const files = incoming.filter((file) => categorize(file) !== 'image');
    await Promise.all([
      images.length > 0
        ? handleImageFiles(images, constraints.maxImages, constraints.maxImageSizeMB)
        : Promise.resolve(),
      files.length > 0
        ? handleFileUpload(files, constraints.maxFileSizeMB)
        : Promise.resolve(),
    ]);
  }, [handleFileUpload, handleImageFiles]);

  const addQuotedImage = useCallback((input: QuotedImageInput) => {
    addQuotedImageToUpload(input.url, input.thumbnailUrl);
  }, [addQuotedImageToUpload]);

  const removeAttachment = useCallback((id: string) => {
    if (id.startsWith('image:')) {
      handleRemoveImage(id.slice('image:'.length));
      return;
    }
    if (id.startsWith('file:')) {
      handleRemoveFile(id.slice('file:'.length));
      return;
    }
    if (id.startsWith('workspace:')) {
      removeWorkspaceFile(id.slice('workspace:'.length));
    }
  }, [handleRemoveFile, handleRemoveImage, removeWorkspaceFile]);

  const clearImages = useCallback(() => {
    handleRemoveAllImages();
    clearWorkspaceImages();
  }, [clearWorkspaceImages, handleRemoveAllImages]);

  const detachForSubmission = useCallback((): AttachmentDraftTransaction => {
    const restoreImages = detachImagesForSubmission();
    const restoreFiles = detachFilesForSubmission();
    const restoreWorkspaceFiles = detachWorkspaceFiles();
    return {
      restore: () => {
        restoreImages();
        restoreFiles();
        restoreWorkspaceFiles();
      },
    };
  }, [detachFilesForSubmission, detachImagesForSubmission, detachWorkspaceFiles]);

  const imageAttachments = attachments.filter((item) => item.kind === 'image');
  const submissionSnapshot = useMemo(
    () => createAttachmentSubmissionSnapshot(attachments),
    [attachments],
  );
  return {
    attachments,
    submissionSnapshot,
    addLocalFiles,
    addQuotedImage,
    addWorkspaceFile,
    removeAttachment,
    clearImages,
    detachForSubmission,
    isUploading: attachments.some((item) => item.status === 'uploading'),
    hasImages: imageAttachments.length > 0,
    hasQuotedImage: imageAttachments.some((item) => item.source === 'quote'),
    hasFiles: attachments.some((item) => item.kind === 'file'),
    readyImageCount: imageAttachments.filter((item) => item.status === 'ready').length,
    uploadError: imageUploadError || fileUploadError,
    clearUploadErrors: () => {
      clearImageUploadError();
      clearFileUploadError();
    },
  };
}
