import { useEffect, useRef, useState } from 'react';
import { api, setToken } from '../api/client';

/**
 * LoginPage —— 本机令牌门禁（Operate 模式：完成一次凭证粘贴即可进入）。
 * 令牌 = 服务端 setting `server.token`（首启 CLI 打印一次）；
 * 设置 → 查询与安全 可关闭 server.auth 实现免令牌（单机自用场景）。
 */
export function LoginPage({ onDone }: { onDone: () => void }) {
  const [value, setValue] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  async function submit() {
    const token = value.trim();
    if (!token) {
      setError('请输入访问令牌');
      return;
    }
    setBusy(true);
    setError('');
    setToken(token);
    try {
      await api.get('/settings', { timeoutMs: 8000 });
      onDone();
    } catch {
      setToken('');
      setError('令牌无效，请核对后重试');
      setBusy(false);
    }
  }

  async function paste() {
    try {
      const text = await navigator.clipboard.readText();
      if (text) {
        setValue(text.trim());
        setError('');
      }
    } catch {
      inputRef.current?.focus();
    }
  }

  return (
    <div className="login-wrap">
      <div className="login-panel">
        <div className="login-title">
          AIto<span className="login-title-accent">sql</span>
        </div>
        <div className="login-sub">本地 Text2SQL 工作台</div>
        <label className="login-label" htmlFor="login-token">
          访问令牌
        </label>
        <div className="login-field">
          <input
            id="login-token"
            ref={inputRef}
            className={`form-input mono${error ? ' invalid' : ''}`}
            type="password"
            placeholder="粘贴服务端首启打印的 token"
            autoComplete="off"
            value={value}
            onChange={(e) => {
              setValue(e.target.value);
              setError('');
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !busy) void submit();
            }}
          />
          <button type="button" className="btn btn-sm" onClick={() => void paste()}>
            粘贴
          </button>
        </div>
        {error && <div className="login-error">{error}</div>}
        <button
          type="button"
          className="btn btn-accent login-submit"
          disabled={busy}
          onClick={() => void submit()}
        >
          {busy ? '验证中…' : '进入工作台'}
        </button>
        <div className="login-hint">
          令牌在服务端首次启动时打印过一次；遗失可在服务端执行
          <code> SELECT value_json FROM setting WHERE key=&apos;server.token&apos; </code>
          取回。进入后可在「设置 → 查询与安全」关闭 API 访问认证，本机免令牌使用。
        </div>
      </div>
    </div>
  );
}
