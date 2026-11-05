import { useEffect, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { providersApi } from '../../api/providers';
import { ApiError, type Provider } from '../../api/types';
import { Drawer } from '../ui/Drawer';
import { Button } from '../ui/Button';
import { toast } from '../ui/Toast';

/**
 * ProviderDrawer（FR-C1/C2）：模型目录编辑 / 密钥 eye 显隐 / 测试分支（200 成功卡 / 4xx 红卡注明「密钥未外发」）/
 * 空模型 ID 校验「模型 N: 模型 ID 不能为空」。
 */
export function ProviderDrawer({
  open,
  provider,
  onClose,
}: {
  open: boolean;
  provider?: Provider | null;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [name, setName] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [showKey, setShowKey] = useState(false);
  const [models, setModels] = useState<string[]>([]);
  const [testState, setTestState] = useState<'idle' | 'testing' | 'ok' | 'fail'>('idle');
  const [testMsg, setTestMsg] = useState('');
  const [pulling, setPulling] = useState(false);
  const [err, setErr] = useState('');

  useEffect(() => {
    if (open) {
      setName(provider?.name ?? '');
      setBaseUrl(provider?.base_url ?? '');
      setApiKey('');
      setModels(provider?.models ?? []);
      setTestState('idle');
      setErr('');
    }
  }, [open, provider]);

  const saveMut = useMutation({
    mutationFn: () =>
      provider
        ? providersApi.patch(provider.id, { name, base_url: baseUrl, models, ...(apiKey ? { api_key: apiKey } : {}) })
        : providersApi.create({ name, base_url: baseUrl, models, api_key: apiKey }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['providers'] });
      onClose();
      toast('提供商已保存', 'ok');
    },
    onError: (e) => toast(`保存失败：${(e as Error).message}`, 'err'),
  });

  const validateAndSave = () => {
    // 空模型 ID 校验（FR-C2）：「模型 N: 模型 ID 不能为空」
    for (let i = 0; i < models.length; i++) {
      if (!models[i].trim()) {
        setErr(`模型 ${i + 1}: 模型 ID 不能为空`);
        return;
      }
    }
    if (!name.trim()) return setErr('名称不能为空');
    if (!baseUrl.trim()) return setErr('Base URL 不能为空');
    setErr('');
    saveMut.mutate();
  };

  const test = async () => {
    if (!provider) {
      setTestMsg('请先保存后再测试');
      setTestState('fail');
      return;
    }
    setTestState('testing');
    try {
      const r = await providersApi.test(provider.id);
      setTestState('ok');
      setTestMsg(`连接成功 · ${r.latency_ms}ms · ${r.models.length} 个模型`);
      setModels(r.models);
    } catch (e) {
      setTestState('fail');
      setTestMsg(e instanceof ApiError ? `${e.code} ${e.message}（密钥未外发）` : `${(e as Error).message}（密钥未外发）`);
    }
  };

  const pullModels = async () => {
    if (!baseUrl.trim()) {
      toast('请先填写 Base URL', 'err');
      return;
    }
    setPulling(true);
    try {
      let list: string[];
      if (provider && !apiKey.trim()) {
        // 已保存且未改密钥：走已存密文通道
        list = await providersApi.models(provider.id);
      } else {
        // 草稿态/新填密钥：免保存探测（密钥不落库）
        list = await providersApi.probeModels({
          base_url: baseUrl.trim(),
          ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
        });
      }
      setModels(Array.isArray(list) ? list : []);
      toast(`已拉取 ${list.length} 个模型`, 'ok');
    } catch (e) {
      toast(`拉取失败：${(e as Error).message}`, 'err');
    } finally {
      setPulling(false);
    }
  };

  return (
    <Drawer
      open={open}
      title={provider ? `提供商 · ${provider.name}` : '新增提供商'}
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>取消</Button>
          <Button variant="accent" data-action="保存提供商" onClick={validateAndSave} disabled={saveMut.isPending}>
            保存
          </Button>
        </>
      }
    >
      <label className="form-label">名称</label>
      <input className="form-input" value={name} onChange={(e) => setName(e.target.value)} />
      <label className="form-label" style={{ marginTop: 10 }}>Base URL（openai_chat 协议）</label>
      <input className="form-input" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="http://127.0.0.1:11434/v1" />
      <label className="form-label" style={{ marginTop: 10 }}>API Key（仅存密文，永不回显）</label>
      <div style={{ display: 'flex', gap: 6 }}>
        <input
          className="form-input"
          type={showKey ? 'text' : 'password'}
          value={apiKey}
          placeholder={provider ? '留空 = 不修改' : ''}
          onChange={(e) => setApiKey(e.target.value)}
        />
        <Button size="sm" variant="ghost" data-action="密钥显隐切换" aria-label="密钥显隐切换" onClick={() => setShowKey(!showKey)}>
          {showKey ? '🙈' : '👁'}
        </Button>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', marginTop: 14, marginBottom: 6 }}>
        <span className="form-label" style={{ margin: 0, flex: 1 }}>模型目录</span>
        <Button size="sm" variant="ghost" data-action="拉取模型目录" onClick={pullModels} disabled={pulling || !baseUrl.trim()}>
          {pulling ? '拉取中…' : '拉取目录'}
        </Button>
        <Button size="sm" variant="ghost" data-action="新增模型行" onClick={() => setModels((m) => [...m, ''])}>
          ＋ 模型
        </Button>
      </div>
      {models.map((m, i) => (
        <div key={i} style={{ display: 'flex', gap: 6, marginBottom: 6 }}>
          <input
            className={`form-input${!m.trim() && err ? ' invalid' : ''}`}
            value={m}
            placeholder={`模型 ${i + 1} ID`}
            onChange={(e) => setModels((arr) => arr.map((x, j) => (j === i ? e.target.value : x)))}
          />
          <Button size="sm" variant="ghost" data-action="删除模型行" onClick={() => setModels((arr) => arr.filter((_, j) => j !== i))}>
            🗑
          </Button>
        </div>
      ))}
      {err && <div className="form-err">{err}</div>}

      <div style={{ marginTop: 14, display: 'flex', gap: 8, alignItems: 'center' }}>
        <Button data-action="测试提供商" onClick={test} disabled={testState === 'testing' || !provider}>
          {testState === 'testing' ? '测试中…' : '测试连接'}
        </Button>
        {testState === 'ok' && <span style={{ color: 'var(--green)', fontSize: 12.5 }}>{testMsg}</span>}
        {testState === 'fail' && (
          <span style={{ color: 'var(--red)', fontSize: 12.5 }} data-action="测试提供商失败">
            {testMsg}
          </span>
        )}
      </div>
    </Drawer>
  );
}
