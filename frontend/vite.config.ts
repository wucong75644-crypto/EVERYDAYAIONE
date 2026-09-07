import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const MARKDOWN_PACKAGES = [
  'bail',
  'comma-separated-tokens',
  'decode-named-character-reference',
  'devlop',
  'hast-util-*',
  'html-void-elements',
  'mdast-util-*',
  'micromark-*',
  'parse-entities',
  'property-information',
  'rehype-*',
  'remark-*',
  'react-markdown',
  'space-separated-tokens',
  'stringify-entities',
  'unified',
  'unist-util-*',
  'vfile',
  'web-namespaces',
  'zwitch',
];

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          const normalizedId = id.replaceAll('\\', '/');
          const packageName = normalizedId.match(/\/node_modules\/((?:@[^/]+\/)?[^/]+)\//)?.[1];
          const isMarkdownPackage = packageName && MARKDOWN_PACKAGES.some((name) => (
            name.endsWith('*')
              ? packageName.startsWith(name.slice(0, -1))
              : packageName === name
          ));
          if (
            normalizedId.includes('/node_modules/react/')
            || normalizedId.includes('/node_modules/react-dom/')
            || normalizedId.includes('/node_modules/react-router/')
            || normalizedId.includes('/node_modules/react-router-dom/')
          ) {
            return 'vendor-react';
          }
          if (
            normalizedId.includes('/node_modules/katex/')
            || normalizedId.includes('/node_modules/highlight.js/')
            || isMarkdownPackage
          ) {
            return 'vendor-markdown';
          }
          if (normalizedId.includes('/node_modules/plotly.js-basic-dist-min/')) {
            return 'vendor-plotly';
          }
          if (normalizedId.includes('/node_modules/exceljs/')) {
            return 'vendor-exceljs';
          }
          if (normalizedId.includes('/node_modules/xlsx/')) {
            return 'vendor-xlsx';
          }
          return undefined;
        },
      },
    },
  },
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        ws: true, // 启用 WebSocket 代理
      },
    },
  },
})
