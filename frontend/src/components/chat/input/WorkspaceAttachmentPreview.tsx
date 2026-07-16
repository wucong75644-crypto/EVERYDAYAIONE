import type { WorkspaceFile } from '../../../services/workspace';
import { categorize } from '../../../utils/fileCategory';
import { getFileIcon } from '../../../utils/fileUtils';
import { toDisplayThumbnailUrl } from '../../../utils/imageUrlRules';

interface WorkspaceAttachmentPreviewProps {
  files: WorkspaceFile[];
  onRemove?: (workspacePath: string) => void;
}

/** 在输入草稿中按媒体语义预览工作区附件。 */
export default function WorkspaceAttachmentPreview({
  files,
  onRemove,
}: WorkspaceAttachmentPreviewProps) {
  return files.map((file) => {
    const isImage = categorize(file) === 'image';
    const imageUrl = isImage ? toDisplayThumbnailUrl(null, file.cdn_url) : '';
    return (
      <div
        key={file.workspace_path}
        className="shrink-0 relative flex items-center gap-2 rounded-lg border border-[var(--s-accent)] bg-[var(--s-accent-soft)] px-3 py-2 text-sm"
      >
        {imageUrl ? (
          <img
            src={imageUrl}
            alt={file.name}
            className="h-10 w-10 shrink-0 rounded-md object-cover"
          />
        ) : (
          <span className="text-base shrink-0">{getFileIcon(file.name)}</span>
        )}
        <span className="truncate max-w-[160px] font-medium text-[var(--s-text-primary)]">
          {file.name}
        </span>
        {onRemove && (
          <button
            type="button"
            onClick={() => onRemove(file.workspace_path)}
            className="shrink-0 rounded p-0.5 text-[var(--s-text-tertiary)] hover:text-[var(--s-text-primary)] transition-colors"
            title="移除"
            aria-label={`移除 ${file.name}`}
          >
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        )}
      </div>
    );
  });
}
