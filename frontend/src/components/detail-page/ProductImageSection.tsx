import { FolderOpen, ImagePlus, Trash2 } from 'lucide-react';
import { useRef, useState } from 'react';
import type { DetailImageCategory, DetailLocalImage } from '../../types/detailPage';
import { Button } from '../ui/Button';
import { WorkspaceImagePicker } from './WorkspaceImagePicker';

interface ProductImageSectionProps {
  images: DetailLocalImage[];
  error: string | null;
  disabled?: boolean;
  onAdd: (category: DetailImageCategory, files: File[]) => void;
  onWorkspaceAdd: (category: DetailImageCategory, paths: string[]) => void;
  onRemove: (id: string) => void;
}

const ACCEPTED_TYPES = 'image/jpeg,image/png,image/webp';

function ImageGroup({
  category,
  title,
  required,
  description,
  images,
  remaining,
  disabled,
  onAdd,
  onWorkspaceAdd,
  onRemove,
}: {
  category: DetailImageCategory;
  title: string;
  required?: boolean;
  description: string;
  images: DetailLocalImage[];
  remaining: number;
  disabled: boolean;
  onAdd: ProductImageSectionProps['onAdd'];
  onWorkspaceAdd: ProductImageSectionProps['onWorkspaceAdd'];
  onRemove: ProductImageSectionProps['onRemove'];
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [pickerOpen, setPickerOpen] = useState(false);

  return (
    <div>
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h3 className="text-sm font-medium text-[var(--s-text-primary)]">
            {title}{required && <span className="text-[var(--s-error)]"> *</span>}
          </h3>
          <p className="mt-0.5 text-xs text-[var(--s-text-tertiary)]">{description}</p>
        </div>
        <div className="flex shrink-0 gap-2 whitespace-nowrap"><Button variant="secondary" size="sm" icon={<FolderOpen className="w-4 h-4" />} disabled={disabled} onClick={() => setPickerOpen(true)}>工作区</Button><Button variant="secondary" size="sm" icon={<ImagePlus className="w-4 h-4" />} disabled={disabled} onClick={() => inputRef.current?.click()}>上传</Button></div>
      </div>
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPTED_TYPES}
        multiple
        disabled={disabled}
        className="hidden"
        aria-label={`上传${title}`}
        onChange={(event) => {
          const files = Array.from(event.target.files ?? []);
          if (files.length) onAdd(category, files);
          event.target.value = '';
        }}
      />
      <div className="mt-2 grid grid-cols-4 gap-2 max-h-[196px] overflow-y-auto overscroll-contain pr-1">
        {images.map((image) => (
          <div key={image.id} className="group relative aspect-square rounded-[var(--s-radius-control)] overflow-hidden border border-[var(--s-border-default)] bg-[var(--s-surface-secondary)]">
            {(() => {
              const imageName = image.name || image.file?.name || '图片';
              return <>
                {image.previewUrl ? <img src={image.previewUrl} alt={`${title} ${imageName}`} className="w-full h-full object-cover" /> : <div className="w-full h-full flex items-center justify-center text-xs text-[var(--s-text-tertiary)]">{image.status === 'missing' ? '原图缺失' : '处理中'}</div>}
                {(image.status === 'uploading' || image.status === 'attaching') && <div className="absolute inset-x-0 bottom-0 py-1 text-center text-[11px] text-white bg-black/55">{image.status === 'uploading' ? '上传中…' : '保存中…'}</div>}
            {!disabled && (
              <button type="button" onClick={() => void onRemove(image.id)} className="absolute top-1 right-1 p-1 rounded-full bg-[var(--s-surface-card)] text-[var(--s-error)] shadow-[var(--s-shadow-whisper)]" aria-label={`删除 ${imageName}`}>
                <Trash2 className="w-3.5 h-3.5" aria-hidden="true" />
              </button>
            )}
              </>;
            })()}
          </div>
        ))}
        {!images.length && (
          <button type="button" disabled={disabled} onClick={() => inputRef.current?.click()} className="col-span-4 min-h-20 lg:min-h-[72px] rounded-[var(--s-radius-control)] border border-dashed border-[var(--s-border-default)] text-sm text-[var(--s-text-tertiary)] hover:bg-[var(--s-hover)] disabled:pointer-events-none disabled:opacity-50">
            <ImagePlus className="w-5 h-5 mx-auto mb-1" aria-hidden="true" />
            点击上传{title}
          </button>
        )}
      </div>
      <WorkspaceImagePicker open={pickerOpen} remaining={remaining} onClose={() => setPickerOpen(false)} onSelect={(paths) => onWorkspaceAdd(category, paths)} />
    </div>
  );
}

export function ProductImageSection({ images, error, disabled = false, onAdd, onWorkspaceAdd, onRemove }: ProductImageSectionProps) {
  const productImages = images.filter((image) => image.category === 'product');
  const referenceImages = images.filter((image) => image.category === 'reference');

  return (
    <section aria-labelledby="product-images-title">
      <div className="flex items-center justify-between">
        <h2 id="product-images-title" className="font-semibold">产品图片</h2>
        <span className="text-xs text-[var(--s-text-tertiary)]">{images.length} / 9</span>
      </div>
      <div className="mt-3 space-y-4">
        <ImageGroup category="product" title="产品图" required description="用于识别产品外观、包装和结构" images={productImages} remaining={9 - images.length} disabled={disabled} onAdd={onAdd} onWorkspaceAdd={onWorkspaceAdd} onRemove={onRemove} />
        <ImageGroup category="reference" title="参考图" description="用于参考氛围、构图、风格或细节" images={referenceImages} remaining={9 - images.length} disabled={disabled} onAdd={onAdd} onWorkspaceAdd={onWorkspaceAdd} onRemove={onRemove} />
      </div>
      {error && <p className="mt-3 text-xs text-[var(--s-error)]" role="alert">{error}</p>}
      <p className="mt-2 text-xs text-[var(--s-text-tertiary)]">两个板块合计最多 9 张，至少上传 1 张产品图</p>
    </section>
  );
}
