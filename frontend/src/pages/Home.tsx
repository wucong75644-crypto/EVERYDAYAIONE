/**
 * 首页（模型广场）
 *
 * 展示所有可用模型，支持分类浏览、搜索、订阅管理和详情查看。
 */

import { useState, useMemo, useEffect, useCallback } from 'react';
import toast from 'react-hot-toast';
import { ALL_MODELS, type UnifiedModel } from '../constants/models';
import { useAuthStore } from '../stores/useAuthStore';
import { useAuthModalStore } from '../stores/useAuthModalStore';
import { useSubscriptionStore } from '../stores/useSubscriptionStore';
import Footer from '../components/Footer';
import NavBar from '../components/home/NavBar';
import HeroSection from '../components/home/HeroSection';
import CategoryTabs, { type TabValue } from '../components/home/CategoryTabs';
import ModelGrid from '../components/home/ModelGrid';
import ModelDetailDrawer from '../components/home/ModelDetailDrawer';
import UnsubscribeModal from '../components/home/UnsubscribeModal';

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

  const [searchQuery, setSearchQuery] = useState('');
  const [activeTab, setActiveTab] = useState<TabValue>('all');
  const [selectedModel, setSelectedModel] = useState<UnifiedModel | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [unsubModal, setUnsubModal] = useState<{ open: boolean; model: UnifiedModel | null }>({
    open: false,
    model: null,
  });
  const [unsubLoading, setUnsubLoading] = useState(false);

  // 初始化：加载模型信息 + 订阅列表
  useEffect(() => {
    fetchModels();
    if (isAuthenticated) {
      fetchSubscriptions();
    }
  }, [isAuthenticated, fetchModels, fetchSubscriptions]);

  // 搜索 + Tab 过滤
  const filteredModels = useMemo(() => {
    let result = DISPLAY_MODELS;

    // Tab 过滤
    if (activeTab !== 'all') {
      result = result.filter((m) => m.type === activeTab);
    }

    // 搜索过滤
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      result = result.filter(
        (m) => m.name.toLowerCase().includes(q) || m.description.toLowerCase().includes(q),
      );
    }

    return result;
  }, [activeTab, searchQuery]);

  // Tab 计数
  const counts = useMemo(() => {
    const base = searchQuery.trim()
      ? DISPLAY_MODELS.filter((m) => {
          const q = searchQuery.toLowerCase();
          return m.name.toLowerCase().includes(q) || m.description.toLowerCase().includes(q);
        })
      : DISPLAY_MODELS;
    return {
      all: base.length,
      chat: base.filter((m) => m.type === 'chat').length,
      image: base.filter((m) => m.type === 'image').length,
      video: base.filter((m) => m.type === 'video').length,
    };
  }, [searchQuery]);

  // 打开详情
  const handleCardClick = useCallback((model: UnifiedModel) => {
    setSelectedModel(model);
    setDrawerOpen(true);
  }, []);

  // 订阅
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

  // 打开取消订阅弹窗
  const handleOpenUnsub = useCallback(() => {
    if (selectedModel) {
      setUnsubModal({ open: true, model: selectedModel });
    }
  }, [selectedModel]);

  // 确认取消订阅
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

  // AuthModal 打开（从 Drawer）
  const handleOpenAuth = useCallback(
    (mode: 'login' | 'register') => {
      if (mode === 'login') openLogin();
      else openRegister();
    },
    [openLogin, openRegister],
  );

  return (
    <div className="min-h-screen bg-gray-50 flex flex-col">
      <NavBar searchQuery={searchQuery} onSearchChange={setSearchQuery} />

      <HeroSection
        totalModels={DISPLAY_MODELS.length}
        isAuthenticated={isAuthenticated}
        onStartChat={openLogin}
      />

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
    </div>
  );
}
