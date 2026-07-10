import { useState } from 'react';
import { toThumbnailImageUrl } from '../../../utils/imageUrlRules';

interface InlineChartImageProps {
  url: string;
  alt: string;
  width?: number;
  height?: number;
  onClick: () => void;
}

/** 元数据驱动的固定占位图片：先按宽高预留空间，加载后直接替换。 */
export default function InlineChartImage({
  url,
  alt,
  width,
  height,
  onClick,
}: InlineChartImageProps) {
  const [loaded, setLoaded] = useState(false);
  const maxW = 500;
  const displayW = width ? Math.min(width, maxW) : maxW;
  const displayH = (width && height) ? Math.round(displayW * height / width) : undefined;

  return (
    <div className="my-3" style={{ width: displayW }}>
      {!loaded && (
        <div
          className="rounded-xl flex items-center justify-center"
          style={{
            width: displayW,
            height: displayH || 120,
            backgroundColor: '#27272a',
          }}
        >
          <svg className="w-8 h-8 text-zinc-500" xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"
            strokeLinecap="round" strokeLinejoin="round"
          >
            <rect width="18" height="18" x="3" y="3" rx="2" ry="2" />
            <circle cx="9" cy="9" r="2" />
            <path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21" />
          </svg>
        </div>
      )}
      <img
        src={toThumbnailImageUrl(url, displayW)}
        alt={alt}
        className={`rounded-xl shadow-sm w-full h-auto cursor-pointer ${loaded ? '' : 'hidden'}`}
        onClick={onClick}
        onLoad={() => setLoaded(true)}
      />
    </div>
  );
}
