import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import App from './App';
import './theme/tokens.css';
import './styles/app.css';

/** V4：REST 数据归 TanStack Query；zustand 只存 SSE 装配态 + UI 态 */
const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 10_000 },
  },
});

// 主题引导（localStorage 记忆，深色默认）
document.documentElement.setAttribute(
  'data-theme',
  localStorage.getItem('aitosql.theme') === 'light' ? 'light' : 'dark',
);

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
