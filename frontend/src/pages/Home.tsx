/**
 * 首页
 *
 * 顶级导航切换「模型广场」和「AI 提示词」两个 section。
 * 模型广场：分类浏览、搜索、订阅管理和详情查看。
 * AI 提示词：电商设计提示词画廊，分类浏览、搜索、复制。
 */

import { useState, useMemo, useEffect, useCallback } from 'react';
import toast from 'react-hot-toast';
import { ALL_MODELS, type UnifiedModel } from '../constants/models';
import { useAuthStore } from '../stores/useAuthStore';
import { useAuthModalStore } from '../stores/useAuthModalStore';
import { useSubscriptionStore } from '../stores/useSubscriptionStore';
import Footer from '../components/Footer';
import NavBar from '../components/home/NavBar';
import type { HomeSection } from '../components/home/NavBar';
import HeroSection from '../components/home/HeroSection';
import CategoryTabs, { type TabValue } from '../components/home/CategoryTabs';
import ModelGrid from '../components/home/ModelGrid';
import ModelDetailDrawer from '../components/home/ModelDetailDrawer';
import UnsubscribeModal from '../components/home/UnsubscribeModal';
import GalleryTabs from '../components/gallery/GalleryTabs';
import PromptCard from '../components/gallery/PromptCard';
import type { PromptGalleryData } from '../components/gallery/types';
import { PageTransition } from '../components/motion';
import { Search } from 'lucide-react';

/** 排除智能模型 auto（路由层概念，非独立模型） */
const DISPLAY_MODELS = ALL_MODELS.filter((m) => m.id !== 'auto');

export default function Home() {
  const { isAuthenticated } = useAuthStore();
  const { openLogin, openRegister } = useAuthModalStore();
  const {
    isLoading,
    fetchModels,
    fetchSubscriptions,
    subscribe,
    unsubscribe,
    isSubscribed,
    isSubscribing,
  } = useSubscriptionStore();

  // 顶级 section 切换
  const [activeSection, setActiveSection] = useState<HomeSection>('models');

  const [searchQuery, setSearchQuery] = useState('');
  const [activeTab, setActiveTab] = useState<TabValue>('all');
  const [selectedModel, setSelectedModel] = useState<UnifiedModel | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [unsubModal, setUnsubModal] = useState<{ open: boolean; model: UnifiedModel | null }>({
    open: false,
    model: null,
  });
  const [unsubLoading, setUnsubLoading] = useState(false);

  // 提示词画廊数据
  const [galleryData, setGalleryData] = useState<PromptGalleryData | null>(null);
  const [galleryLoading, setGalleryLoading] = useState(false);
  const [galleryCategory, setGalleryCategory] = useState('all');

  // 切换 section 时清空搜索
  const handleSectionChange = useCallback((section: HomeSection) => {
    setActiveSection(section);
    setSearchQuery('');
  }, []);

  // 初始化：加载模型信息 + 订阅列表
  useEffect(() => {
    fetchModels();
    if (isAuthenticated) {
      fetchSubscriptions();
    }
  }, [isAuthenticated, fetchModels, fetchSubscriptions]);

  // 懒加载提示词数据（切到 prompts section 时才加载）
  useEffect(() => {
    if (activeSection !== 'prompts' || galleryData) return;
    let ignore = false;
    setGalleryLoading(true);
    fetch('/data/prompt_gallery.json')
      .then((res) => {
        if (!res.ok) throw new Error('加载失败');
        return res.json();
      })
      .then((json: PromptGalleryData) => {
        if (ignore) return;
        setGalleryData(json);
        setGalleryLoading(false);
      })
      .catch(() => {
        if (ignore) return;
        setGalleryLoading(false);
      });
    return () => { ignore = true; };
  }, [activeSection, galleryData]);

  // === 模型广场逻辑 ===

  const filteredModels = useMemo(() => {
    let result = DISPLAY_MODELS;
    if (activeTab !== 'all') {
      result = result.filter((m) => m.type === activeTab);
    }
    if (searchQuery.trim() && activeSection === 'models') {
      const q = searchQuery.toLowerCase();
      result = result.filter(
        (m) => m.name.toLowerCase().includes(q) || m.description.toLowerCase().includes(q),
      );
    }
    return result;
  }, [activeTab, searchQuery, activeSection]);

  const counts = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    const base = (q && activeSection === 'models')
      ? DISPLAY_MODELS.filter((m) =>
          m.name.toLowerCase().includes(q) || m.description.toLowerCase().includes(q),
        )
      : DISPLAY_MODELS;
    return {
      all: base.length,
      chat: base.filter((m) => m.type === 'chat').length,
      image: base.filter((m) => m.type === 'image').length,
      video: base.filter((m) => m.type === 'video').length,
    };
  }, [searchQuery, activeSection]);

  const handleCardClick = useCallback((model: UnifiedModel) => {
    setSelectedModel(model);
    setDrawerOpen(true);
  }, []);

  const handleSubscribe = useCallback(
    async (modelId: string) => {
      if (!isAuthenticated) {
        openRegister();
        return;
      }
      try {
        await subscribe(modelId);
        toast.success('订阅成功');
      } catch {
        toast.error('订阅失败，请重试');
      }
    },
    [isAuthenticated, subscribe, openRegister],
  );

  const handleOpenUnsub = useCallback(() => {
    if (selectedModel) {
      setUnsubModal({ open: true, model: selectedModel });
    }
  }, [selectedModel]);

  const handleConfirmUnsub = useCallback(async () => {
    if (!unsubModal.model) return;
    setUnsubLoading(true);
    try {
      await unsubscribe(unsubModal.model.id);
      toast.success('已取消订阅');
      setUnsubModal({ open: false, model: null });
    } catch {
      toast.error('取消订阅失败，请重试');
    } finally {
      setUnsubLoading(false);
    }
  }, [unsubModal.model, unsubscribe]);

  const handleOpenAuth = useCallback(
    (mode: 'login' | 'register') => {
      if (mode === 'login') openLogin();
      else openRegister();
    },
    [openLogin, openRegister],
  );

  // === 提示词画廊逻辑 ===

  const filteredPrompts = useMemo(() => {
    if (!galleryData) return [];
    let result = galleryData.prompts;
    if (galleryCategory !== 'all') {
      result = result.filter((p) => p.category === galleryCategory);
    }
    if (searchQuery.trim() && activeSection === 'prompts') {
      const q = searchQuery.toLowerCase();
      result = result.filter(
        (p) =>
          p.title.toLowerCase().includes(q) ||
          p.description.toLowerCase().includes(q) ||
          p.tags.some((t) => t.toLowerCase().includes(q)),
      );
    }
    return result;
  }, [galleryData, galleryCategory, searchQuery, activeSection]);

  return (
    <PageTransition className="min-h-screen bg-surface flex flex-col">
      <NavBar
        searchQuery={searchQuery}
        onSearchChange={setSearchQuery}
        activeSection={activeSection}
        onSectionChange={handleSectionChange}
      />

      <HeroSection
        totalModels={DISPLAY_MODELS.length}
        isAuthenticated={isAuthenticated}
        onStartChat={openLogin}
      />

      {/* === 模型广场 === */}
      {activeSection === 'models' && (
        <>
          <CategoryTabs activeTab={activeTab} onTabChange={setActiveTab} counts={counts} />
          <div className="flex-1">
            <ModelGrid
              models={filteredModels}
              activeTab={activeTab}
              searchQuery={searchQuery}
              isLoading={isLoading}
              isAuthenticated={isAuthenticated}
              isSubscribed={isSubscribed}
              isSubscribing={isSubscribing}
              onCardClick={handleCardClick}
              onSubscribe={handleSubscribe}
            />
          </div>
        </>
      )}

      {/* === AI 提示词 === */}
      {activeSection === 'prompts' && (
        <>
          {galleryLoading ? (
            <div className="flex-1 flex items-center justify-center py-20">
              <div className="text-text-tertiary">加载提示词库...</div>
            </div>
          ) : galleryData ? (
            <>
              <GalleryTabs
                categories={galleryData.categories}
                activeCategory={galleryCategory}
                onCategoryChange={setGalleryCategory}
                totalCount={galleryData.prompts.length}
              />
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
              {/* 来源说明 */}
              <div className="border-t border-border-default py-4">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 text-center">
                  <p className="text-xs text-text-disabled">
                    提示词来源：
                    <a
                      href={galleryData.source_repo}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="hover:text-accent transition-colors ml-1"
                    >
                      {galleryData.source_repo.replace('https://github.com/', '')}
                    </a>
                    {' '}(MIT License)
                  </p>
                </div>
              </div>
            </>
          ) : (
            <div className="flex-1 flex items-center justify-center py-20">
              <div className="text-error">加载提示词库失败</div>
            </div>
          )}
        </>
      )}

      <Footer />

      {/* 详情抽屉 */}
      <ModelDetailDrawer
        model={selectedModel}
        isOpen={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        isAuthenticated={isAuthenticated}
        isSubscribed={selectedModel ? isSubscribed(selectedModel.id) : false}
        isSubscribing={selectedModel ? isSubscribing(selectedModel.id) : false}
        onSubscribe={handleSubscribe}
        onUnsubscribe={handleOpenUnsub}
        onOpenAuth={handleOpenAuth}
      />

      {/* 取消订阅确认弹窗 */}
      <UnsubscribeModal
        isOpen={unsubModal.open}
        modelName={unsubModal.model?.name ?? ''}
        isLoading={unsubLoading}
        onConfirm={handleConfirmUnsub}
        onCancel={() => setUnsubModal({ open: false, model: null })}
      />
    </PageTransition>
  );
}
