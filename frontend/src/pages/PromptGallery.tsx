/**
 * AI 绘图提示词画廊
 *
 * 纯展示页面，从 public/data/prompt_gallery.json 加载数据。
 * 支持分类筛选、搜索、点击展开查看完整 prompt 并复制。
 */

import { useState, useMemo, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { ArrowLeft, Search, Sparkles } from 'lucide-react';
import { PageTransition } from '../components/motion';
import { Reveal } from '../components/motion/Reveal';
import GalleryTabs from '../components/gallery/GalleryTabs';
import PromptCard from '../components/gallery/PromptCard';
import type { PromptGalleryData } from '../components/gallery/types';

export default function PromptGallery() {
  const [data, setData] = useState<PromptGalleryData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [activeCategory, setActiveCategory] = useState('all');
  const [searchQuery, setSearchQuery] = useState('');

  // 加载数据
  useEffect(() => {
    let ignore = false;
    fetch('/data/prompt_gallery.json')
      .then((res) => {
        if (!res.ok) throw new Error('加载失败');
        return res.json();
      })
      .then((json: PromptGalleryData) => {
        if (ignore) return;
        setData(json);
        setLoading(false);
      })
      .catch((err) => {
        if (ignore) return;
        setError(err.message);
        setLoading(false);
      });
    return () => { ignore = true; };
  }, []);

  // 筛选
  const filteredPrompts = useMemo(() => {
    if (!data) return [];
    let result = data.prompts;

    if (activeCategory !== 'all') {
      result = result.filter((p) => p.category === activeCategory);
    }

    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      result = result.filter(
        (p) =>
          p.title.toLowerCase().includes(q) ||
          p.description.toLowerCase().includes(q) ||
          p.tags.some((t) => t.toLowerCase().includes(q)),
      );
    }

    return result;
  }, [data, activeCategory, searchQuery]);

  if (loading) {
    return (
      <PageTransition className="min-h-screen bg-surface flex flex-col">
        <div className="flex-1 flex items-center justify-center">
          <div className="text-text-tertiary">加载提示词库...</div>
        </div>
      </PageTransition>
    );
  }

  if (error || !data) {
    return (
      <PageTransition className="min-h-screen bg-surface flex flex-col">
        <div className="flex-1 flex items-center justify-center">
          <div className="text-error">加载失败: {error}</div>
        </div>
      </PageTransition>
    );
  }

  return (
    <PageTransition className="min-h-screen bg-surface flex flex-col">
      {/* 导航栏 */}
      <nav className="glass-subtle shadow-sm sticky top-0 z-20 border-b border-[var(--s-border-subtle)]">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex justify-between h-16 items-center gap-4">
            <div className="flex items-center gap-3 flex-1 min-w-0">
              <Link
                to="/"
                className="flex items-center gap-2 text-text-secondary hover:text-text-primary transition-colors shrink-0"
              >
                <ArrowLeft className="w-4 h-4" />
                <span className="text-xl font-bold text-text-primary">EVERYDAYAI</span>
              </Link>
              <div className="relative max-w-xs hidden sm:block">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-text-disabled" />
                <input
                  type="text"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder="搜索提示词..."
                  className="w-full pl-10 pr-4 py-2 rounded-xl border border-border-default text-text-primary bg-surface-card focus:outline-none focus:ring-2 focus:ring-focus-ring focus:border-focus-ring text-sm"
                />
              </div>
            </div>
          </div>

          {/* 移动端搜索框 */}
          <div className="pb-3 sm:hidden relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-text-disabled" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="搜索提示词..."
              className="w-full pl-10 pr-4 py-2 rounded-xl border border-border-default text-text-primary bg-surface-card focus:outline-none focus:ring-2 focus:ring-focus-ring focus:border-focus-ring text-sm"
            />
          </div>
        </div>
      </nav>

      {/* 标题区 */}
      <section className="py-8 sm:py-10 text-center">
        <Reveal>
          <div className="flex items-center justify-center gap-2 mb-2">
            <Sparkles className="w-5 h-5 text-accent" />
            <span className="text-xs font-medium text-accent uppercase tracking-wider">
              Prompt Library
            </span>
          </div>
          <h1 className="text-2xl sm:text-3xl font-bold text-text-primary font-heading">
            AI 绘图提示词库
          </h1>
        </Reveal>
        <Reveal delay={0.08}>
          <p className="text-sm sm:text-base text-text-tertiary mt-2">
            {data.prompts.length} 条精选电商设计提示词，点击卡片查看完整 Prompt 并复制使用
          </p>
        </Reveal>
      </section>

      {/* 分类标签 */}
      <GalleryTabs
        categories={data.categories}
        activeCategory={activeCategory}
        onCategoryChange={setActiveCategory}
        totalCount={data.prompts.length}
      />

      {/* 卡片网格 */}
      <div className="flex-1">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
          {filteredPrompts.length === 0 ? (
            <div className="py-20 text-center">
              <Search className="mx-auto w-12 h-12 text-text-disabled" />
              <p className="text-text-tertiary mt-4">未找到匹配的提示词</p>
            </div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
              {filteredPrompts.map((prompt) => (
                <PromptCard key={prompt.id} prompt={prompt} />
              ))}
            </div>
          )}
        </div>
      </div>

      {/* 底部来源说明 */}
      <footer className="border-t border-border-default py-6">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 text-center">
          <p className="text-xs text-text-disabled">
            提示词来源：
            <a
              href={data.source_repo}
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-accent transition-colors ml-1"
            >
              {data.source_repo.replace('https://github.com/', '')}
            </a>
            {' '}(MIT License)
          </p>
        </div>
      </footer>
    </PageTransition>
  );
}
