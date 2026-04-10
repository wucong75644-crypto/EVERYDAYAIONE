/**
 * 错误边界组件
 *
 * 捕获 React 渲染错误，防止白屏，并显示错误详情
 */

import { Component, type ReactNode, type ErrorInfo } from 'react';
import { logger } from '../../utils/logger';

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
  errorInfo: ErrorInfo | null;
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = {
    hasError: false,
    error: null,
    errorInfo: null,
  };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    this.setState({ errorInfo });
    logger.error('errorBoundary', 'React rendering crash', error, { componentStack: errorInfo.componentStack });
  }

  handleReload = (): void => {
    window.location.reload();
  };

  handleDismiss = (): void => {
    this.setState({ hasError: false, error: null, errorInfo: null });
  };

  render(): ReactNode {
    if (this.state.hasError) {
      const { error, errorInfo } = this.state;

      return (
        <div className="fixed inset-0 z-[9999] bg-surface-card text-text-primary p-6 overflow-auto font-mono">
          <h1 className="text-error text-xl font-bold mb-4">页面渲染崩溃</h1>

          <div className="bg-error-light border border-error/30 rounded-lg p-4 mb-4">
            <p className="font-bold mb-2">Error: {error?.message}</p>
            <pre className="text-xs whitespace-pre-wrap break-all text-text-secondary">
              {error?.stack}
            </pre>
          </div>

          {errorInfo?.componentStack && (
            <div className="bg-warning-light border border-warning/30 rounded-lg p-4 mb-4">
              <p className="font-bold mb-2">Component Stack:</p>
              <pre className="text-xs whitespace-pre-wrap text-text-secondary">
                {errorInfo.componentStack}
              </pre>
            </div>
          )}

          <div className="flex gap-3">
            <button
              onClick={this.handleReload}
              className="px-4 py-2 bg-accent text-text-on-accent border-0 rounded-md cursor-pointer text-sm hover:bg-accent-hover transition-base"
            >
              刷新页面
            </button>
            <button
              onClick={this.handleDismiss}
              className="px-4 py-2 bg-text-tertiary text-text-on-accent border-0 rounded-md cursor-pointer text-sm hover:bg-text-secondary transition-base"
            >
              尝试恢复
            </button>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}
