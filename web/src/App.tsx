import { lazy, Suspense, useEffect, useState, type ReactNode } from 'react';
import { Route, Routes } from 'react-router-dom';
import { Rail } from './components/rail/Rail';
import { ToastHost } from './components/ui/Toast';
import { api, UNAUTHORIZED_EVENT } from './api/client';
import { QueryPage } from './pages/QueryPage';
import { LoginPage } from './pages/LoginPage';

// 非首屏页面按路由懒加载（F 阶段分包）：QueryPage（首屏）与 LoginPage（门禁）保持
// 同步加载；CodeMirror 仅随 QueryPage 的 SqlBlock 进入主 chunk，不旁落到其他页面包
const DatasourcesPage = lazy(() =>
  import('./pages/DatasourcesPage').then((m) => ({ default: m.DatasourcesPage })),
);
const SamplesPage = lazy(() =>
  import('./pages/SamplesPage').then((m) => ({ default: m.SamplesPage })),
);
const SettingsPage = lazy(() =>
  import('./pages/SettingsPage').then((m) => ({ default: m.SettingsPage })),
);

type Gate = 'loading' | 'authed' | 'login';

/** 四路由 SPA（react-router v7，替代原型物理多页）；Rail 全局。
 *  门禁：启动探测 /settings——200 直接进（含 server.auth=off 免令牌）；
 *  401 或失败 → LoginPage；运行期任何 401 经事件广播踢回登录页。 */
export default function App() {
  const [gate, setGate] = useState<Gate>('loading');

  useEffect(() => {
    let alive = true;
    api
      .get('/settings', { timeoutMs: 5000 })
      .then(() => alive && setGate('authed'))
      .catch(() => alive && setGate('login'));
    const kick = () => setGate('login');
    window.addEventListener(UNAUTHORIZED_EVENT, kick);
    return () => {
      alive = false;
      window.removeEventListener(UNAUTHORIZED_EVENT, kick);
    };
  }, []);

  let body: ReactNode;
  if (gate === 'loading') {
    body = <div className="login-wrap" />;
  } else if (gate === 'login') {
    body = <LoginPage onDone={() => setGate('authed')} />;
  } else {
    body = (
      <div className="shell">
        <Rail />
        <div className="main">
          <Suspense fallback={<div className="p-6 text-sm text-neutral-500">页面加载中…</div>}>
            <Routes>
              <Route path="/" element={<QueryPage />} />
              <Route path="/sessions/:sessionId" element={<QueryPage />} />
              <Route path="/datasources" element={<DatasourcesPage />} />
              <Route path="/samples" element={<SamplesPage />} />
              <Route path="/settings" element={<SettingsPage />} />
            </Routes>
          </Suspense>
        </div>
        <ToastHost />
      </div>
    );
  }
  return body;
}
