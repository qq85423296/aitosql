import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { settingsApi } from '../api/settings';
import { providersApi } from '../api/providers';
import type { SettingsMap } from '../api/types';
import { SettingsNav, SETTING_GROUPS } from '../components/settings/SettingsNav';
import { ProviderList } from '../components/settings/ProviderList';
import { RouteSection } from '../components/settings/RouteSection';
import { AiCompletionSection } from '../components/settings/AiCompletionSection';
import { PromptSection } from '../components/settings/PromptSection';
import { MemorySection } from '../components/settings/MemorySection';
import { GeneralSection } from '../components/settings/GeneralSection';
import { SafetySection } from '../components/settings/SafetySection';
import { SchemaSection } from '../components/settings/SchemaSection';
import { SampleCacheSection } from '../components/settings/SampleCacheSection';
import { BackupSection } from '../components/settings/BackupSection';
import { AboutSection } from '../components/settings/AboutSection';
import { Button } from '../components/ui/Button';
import { toast } from '../components/ui/Toast';
import { parseRouteRef } from '../lib/format';

/**
 * SettingsPage（FR-C1~C8）：左分组导航 180px + 右滚动 10 分组；
 * 内容区滚动联动导航高亮；保存 = PUT /settings；出境联动（F11/D07）。
 */
export function SettingsPage() {
  const qc = useQueryClient();
  const { data: settingsBundle } = useQuery({ queryKey: ['settings'], queryFn: settingsApi.get });
  const { data: providers = [] } = useQuery({ queryKey: ['providers'], queryFn: providersApi.list });
  const [draft, setDraft] = useState<SettingsMap | null>(null);
  const [activeGroup, setActiveGroup] = useState<string>(SETTING_GROUPS[0].id);
  const bodyRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (settingsBundle && draft == null) setDraft(settingsBundle.settings);
  }, [settingsBundle, draft]);

  // 滚动联动高亮
  useEffect(() => {
    const el = bodyRef.current;
    if (!el) return;
    const onScroll = () => {
      for (const g of [...SETTING_GROUPS].reverse()) {
        const sec = document.getElementById(g.id);
        if (sec && sec.getBoundingClientRect().top <= 120) {
          setActiveGroup(g.id);
          break;
        }
      }
    };
    el.addEventListener('scroll', onScroll, { passive: true });
    return () => el.removeEventListener('scroll', onScroll);
  }, []);

  const saveMut = useMutation({
    mutationFn: (body: Partial<SettingsMap>) => settingsApi.put(body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['settings'] });
      toast('设置已保存', 'ok');
    },
    onError: (e) => toast(`保存失败：${(e as Error).message}`, 'err'),
  });

  if (!draft) return <div className="main-col"><div className="empty-state">设置加载中…</div></div>;

  const patch = (p: Partial<SettingsMap>) => setDraft({ ...draft, ...p });

  // F11/D07 联动判定：任一路由引用非 local 提供商 → 含云（route 值 = {provider_id}:{model}，P0-3）
  const cloudRoute = (['route.generate', 'route.fix', 'route.ai'] as const).some((k) => {
    const r = parseRouteRef(String(draft[k] ?? ''));
    if (!r) return false;
    const p = providers.find((x) => x.id === r.providerId);
    return p != null && p.type !== 'local';
  });

  return (
    <div className="main-col">
      <div className="topbar">
        <span className="title">设置</span>
        <span style={{ flex: 1 }} />
        <Button variant="accent" size="sm" data-action="保存设置" onClick={() => saveMut.mutate(draft)} disabled={saveMut.isPending}>
          保存设置
        </Button>
      </div>
      <div className="settings-layout" style={{ flex: 1, minHeight: 0 }}>
        <SettingsNav active={activeGroup} />
        <div className="settings-body" ref={bodyRef}>
          <section className="sgroup" id="g-providers">
            <h3>提供商</h3>
            <div className="desc">本地 Ollama / 云端 OpenAI 兼容（openai_chat 协议）；密钥仅存密文，永不回显</div>
            <ProviderList />
          </section>
          <section className="sgroup" id="g-route">
            <h3>路由与出境</h3>
            <div className="desc">三路路由（生成/修正/AI 补全）；含云路由展示出境清单 E1-E8（E6 红标），全本地 = local_only 零出境</div>
            <RouteSection settings={draft} onChange={patch} />
          </section>
          <section className="sgroup" id="g-aicompletion">
            <h3>AI 补全</h3>
            <div className="desc">并发泳道数 / 采样值出境门控（方案 v1.1：默认仅元数据推断，采样值不出境）</div>
            <AiCompletionSection settings={draft} onChange={patch} />
          </section>
          <section className="sgroup" id="g-prompt">
            <h3>提示词</h3>
            <div className="desc">系统模板 / 方言能力卡 / 行为开关 / 组装预览（8 段信封）</div>
            <PromptSection settings={draft} cloudRoute={cloudRoute} onChange={patch} />
          </section>
          <section className="sgroup" id="g-memory">
            <h3>记忆</h3>
            <div className="desc">总账（按 topic 分组，停用即不注入）+ 流水账（只读）+ 水位线合并</div>
            <MemorySection settings={draft} onChange={patch} />
          </section>
          <section className="sgroup" id="g-general">
            <h3>通用</h3>
            <div className="desc">端口 / 会话压缩 / 导出 TTL / 令牌</div>
            <GeneralSection settings={draft} onChange={patch} />
          </section>
          <section className="sgroup" id="g-safety">
            <h3>查询安全</h3>
            <div className="desc">三层防线：R15 静态阻断 → EXPLAIN 干跑 → 超时矩阵</div>
            <SafetySection settings={draft} onChange={patch} />
          </section>
          <section className="sgroup" id="g-schema">
            <h3>Schema 注入</h3>
            <div className="desc">注入模式（滞后带 55-65%）/ 预算占比 / 向量检索 / 枚举上限</div>
            <SchemaSection settings={draft} onChange={patch} />
          </section>
          <section className="sgroup" id="g-sample">
            <h3>样例缓存</h3>
            <div className="desc">few-shot 条数 / 缓存命中（semantic_rev 判等）/ 自动置过期</div>
            <SampleCacheSection settings={draft} onChange={patch} />
          </section>
          <section className="sgroup" id="g-backup">
            <h3>备份</h3>
            <div className="desc">VACUUM INTO 滚动 keep=10；剥离密文；恢复不适用自动续跑</div>
            <BackupSection settings={draft} onChange={patch} />
          </section>
          <section className="sgroup" id="g-about">
            <h3>关于</h3>
            <AboutSection />
          </section>
        </div>
      </div>
    </div>
  );
}
