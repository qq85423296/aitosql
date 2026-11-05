import { useCallback, useEffect } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { sessionsApi } from '../api/sessions';
import { queriesApi } from '../api/queries';
import { datasourcesApi } from '../api/datasources';
import { settingsApi } from '../api/settings';
import { providersApi } from '../api/providers';
import { newQueryId } from '../lib/ulid';
import { fmtRouteRef } from '../lib/format';
import { useSessionStore } from '../stores/sessionStore';
import { useQueryStore } from '../stores/queryStore';
import { useAssembly } from '../hooks/useAssembly';
import { SessionDrawer } from '../components/query/SessionDrawer';
import { DsSwitcher } from '../components/query/DsSwitcher';
import { Composer } from '../components/query/Composer';
import { Stream } from '../components/query/Stream';
import { Banners } from '../components/query/Banners';
import { ContextPanel } from '../components/query/ContextPanel';
import { toast } from '../components/ui/Toast';

/**
 * QueryPage（FR-Q1~Q14）：会话抽屉 + 顶栏（数据源切换/面板开关）+ 消息流 + Composer + 上下文面板。
 * 装配五步由 useAssembly 承载（D-21）。
 */
export function QueryPage() {
  const { sessionId } = useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();

  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  const setCurrentSession = useSessionStore((s) => s.setCurrentSession);
  const toggleDrawer = useSessionStore((s) => s.toggleDrawer);
  const currentDsId = useSessionStore((s) => s.currentDsId);
  const setCurrentDs = useSessionStore((s) => s.setCurrentDs);
  const toggleContextPanel = useSessionStore((s) => s.toggleContextPanel);

  const messages = useQueryStore((s) => s.messages);
  const upsertQuestion = useQueryStore((s) => s.upsertQuestion);

  const { data: datasources = [] } = useQuery({ queryKey: ['datasources'], queryFn: datasourcesApi.list });
  const { data: settingsBundle } = useQuery({ queryKey: ['settings'], queryFn: settingsApi.get });
  const { data: providers = [] } = useQuery({ queryKey: ['providers'], queryFn: providersApi.list });
  const { data: sessions = [] } = useQuery({ queryKey: ['sessions'], queryFn: sessionsApi.list });
  const settings = settingsBundle?.settings;
  const followupsEnabled = (settings?.['feature.followups'] as boolean) ?? true;

  // 路由参数 ↔ store 同步
  useEffect(() => {
    if (sessionId && sessionId !== currentSessionId) setCurrentSession(sessionId);
  }, [sessionId, currentSessionId, setCurrentSession]);

  // 默认数据源
  useEffect(() => {
    if (currentDsId == null && datasources.length) setCurrentDs(datasources[0].id);
  }, [datasources, currentDsId, setCurrentDs]);

  const { beginQuery, stopQuery } = useAssembly(currentSessionId);

  const currentDs = datasources.find((d) => d.id === currentDsId);
  const offline = currentDs?.mode === 'offline';
  // 活动轮 = running 且非排队壳（契约 1：queued 壳不算活动轮，不参与 steer/停止目标选择）
  const runningQuery = [...messages].reverse().find((m) => m.status === 'running' && !m.queued);

  const newSession = useMutation({
    mutationFn: () => sessionsApi.create({ datasource_id: currentDsId }),
    onSuccess: (s) => {
      void qc.invalidateQueries({ queryKey: ['sessions'] });
      navigate(`/sessions/${s.id}`);
    },
  });

  const send = useCallback(
    async (question: string) => {
      let sid = currentSessionId;
      if (!sid) {
        const s = await sessionsApi.create({ datasource_id: currentDsId });
        sid = s.id;
        void qc.invalidateQueries({ queryKey: ['sessions'] });
        navigate(`/sessions/${sid}`);
      }
      if (currentDsId == null) {
        toast('请先选择数据源', 'err');
        return;
      }
      const qid = newQueryId(); // client_request_id 前端生成（D-22）
      // 登记消息壳（乐观渲染用户气泡）
      upsertQuestion({
        query_id: qid,
        session_id: sid,
        question,
        datasource_id: currentDsId,
        dialect: currentDs?.dialect,
        ds_mode: currentDs?.mode,
        status: 'running',
        stages: [],
        assumptions: [],
        risks: [],
        corrections: [],
        followups: [],
        unverified: false,
        created_at: new Date().toISOString(),
      });
      try {
        // 幂等：同 client_request_id 重发返回同一 query_id，不重复执行（C2）
        const res = await queriesApi.create({ session_id: sid, datasource_id: currentDsId, question, client_request_id: qid });
        if (res.queued || res.query_id == null) {
          // 契约 1：会话忙 → 排队回执。消息壳转排队态（渲染「已排队」小条），
          // 不挂流；当前轮终态后后端自动开轮，followup_started 事件解除 queued 并由 useAssembly 挂流
          useQueryStore.getState().markQueued(qid, true);
        } else {
          beginQuery(res.query_id ?? qid, { offline });
        }
      } catch (e) {
        useQueryStore.getState().applyEvent(
          qid,
          { type: 'error', code: 'E2001', message: (e as Error).message, retryable: true },
          null,
        );
      }
    },
    [currentSessionId, currentDsId, currentDs, offline, navigate, qc, upsertQuestion, beginQuery],
  );

  /** 契约 2：引导当前轮（POST /queries/:id/steer，kind=steer）；仅活动查询受理 */
  const steer = useCallback(
    async (content: string) => {
      if (!runningQuery) return;
      try {
        await queriesApi.steer(runningQuery.query_id, { content, kind: 'steer' });
      } catch (e) {
        toast(`引导发送失败：${(e as Error).message}`, 'err');
      }
    },
    [runningQuery],
  );

  const stop = useCallback(
    async (qid: string) => {
      stopQuery(qid);
      try {
        await queriesApi.stop(qid);
        // P1-2 修复：SSE 已先行关闭，后端 stopped 事件不可达——本地直接落终态解锁 Composer，
        // 并 invalidate + 延迟 E5 resync 兜底对齐真实状态（stop 对已终态幂等）
        useQueryStore.getState().applyEvent(qid, { type: 'stopped', reason: 'user_stop' }, null);
        void qc.invalidateQueries({ queryKey: ['sessions'] });
        void qc.invalidateQueries({ queryKey: ['queries'] });
        window.setTimeout(() => useQueryStore.getState().setResyncing(true), 1000);
        // CH readonly 无 KILL 通道（C1.5）：后端忽略时前端 UI 注明
      } catch {
        toast('停止请求已发送（ClickHouse readonly 连接无 KILL 通道，待超时终止）');
      }
    },
    [stopQuery, qc],
  );

  return (
    <>
      <SessionDrawer
        currentId={currentSessionId}
        onSelect={(id) => navigate(`/sessions/${id}`)}
        onNew={() => newSession.mutate()}
      />
      <div className="main-col">
        <div className="topbar">
          <button className="icon-btn" aria-label="切换会话抽屉" data-action="切换会话抽屉" onClick={toggleDrawer}>
            ☰
          </button>
          <DsSwitcher value={currentDsId} onChange={setCurrentDs} />
          <span style={{ flex: 1 }} />
          <span className="proto-note">路由：{fmtRouteRef(settings?.['route.generate'], providers)}</span>
          <button
            className="icon-btn"
            aria-label="切换上下文面板"
            data-action="切换上下文面板"
            onClick={toggleContextPanel}
          >
            ◫
          </button>
        </div>
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, overflow: 'hidden' }}>
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
            <Banners compactSummary={sessions.find((s) => s.id === currentSessionId)?.summary ?? null} />
            <Stream messages={messages} followupsEnabled={followupsEnabled} onSend={send} onStopQuery={stop} />
          </div>
          <Composer
            offline={offline}
            running={!!runningQuery}
            onSend={send}
            onSteer={runningQuery ? steer : undefined}
            onStop={runningQuery ? () => stop(runningQuery.query_id) : undefined}
          />
        </div>
      </div>
      <ContextPanel datasourceId={currentDsId} />
    </>
  );
}
