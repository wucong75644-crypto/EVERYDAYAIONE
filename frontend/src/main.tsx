import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { LazyMotion, domAnimation } from 'framer-motion'
import './index.css'
import App from './App.tsx'

/**
 * LazyMotion + domAnimation：
 * 只打包我们实际用的 framer-motion feature（dom + animation），
 * 相比全量 motion.div 减少约 50% bundle 体积。
 * 所有 motion.* 组件在这个上下文里都会自动拿到 animation feature。
 */
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <LazyMotion features={domAnimation} strict>
      <App />
    </LazyMotion>
  </StrictMode>,
)
