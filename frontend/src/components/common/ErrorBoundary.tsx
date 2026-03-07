/**
 * 错误边界组件
 *
 * 捕获 React 渲染错误，防止白屏，并显示错误详情
 */

import { Component, type ReactNode, type ErrorInfo } from 'react';

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
    console.error('[ErrorBoundary] React rendering crash:', error);
    console.error('[ErrorBoundary] Component stack:', errorInfo.componentStack);
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
        <div style={{
          position: 'fixed',
          inset: 0,
          zIndex: 99999,
          backgroundColor: '#fff',
          padding: '24px',
          overflow: 'auto',
          fontFamily: 'monospace',
        }}>
          <h1 style={{ color: '#dc2626', fontSize: '20px', marginBottom: '16px' }}>
            页面渲染崩溃
          </h1>

          <div style={{
            backgroundColor: '#fef2f2',
            border: '1px solid #fca5a5',
            borderRadius: '8px',
            padding: '16px',
            marginBottom: '16px',
          }}>
            <p style={{ fontWeight: 'bold', marginBottom: '8px' }}>
              Error: {error?.message}
            </p>
            <pre style={{ fontSize: '12px', whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
              {error?.stack}
            </pre>
          </div>

          {errorInfo?.componentStack && (
            <div style={{
              backgroundColor: '#fffbeb',
              border: '1px solid #fcd34d',
              borderRadius: '8px',
              padding: '16px',
              marginBottom: '16px',
            }}>
              <p style={{ fontWeight: 'bold', marginBottom: '8px' }}>Component Stack:</p>
              <pre style={{ fontSize: '12px', whiteSpace: 'pre-wrap' }}>
                {errorInfo.componentStack}
              </pre>
            </div>
          )}

          <div style={{ display: 'flex', gap: '12px' }}>
            <button
              onClick={this.handleReload}
              style={{
                padding: '8px 16px',
                backgroundColor: '#3b82f6',
                color: '#fff',
                border: 'none',
                borderRadius: '6px',
                cursor: 'pointer',
                fontSize: '14px',
              }}
            >
              刷新页面
            </button>
            <button
              onClick={this.handleDismiss}
              style={{
                padding: '8px 16px',
                backgroundColor: '#6b7280',
                color: '#fff',
                border: 'none',
                borderRadius: '6px',
                cursor: 'pointer',
                fontSize: '14px',
              }}
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
