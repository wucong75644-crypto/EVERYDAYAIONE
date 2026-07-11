import { ImagePlus, Trash2 } from 'lucide-react';
import { useRef } from 'react';
import type { DetailImageCategory, DetailLocalImage } from '../../types/detailPage';
import { Button } from '../ui/Button';

interface ProductImageSectionProps {
  images: DetailLocalImage[];
  error: string | null;
  disabled?: boolean;
  onAdd: (category: DetailImageCategory, files: File[]) => void;
  onRemove: (id: string) => void;
}

const ACCEPTED_TYPES = 'image/jpeg,image/png,image/webp';

function ImageGroup({
  category,
  title,
  required,
  description,
  images,
  disabled,
  onAdd,
  onRemove,
}: {
  category: DetailImageCategory;
  title: string;
  required?: boolean;
  description: string;
  images: DetailLocalImage[];
  disabled: boolean;
  onAdd: ProductImageSectionProps['onAdd'];
  onRemove: ProductImageSectionProps['onRemove'];
}) {
  const inputRef = useRef<HTMLInputElement>(null);

  return (
    <div>
      <div className="flex items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-medium text-[var(--s-text-primary)]">
            {title}{required && <span className="text-[var(--s-error)]"> *</span>}
          </h3>
          <p className="mt-0.5 text-xs text-[var(--s-text-tertiary)]">{description}</p>
        </div>
        <Button variant="secondary" size="sm" icon={<ImagePlus className="w-4 h-4" />} disabled={disabled} onClick={() => inputRef.current?.click()}>
          上传
        </Button>
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
      <div className="mt-3 grid grid-cols-3 gap-2">
        {images.map((image) => (
          <div key={image.id} className="group relative aspect-square rounded-[var(--s-radius-control)] overflow-hidden border border-[var(--s-border-default)] bg-[var(--s-surface-secondary)]">
            <img src={image.previewUrl} alt={`${title} ${image.file.name}`} className="w-full h-full object-cover" />
            {!disabled && (
              <button type="button" onClick={() => onRemove(image.id)} className="absolute top-1 right-1 p-1 rounded-full bg-[var(--s-surface-card)] text-[var(--s-error)] shadow-[var(--s-shadow-whisper)]" aria-label={`删除 ${image.file.name}`}>
                <Trash2 className="w-3.5 h-3.5" aria-hidden="true" />
              </button>
            )}
          </div>
        ))}
        {!images.length && (
          <button type="button" disabled={disabled} onClick={() => inputRef.current?.click()} className="col-span-3 min-h-24 rounded-[var(--s-radius-control)] border border-dashed border-[var(--s-border-default)] text-sm text-[var(--s-text-tertiary)] hover:bg-[var(--s-hover)] disabled:pointer-events-none disabled:opacity-50">
            <ImagePlus className="w-5 h-5 mx-auto mb-1" aria-hidden="true" />
            点击上传{title}
          </button>
        )}
      </div>
    </div>
  );
}

export function ProductImageSection({ images, error, disabled = false, onAdd, onRemove }: ProductImageSectionProps) {
  const productImages = images.filter((image) => image.category === 'product');
  const referenceImages = images.filter((image) => image.category === 'reference');

  return (
    <section aria-labelledby="product-images-title">
      <div className="flex items-center justify-between">
        <h2 id="product-images-title" className="font-semibold">产品图片</h2>
        <span className="text-xs text-[var(--s-text-tertiary)]">{images.length} / 9</span>
      </div>
      <div className="mt-4 space-y-5">
        <ImageGroup category="product" title="产品图" required description="用于识别产品外观、包装和结构" images={productImages} disabled={disabled} onAdd={onAdd} onRemove={onRemove} />
        <ImageGroup category="reference" title="参考图" description="用于参考氛围、构图、风格或细节" images={referenceImages} disabled={disabled} onAdd={onAdd} onRemove={onRemove} />
      </div>
      {error && <p className="mt-3 text-xs text-[var(--s-error)]" role="alert">{error}</p>}
      <p className="mt-3 text-xs text-[var(--s-text-tertiary)]">两个板块合计最多 9 张，至少上传 1 张产品图</p>
    </section>
  );
}
