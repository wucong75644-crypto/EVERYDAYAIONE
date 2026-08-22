import { useCallback, useMemo, useRef, useState } from 'react';
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

  const nextSequenceRef = useRef(0);
  const workspaceSequenceRef = useRef(new Map<string, number>());
  const [workspaceSequences, setWorkspaceSequences] = useState<Map<string, number>>(() => new Map());
  const allocateSequence = useCallback(() => nextSequenceRef.current++, []);

  const attachments = useMemo<ChatAttachment[]>(() => [
    ...images.map(fromUploadedImage),
    ...files.map(fromUploadedFile),
    ...workspaceFiles.map((file) => fromWorkspaceFile(
      file,
      workspaceSequences.get(file.workspace_path),
    )),
  ].sort((left, right) => (left.sequence ?? Number.MAX_SAFE_INTEGER)
    - (right.sequence ?? Number.MAX_SAFE_INTEGER)), [files, images, workspaceFiles, workspaceSequences]);

  const addLocalFiles = useCallback(async (
    incoming: File[],
    constraints: AttachmentConstraints = {},
  ) => {
    if (incoming.length === 0) return;
    const sequenceByFile = new Map<File, number>();
    incoming.forEach((file) => sequenceByFile.set(file, allocateSequence()));
    const images = incoming.filter((file) => categorize(file) === 'image');
    const files = incoming.filter((file) => categorize(file) !== 'image');
    await Promise.all([
      images.length > 0
        ? handleImageFiles(images, constraints.maxImages, constraints.maxImageSizeMB, sequenceByFile)
        : Promise.resolve(),
      files.length > 0
        ? handleFileUpload(files, constraints.maxFileSizeMB, sequenceByFile)
        : Promise.resolve(),
    ]);
  }, [allocateSequence, handleFileUpload, handleImageFiles]);

  const addQuotedImage = useCallback((input: QuotedImageInput) => {
    addQuotedImageToUpload({ ...input, sequence: allocateSequence() });
  }, [addQuotedImageToUpload, allocateSequence]);

  const addWorkspaceAttachment = useCallback((file: Parameters<typeof addWorkspaceFile>[0]) => {
    if (!workspaceSequenceRef.current.has(file.workspace_path)) {
      workspaceSequenceRef.current.set(file.workspace_path, allocateSequence());
      setWorkspaceSequences(new Map(workspaceSequenceRef.current));
    }
    addWorkspaceFile(file);
  }, [addWorkspaceFile, allocateSequence]);

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
      const workspacePath = id.slice('workspace:'.length);
      workspaceSequenceRef.current.delete(workspacePath);
      setWorkspaceSequences(new Map(workspaceSequenceRef.current));
      removeWorkspaceFile(workspacePath);
    }
  }, [handleRemoveFile, handleRemoveImage, removeWorkspaceFile]);

  const clearImages = useCallback(() => {
    workspaceFiles
      .filter((file) => categorize(file) === 'image')
      .forEach((file) => workspaceSequenceRef.current.delete(file.workspace_path));
    setWorkspaceSequences(new Map(workspaceSequenceRef.current));
    handleRemoveAllImages();
    clearWorkspaceImages();
  }, [clearWorkspaceImages, handleRemoveAllImages, workspaceFiles]);

  const detachForSubmission = useCallback((): AttachmentDraftTransaction => {
    const restoreImages = detachImagesForSubmission();
    const restoreFiles = detachFilesForSubmission();
    const restoreWorkspaceFiles = detachWorkspaceFiles();
    const detachedWorkspaceSequences = new Map<string, number>();
    workspaceFiles.forEach((file) => {
      const sequence = workspaceSequenceRef.current.get(file.workspace_path);
      if (sequence !== undefined) detachedWorkspaceSequences.set(file.workspace_path, sequence);
      workspaceSequenceRef.current.delete(file.workspace_path);
    });
    setWorkspaceSequences(new Map(workspaceSequenceRef.current));
    return {
      restore: () => {
        restoreImages();
        restoreFiles();
        restoreWorkspaceFiles();
        detachedWorkspaceSequences.forEach((sequence, workspacePath) => {
          if (!workspaceSequenceRef.current.has(workspacePath)) {
            workspaceSequenceRef.current.set(workspacePath, sequence);
          }
        });
        setWorkspaceSequences(new Map(workspaceSequenceRef.current));
      },
    };
  }, [detachFilesForSubmission, detachImagesForSubmission, detachWorkspaceFiles, workspaceFiles]);

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
    addWorkspaceFile: addWorkspaceAttachment,
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
