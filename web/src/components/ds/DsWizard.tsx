import { useEffect, useRef, useState } from 'react';
import { clsx } from 'clsx';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { datasourcesApi, type CreateDatasourceReq } from '../../api/datasources';
import { suggestionsApi } from '../../api/suggestions';
import { connectIngestStream, type SseConnection } from '../../api/sse';
import { mockEnabled } from '../../api/client';
import { ApiError, type DdlPreview } from '../../api/types';
import { Button } from '../ui/Button';
import { Icon } from '../ui/icons';
import { toast } from '../ui/Toast';
import { AiCompleteModal } from './AiCompleteModal';
import { SuggestionDrawer } from './SuggestionDrawer';

type Mode = 'online' | 'offline';
type ImpMode = 'ddl' | 'csv' | 'ai';

/**
 * DsWizard（FR-D2/D3/D4/D13，原型「添加数据源」向导）：
 * 在线 3 步（连接信息 → 测试连接 → 摄取 + AI 补全）/ 离线 3 步（基本信息 → 元数据导入 → AI 补全）。
 * E1001 红卡 + 下一步锁定；摄取 SSE 进度 {pct, line}；E1002 标红续摄；DDL 部分失败行标红、成功可入库；
 * 右侧 aside 注册摘要/接下来会发生什么/安全边界 随步骤与接入方式联动。
 */
export function DsWizard({ onDone, onCancel }: { onDone: (id: number) => void; onCancel: () => void }) {
  const qc = useQueryClient();
  const [mode, setMode] = useState<Mode>('online');
  const [step, setStep] = useState(1);
  const [form, setForm] = useState<CreateDatasourceReq>({ name: '', dialect: 'mysql', mode: 'online' });
  const [testState, setTestState] = useState<'idle' | 'testing' | 'ok' | 'fail'>('idle');
  const [testMsg, setTestMsg] = useState('');
  const [testVer, setTestVer] = useState('');
  const [createdId, setCreatedId] = useState<number | null>(null);
  const [ingestPct, setIngestPct] = useState(0);
  const [ingestLines, setIngestLines] = useState<string[]>([]);
  const [ingestFail, setIngestFail] = useState(false);
  const [ingestDone, setIngestDone] = useState(false);
  const ingestRef = useRef<SseConnection | null>(null);
  const [impMode, setImpMode] = useState<ImpMode>('ddl');
  const [ddl, setDdl] = useState('');
  const [preview, setPreview] = useState<DdlPreview | null>(null);
  const [committing, setCommitting] = useState(false);
  const [csvBusy, setCsvBusy] = useState(false);
  const [csvDone, setCsvDone] = useState(false);
  const [csvErrors, setCsvErrors] = useState<{ row: number; table: string; message: string }[]>([]);
  const csvFileRef = useRef<HTMLInputElement>(null);
  const [sgOpen, setSgOpen] = useState(false);
  const [sgRunId, setSgRunId] = useState<number | null>(null);
  const [aiOpen, setAiOpen] = useState(false);

  const createMut = useMutation({
    mutationFn: (body: CreateDatasourceReq) => datasourcesApi.create(body),
    onSuccess: (d) => {
      setCreatedId(d.id);
      void qc.invalidateQueries({ queryKey: ['datasources'] });
    },
  });

  // AI 补全建议计数（创建后拉取）
  const { data: sgData, refetch: refetchSg } = useQuery({
    queryKey: ['suggestions', createdId],
    queryFn: () => suggestionsApi.list(createdId!, 'pending'),
    enabled: createdId != null,
  });
  // 表计数（AiCompleteModal 范围展示用）
  const { data: tableItems } = useQuery({
    queryKey: ['tables', createdId],
    queryFn: () => datasourcesApi.tables(createdId!),
    enabled: createdId != null,
  });

  useEffect(() => () => ingestRef.current?.close(), []);

  const ensureCreated = async (): Promise<number> => {
    if (createdId != null) return createdId;
    const d = await createMut.mutateAsync({ ...form, mode });
    return d.id;
  };

  const testConnection = async () => {
    setTestState('testing');
    try {
      const id = await ensureCreated();
      const r = await datasourcesApi.test(id);
      setTestState('ok');
      setTestVer(r.version);
      setTestMsg(`连接成功 · 账号为${r.readonly ? '只读' : '读写'}权限`);
    } catch (e) {
      // E1001：红卡 + 下一步锁定
      setTestState('fail');
      setTestMsg(e instanceof ApiError ? `${e.code} ${e.message}` : (e as Error).message);
    }
  };

  const startIngest = (id: number) => {
    setIngestPct(0);
    setIngestFail(false);
    setIngestDone(false);
    setIngestLines([]);
    if (mockEnabled()) {
      // mock 摄取进度
      const lines = ['连接成功，开始摄取 schema…', '读取表清单（87 表）', '字段消毒与注释入库', '构建 FTS/bigram 索引', '命名约定推测关系…', '摄取完成'];
      lines.forEach((l, i) =>
        setTimeout(() => {
          setIngestLines((p) => [...p, l]);
          setIngestPct(((i + 1) / lines.length) * 100);
          if (i === lines.length - 1) {
            setIngestDone(true);
            void refetchSg();
          }
        }, 380 * (i + 1)),
      );
      return;
    }
    ingestRef.current = connectIngestStream(id, {
      onProgress: (pct, line) => {
        setIngestPct(pct);
        setIngestLines((p) => [...p.slice(-199), line]);
        if (pct >= 100) {
          setIngestDone(true);
          void refetchSg();
        }
      },
      onDone: (err) => {
        if (err) setIngestFail(true); // E1002：进度条标红，可重试续摄
        else {
          setIngestDone(true);
          void refetchSg();
          setAiOpen(true); // 摄取完成 → 打开 AI 补全（可视化泳道过程）
        }
      },
    });
  };

  const parseDdl = async () => {
    try {
      // P1-9 修复：用 mutateAsync 返回值作为 id（旧闭包 createdId 首建时为 null，会打向 /datasources/0 → 404）
      const id = await ensureCreated();
      const r = await datasourcesApi.importDdlPreview(id, ddl);
      setPreview(r);
    } catch (e) {
      toast(`DDL 解析失败：${(e as Error).message}`, 'err');
    }
  };

  /** 元数据字典模板上传：读文件 → 服务端解析入库（行级错误不阻断）→ 汇总 toast + 错误清单 */
  const uploadCsv = async (file: File) => {
    setCsvBusy(true);
    setCsvErrors([]);
    try {
      // Excel 中文版「另存为 CSV」默认 GBK：先严格 UTF-8 解码，失败回退 GBK
      const buf = await file.arrayBuffer();
      let text: string;
      try {
        text = new TextDecoder('utf-8', { fatal: true }).decode(buf);
      } catch {
        text = new TextDecoder('gbk').decode(buf);
      }
      const id = await ensureCreated();
      const r = await datasourcesApi.importCsvTemplate(id, text);
      setCsvErrors(r.errors ?? []);
      setCsvDone(true);
      if ((r.errors ?? []).length > 0) {
        toast(`部分入库：${r.tables} 表 / ${r.columns} 字段，${r.errors.length} 行失败（见下方清单）`, 'err');
      } else {
        toast(`模板已入库：${r.tables} 表 / ${r.columns} 字段`, 'ok');
      }
      void qc.invalidateQueries({ queryKey: ['datasources'] });
    } catch (e) {
      toast(`模板导入失败：${(e as Error).message}`, 'err');
    } finally {
      setCsvBusy(false);
      if (csvFileRef.current) csvFileRef.current.value = '';
    }
  };

  const finish = async () => {
    // P1-9 同款：await 后 TS 收窄复位，用 const 一次性落定 id
    const id = createdId ?? (await createMut.mutateAsync({ ...form, mode })).id;
    if (mode === 'offline' && ddl.trim()) {
      setCommitting(true);
      try {
        const r = await datasourcesApi.importDdlCommit(id, ddl);
        toast(`DDL 已入库：导入 ${r.imported} 张表`, 'ok');
      } catch (e) {
        toast(`DDL 入库失败：${(e as Error).message}`, 'err');
        setCommitting(false);
        return;
      }
      setCommitting(false);
    }
    void qc.invalidateQueries({ queryKey: ['datasources'] });
    toast('数据源已注册；建议可在详情页继续评审', 'ok');
    onDone(id);
  };

  const stepLabels = mode === 'online' ? ['连接信息', '测试连接', '摄取 + AI 补全'] : ['基本信息', '元数据导入', 'AI 补全'];
  const nextLocked =
    (step === 1 && (mode === 'online' ? !form.name || !form.host : !form.name)) ||
    (step === 2 && mode === 'online' && testState !== 'ok') ||
    (step === 2 && mode === 'offline' && !((impMode === 'ddl' && preview) || (impMode === 'csv' && csvDone)));
  const sgTotal = sgData?.total ?? 0;
  const sgGroups = sgData?.groups;

  const goNext = async () => {
    const next = step + 1;
    setStep(next);
    if (next === 3 && mode === 'online' && createdId != null) startIngest(createdId);
    if (next === 3 && mode === 'offline') {
      const r = await refetchSg();
      // 尚无待评审建议（首次进入）→ 直接打开 AI 补全（可视化过程）；已有建议则先看横幅
      if ((r.data?.total ?? 0) === 0) setAiOpen(true);
    }
  };

  return (
    <div>
      <div className="page-head">
        <span className="page-title">添加数据源</span>
        <div className="head-actions">
          <Button variant="ghost" size="sm" data-action="返回数据源列表" onClick={onCancel}>
            返回列表
          </Button>
        </div>
      </div>
      <div className="page-sub">在线直连自动摄取；离线配置通过 DDL 导入 + AI 补全维护元数据。</div>

      <div className="wizard">
        <div className="wiz-steps">
          {stepLabels.map((label, i) => {
            const n = i + 1;
            const cls = n === step ? 'cur' : n < step ? 'done' : '';
            return (
              <div key={label} className={`wiz-step ${cls}`}>
                <span className="sn">{n < step ? '✓' : n}</span>
                {label}
              </div>
            );
          })}
        </div>

        {/* 第 1 步 */}
        {step === 1 && (
          <div className="wiz-cols">
            <div className="wiz-form">
              <div className="form-row">
                <label className="form-label">接入方式</label>
                <div className="mode-cards">
                  <div className={clsx('mode-card', mode === 'online' && 'sel')} data-action="选择在线接入" onClick={() => { setMode('online'); setForm((f) => ({ ...f, mode: 'online' })); }}>
                    <h5>
                      <Icon name="wifi" size={15} strokeWidth={2} />
                      在线连接
                      <span className="mc-check"></span>
                    </h5>
                    <p>直连数据库自动摄取元数据；建议只读账号，AI 查询只读不写</p>
                  </div>
                  <div className={clsx('mode-card', mode === 'offline' && 'sel')} data-action="选择离线接入" onClick={() => { setMode('offline'); setForm((f) => ({ ...f, mode: 'offline' })); }}>
                    <h5>
                      <Icon name="power" size={15} strokeWidth={2} />
                      离线配置
                      <span className="mc-check"></span>
                    </h5>
                    <p>生产核心库不允许直连时，用 DDL 导入 + AI 补全维护元数据</p>
                  </div>
                </div>
              </div>
              <div className="form-row">
                <label className="form-label">
                  数据源名称 <span className="req">*</span>
                </label>
                <input className="form-input" data-action="填写数据源名称" placeholder="如：生产分析库" value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
              </div>
              <div className="form-row">
                <label className="form-label">方言</label>
                <select className="form-select" data-action="选择方言" value={form.dialect} onChange={(e) => setForm((f) => ({ ...f, dialect: e.target.value as CreateDatasourceReq['dialect'] }))}>
                  <option value="mysql">MySQL</option>
                  <option value="doris">Doris</option>
                  <option value="clickhouse">ClickHouse</option>
                </select>
                <div className="form-hint">方言决定 SQL 生成、函数映射与摄取策略</div>
              </div>
              {mode === 'online' && (
                <>
                  <div className="form-row form-grid">
                    <div>
                      <label className="form-label">
                        主机 <span className="req">*</span>
                      </label>
                      <input className="form-input mono" data-action="填写主机" placeholder="192.168.1.10" value={form.host ?? ''} onChange={(e) => setForm((f) => ({ ...f, host: e.target.value }))} />
                    </div>
                    <div>
                      <label className="form-label">端口</label>
                      <input className="form-input mono" value={form.port ?? ''} onChange={(e) => setForm((f) => ({ ...f, port: e.target.value ? Number(e.target.value) : undefined }))} />
                    </div>
                  </div>
                  <div className="form-row">
                    <label className="form-label">数据库名</label>
                    <input className="form-input mono" placeholder="analytics" value={form.db_name ?? ''} onChange={(e) => setForm((f) => ({ ...f, db_name: e.target.value }))} />
                  </div>
                  <div className="form-row form-grid">
                    <div>
                      <label className="form-label">用户名</label>
                      <input className="form-input mono" placeholder="reader" value={form.username ?? ''} onChange={(e) => setForm((f) => ({ ...f, username: e.target.value }))} />
                    </div>
                    <div>
                      <label className="form-label">密码</label>
                      <input className="form-input mono" type="password" placeholder="••••••" value={form.password ?? ''} onChange={(e) => setForm((f) => ({ ...f, password: e.target.value }))} />
                    </div>
                  </div>
                </>
              )}
              {mode === 'offline' && (
                <div className="form-row">
                  <div className="form-hint" style={{ marginTop: 0 }}>
                    离线库不保存任何连接信息；下一步通过 DDL / 模板导入元数据。
                  </div>
                </div>
              )}
              <div className="wiz-foot">
                <Button variant="ghost" onClick={onCancel}>
                  取消
                </Button>
                <div className="right">
                  <Button variant="accent" data-action="向导下一步" disabled={nextLocked} onClick={goNext}>
                    下一步
                  </Button>
                </div>
              </div>
            </div>
            <aside className="wiz-aside">
              <div className="aside-card">
                <h5>
                  <Icon name="file" size={13} />
                  注册摘要
                </h5>
                <div className="aside-row">
                  <span className="k">名称</span>
                  <span className="v">{form.name || '—'}</span>
                </div>
                <div className="aside-row">
                  <span className="k">方言</span>
                  <span className="v">{form.dialect === 'mysql' ? 'MySQL' : form.dialect === 'doris' ? 'Doris' : 'ClickHouse'}</span>
                </div>
                <div className="aside-row">
                  <span className="k">接入</span>
                  <span className="v">{mode === 'online' ? '在线' : '离线'}</span>
                </div>
                <div className="aside-row">
                  <span className="k">主机</span>
                  <span className="v mono">{mode === 'online' ? `${form.host || '—'}${form.port ? `:${form.port}` : ''}` : '—（离线）'}</span>
                </div>
                <div className="aside-row">
                  <span className="k">库名</span>
                  <span className="v mono">{form.db_name || '—'}</span>
                </div>
              </div>
              <div className="aside-card">
                <h5>
                  <Icon name="info" size={13} />
                  接下来会发生什么
                </h5>
                <ul className="aside-list">
                  {mode === 'online' ? (
                    <>
                      <li>测试连接与版本探测</li>
                      <li>首次摄取表 / 字段 / 分区键 / 行数估算</li>
                      <li>AI 补全注释与标准绑定建议（进评审队列）</li>
                    </>
                  ) : (
                    <>
                      <li>导入 DDL / 模板建立初始元数据</li>
                      <li>AI 补全注释与标准绑定建议（进评审队列）</li>
                      <li>查询时仅生成 SQL，提供复制</li>
                    </>
                  )}
                </ul>
              </div>
              <div className="aside-card">
                <h5>
                  <Icon name="lock" size={13} strokeWidth={2} />
                  安全边界
                </h5>
                <ul className="aside-list">
                  {mode === 'online' ? (
                    <>
                      <li>密码仅存本机 SQLite，永不外发</li>
                      <li>强烈建议只读账号</li>
                      <li>AI 生成的 SQL 默认只读执行</li>
                    </>
                  ) : (
                    <>
                      <li>不发起任何网络请求</li>
                      <li>DDL 仅用于解析元数据</li>
                    </>
                  )}
                </ul>
              </div>
            </aside>
          </div>
        )}

        {/* 第 2 步 · 在线：测试连接 */}
        {step === 2 && mode === 'online' && (
          <div className="wiz-cols">
            <div className="wiz-form">
              <div className="form-label" style={{ marginBottom: 4 }}>连接测试与版本探测</div>
              <div className="muted" style={{ marginBottom: 14 }}>测试通过才允许下一步；失败会给出可操作的修复建议。</div>
              <div>
                <Button size="sm" data-action="测试连接" disabled={testState === 'testing'} onClick={() => void testConnection()}>
                  <Icon name="wifi" size={13} strokeWidth={2} />
                  测试连接
                </Button>
              </div>
              {testState === 'testing' && (
                <div className="test-loading">
                  <span className="pulse-dot"></span>正在连接并探测版本…
                </div>
              )}
              {testState === 'ok' && (
                <div className="test-result ok">
                  <Icon name="check-circle" size={16} />
                  <div>{testMsg}</div>
                  <span className="ver-chip">{testVer}</span>
                </div>
              )}
              {testState === 'fail' && (
                <div className="test-result fail">
                  <Icon name="alert-circle" size={16} />
                  <div>
                    <b>连接失败</b>
                    <div className="fail-detail">
                      {testMsg.includes('E1001') ? (
                        <>
                          错误码 <code>E1001</code> {testMsg.replace(/^E1001\s*/, '')}
                        </>
                      ) : (
                        testMsg
                      )}
                    </div>
                  </div>
                </div>
              )}
              <div className="wiz-foot">
                <Button variant="ghost" onClick={() => setStep(1)}>
                  上一步
                </Button>
                <div className="right">
                  <Button variant="accent" data-action="向导下一步" disabled={nextLocked} onClick={goNext}>
                    下一步
                  </Button>
                </div>
              </div>
            </div>
            <aside className="wiz-aside">
              <div className="aside-card">
                <h5>
                  <Icon name="lock" size={13} strokeWidth={2} />
                  为什么强制只读
                </h5>
                <ul className="aside-list">
                  <li>AI 只生成 SELECT，但只读账号是最后一道防线</li>
                  <li>连接串只用于元数据摄取与查询执行</li>
                  <li>失败信息原文保留，便于运维定位</li>
                </ul>
              </div>
            </aside>
          </div>
        )}

        {/* 第 2 步 · 离线：元数据导入 */}
        {step === 2 && mode === 'offline' && (
          <div className="wiz-cols">
            <div className="wiz-form" style={{ maxWidth: 'none' }}>
              <div className="form-label" style={{ marginBottom: 4 }}>导入元数据（三选一）</div>
              <div className="muted" style={{ marginBottom: 14 }}>任意通道都会进同一个元数据评审队列，人工确认后落库。</div>
              <div className="import-cards">
                <div className={clsx('imp-card', impMode === 'ddl' && 'sel')} data-action="选择DDL导入" onClick={() => setImpMode('ddl')}>
                  <h5>
                    <Icon name="code" size={14} />
                    DDL 粘贴解析
                  </h5>
                  <p>粘贴 CREATE TABLE 语句，自动解析字段/注释</p>
                </div>
                <div className={clsx('imp-card', impMode === 'csv' && 'sel')} data-action="选择模板上传" onClick={() => setImpMode('csv')}>
                  <h5>
                    <Icon name="file" size={14} />
                    模板上传
                  </h5>
                  <p>下载 Excel 模板，批量填写表/字段后上传</p>
                </div>
                <div className={clsx('imp-card', impMode === 'ai' && 'sel')} data-action="选择AI录入" onClick={() => setImpMode('ai')}>
                  <h5>
                    <Icon name="spark" size={14} />
                    AI 对话录入
                  </h5>
                  <p>大白话描述表结构，AI 生成元数据草稿</p>
                </div>
              </div>

              {impMode === 'ddl' && (
                <>
                  <label className="form-label">粘贴 DDL（支持多条 CREATE TABLE）</label>
                  <textarea className="ddl-area" value={ddl} onChange={(e) => setDdl(e.target.value)} />
                  <div className="wiz-foot" style={{ marginTop: 12 }}>
                    <Button size="sm" data-action="解析DDL" disabled={!ddl.trim()} onClick={() => void parseDdl()}>
                      <Icon name="code" size={13} />
                      解析 DDL
                    </Button>
                  </div>
                  {preview && (
                    <div className="parsed-preview">
                      <div className="parsed-summary">
                        <Icon name="check-circle" size={14} />
                        解析完成：成功 <b>{preview.tables.length}</b> 张表
                        {preview.errors.length > 0 && (
                          <>
                            {' '}/ 失败 <b style={{ color: 'var(--red)' }}>{preview.errors.length}</b> 处 · 成功部分可直接入库
                          </>
                        )}
                      </div>
                      <div className="panel">
                        <table className="dt">
                          <thead>
                            <tr><th>表</th><th>字段</th><th>类型</th></tr>
                          </thead>
                          <tbody>
                            {preview.tables.flatMap((t) =>
                              t.columns.map((c, i) => (
                                <tr key={`${t.name}-${i}`}>
                                  <td className="mono-cell" style={{ color: 'var(--t1)' }}>
                                    {i === 0 ? `${t.name}${t.comment ? ` · ${t.comment}` : ''}` : ''}
                                  </td>
                                  <td className="mono-cell">{c.name}</td>
                                  <td className="mono-cell">{c.type}</td>
                                </tr>
                              )),
                            )}
                            {preview.errors.map((er, i) => (
                              <tr key={`err-${i}`} className="row-fail">
                                <td colSpan={3}>第 {er.line} 行：{er.message}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}
                </>
              )}
              {impMode === 'csv' && (
                <div className="aside-card" style={{ padding: 16 }}>
                  <h5>
                    <Icon name="file" size={13} />
                    模板格式
                  </h5>
                  <div style={{ fontSize: 12.5, color: 'var(--t2)', lineHeight: 1.7 }}>
                    11 列：表模式名 / 表英文名 / 表中文名 / 表业务描述 / 字段序号 / 字段英文名 / 字段中文名 / 字段类型 / 字段业务描述 / 是否主键 / 是否分区。
                    <br />
                    必填：表模式名、表英文名、表中文名、字段序号、字段英文名、字段中文名、字段类型、是否主键。
                    <br />
                    一行 = 一个字段；表级四列可合并单元格（留空沿用上一行）；是否列填 1/0 或是/否。
                  </div>
                  <div style={{ marginTop: 12, display: 'flex', gap: 8 }}>
                    <Button
                      size="sm"
                      data-action="下载模板"
                      onClick={() => {
                        const csv =
                          '表模式名,表英文名,表中文名,表业务描述,字段序号,字段英文名,字段中文名,字段类型,字段业务描述,是否主键,是否分区\n' +
                          'dataos_v5_dev,dataps_logic_model_field,逻辑模型字段信息,主要存储逻辑模型元数据信息,1,id,字段编码,varchar(32),系统内定义的逻辑模型字段唯一标识,1,\n';
                        const a = document.createElement('a');
                        a.href = URL.createObjectURL(new Blob(['﻿' + csv], { type: 'text/csv' }));
                        a.download = 'aitosql-metadata-template.csv';
                        a.click();
                        URL.revokeObjectURL(a.href);
                      }}
                    >
                      下载模板
                    </Button>
                    <Button size="sm" variant="ghost" data-action="上传模板" disabled={csvBusy} onClick={() => csvFileRef.current?.click()}>
                      {csvBusy ? '导入中…' : '上传文件'}
                    </Button>
                    <input
                      ref={csvFileRef}
                      type="file"
                      accept=".csv,text/csv"
                      style={{ display: 'none' }}
                      onChange={(e) => {
                        const f = e.target.files?.[0];
                        if (f) void uploadCsv(f);
                      }}
                    />
                  </div>
                  {csvErrors.length > 0 && (
                    <div style={{ marginTop: 10, fontSize: 12, color: 'var(--red)', lineHeight: 1.7 }}>
                      {csvErrors.slice(0, 5).map((er, i) => (
                        <div key={i}>第 {er.row} 行：{er.message}</div>
                      ))}
                      {csvErrors.length > 5 && <div>… 共 {csvErrors.length} 行失败</div>}
                    </div>
                  )}
                </div>
              )}
              {impMode === 'ai' && (
                <>
                  <div className="form-label" style={{ marginBottom: 6 }}>用大白话描述你的库表</div>
                  <textarea className="form-input" rows={5} placeholder="有一张订单表 orders，记录每笔订单：id、下单用户 user_id、渠道 channel_id、含税金额 amount、状态 status、下单时间 created_at…" />
                  <div className="ai-chat-hint">AI 会生成表结构、注释、维度枚举与关系建议，全部进入评审队列，你逐条裁决后才落库。</div>
                  <div className="wiz-foot" style={{ marginTop: 12 }}>
                    <Button size="sm" variant="ai" data-action="AI生成草稿" onClick={() => toast('AI 对话录入将在后续版本提供，当前请用 DDL 粘贴通道')}>
                      <Icon name="spark" size={13} />
                      AI 生成草稿
                    </Button>
                  </div>
                </>
              )}

              <div className="wiz-foot">
                <Button variant="ghost" onClick={() => setStep(1)}>
                  上一步
                </Button>
                <div className="right">
                  <Button variant="accent" data-action="向导下一步" disabled={nextLocked} onClick={goNext}>
                    下一步：AI 补全
                  </Button>
                </div>
              </div>
            </div>
            <aside className="wiz-aside">
              <div className="aside-card">
                <h5>
                  <Icon name="info" size={13} />
                  离线库说明
                </h5>
                <ul className="aside-list">
                  <li>不保存任何连接信息，永不发起网络请求</li>
                  <li>查询时只生成 SQL，执行按钮替换为「复制」</li>
                  <li>元数据可反复导入，diff 合并不覆盖人工修改</li>
                  <li>AI 补全用本地模型即可，数据不出本机</li>
                </ul>
              </div>
            </aside>
          </div>
        )}

        {/* 第 3 步 · 在线：摄取 + AI 补全 */}
        {step === 3 && mode === 'online' && (
          <div className="wiz-cols">
            <div className="wiz-form" style={{ maxWidth: 'none' }}>
              <div className="form-label" style={{ marginBottom: 4 }}>首次摄取与 AI 补全</div>
              <div className="muted">摄取后自动跑一轮 AI 语义补全；建议进入评审队列，不会直接落库。</div>
              <div className={clsx('progress-track', ingestFail && 'fail')}>
                <div className="progress-fill" style={{ width: `${ingestPct}%` }}></div>
              </div>
              <div className="ingest-log">
                {ingestLines.length === 0 && <div>等待开始…</div>}
                {ingestLines.map((l, i) => (
                  <div key={i} className={i === ingestLines.length - 1 && ingestDone ? 'ok' : ''}>{l}</div>
                ))}
              </div>
              {ingestFail && (
                <div className="fail-detail" style={{ color: 'var(--red)', fontSize: 12, marginTop: 8 }}>
                  摄取中断（E1002）：再次进入本步可断点续摄。
                </div>
              )}
              {ingestDone && sgTotal > 0 && (
                <div className="ai-banner" style={{ cursor: 'pointer' }} data-action="打开建议抽屉" onClick={() => { setSgRunId(null); setSgOpen(true); }}>
                  <Icon name="spark" size={16} />
                  AI 语义补全完成，生成 <b>{sgTotal}</b> 条建议（{sgGroups?.comment?.length ?? 0} 注释 · {sgGroups?.std_bind?.length ?? 0} 标准绑定 · {sgGroups?.relation?.length ?? 0} 关系确认）· 点击查看明细
                </div>
              )}
              {ingestDone && (
                <div style={{ marginTop: 10 }}>
                  <Button size="sm" variant="ghost" data-action="运行AI补全" onClick={() => setAiOpen(true)}>
                    <Icon name="spark" size={13} />
                    {sgTotal > 0 ? '重跑 AI 补全（可视化过程）' : '运行 AI 补全（可视化过程）'}
                  </Button>
                </div>
              )}
              <div className="wiz-foot">
                <div className="right">
                  <Button variant="accent" data-action="完成注册" disabled={committing || (!ingestDone && !mockEnabled())} onClick={() => void finish()}>
                    {committing ? '入库中…' : '完成注册'}
                  </Button>
                </div>
              </div>
            </div>
            <aside className="wiz-aside">
              <div className="aside-card">
                <h5>
                  <Icon name="spark" size={13} />
                  AI 补全做什么
                </h5>
                <ul className="aside-list">
                  <li>为缺注释的表/字段生成候选注释</li>
                  <li>建议字段 ↔ 字段标准绑定</li>
                  <li>合并命名约定关系并给出置信度</li>
                  <li>所有建议人工采纳后才生效</li>
                </ul>
              </div>
            </aside>
          </div>
        )}

        {/* 第 3 步 · 离线：AI 补全 */}
        {step === 3 && mode === 'offline' && (
          <div className="wiz-cols">
            <div className="wiz-form" style={{ maxWidth: 'none' }}>
              <div className="form-label" style={{ marginBottom: 4 }}>AI 补全</div>
              <div className="muted">基于已导入的元数据，AI 生成注释 / 标准绑定 / 关系建议——运行过程实时可见（泳道 + 日志），产出逐条核对后再采纳。</div>
              <div
                className="ai-banner"
                style={{ marginTop: 18, cursor: 'pointer' }}
                data-action="打开建议抽屉"
                onClick={() => { setSgRunId(null); setSgOpen(true); }}
              >
                <Icon name="spark" size={16} />
                {sgTotal > 0 ? (
                  <>
                    已有 <b>{sgTotal}</b> 条建议待评审（{sgGroups?.comment?.length ?? 0} 注释 · {sgGroups?.std_bind?.length ?? 0} 标准绑定 · {sgGroups?.relation?.length ?? 0} 关系确认）· 点击查看明细
                  </>
                ) : (
                  <>暂无建议——点击下方按钮运行 AI 补全，过程实时可见</>
                )}
              </div>
              <div style={{ marginTop: 10 }}>
                <Button size="sm" variant="ai" data-action="运行AI补全" onClick={() => setAiOpen(true)}>
                  <Icon name="spark" size={13} />
                  {sgTotal > 0 ? '重跑 AI 补全' : '运行 AI 补全'}
                </Button>
              </div>
              <div className="wiz-foot">
                <div className="right">
                  <Button variant="accent" data-action="完成注册" disabled={committing} onClick={() => void finish()}>
                    {committing ? '入库中…' : '完成注册'}
                  </Button>
                </div>
              </div>
            </div>
            <aside className="wiz-aside">
              <div className="aside-card">
                <h5>
                  <Icon name="spark" size={13} />
                  AI 补全做什么
                </h5>
                <ul className="aside-list">
                  <li>为缺注释的表/字段生成候选注释</li>
                  <li>建议字段 ↔ 字段标准绑定</li>
                  <li>合并命名约定关系并给出置信度</li>
                  <li>所有建议人工采纳后才生效</li>
                </ul>
              </div>
            </aside>
          </div>
        )}
      </div>

      {/* AI 建议评审抽屉（注册完成前即可预览；sgRunId 由补全完成视图带过滤打开） */}
      {createdId != null && (
        <SuggestionDrawer datasourceId={createdId} open={sgOpen} onClose={() => setSgOpen(false)} filterRunId={sgRunId} />
      )}
      {/* AI 补全流程模态（泳道可视化 + 产出逐条核对） */}
      {createdId != null && (
        <AiCompleteModal
          datasourceId={createdId}
          dsName={form.name || '未命名数据源'}
          scope="datasource"
          totalTables={tableItems?.length ?? 0}
          online={mode === 'online'}
          open={aiOpen}
          onClose={() => {
            setAiOpen(false);
            void refetchSg();
          }}
          onReview={(runId) => {
            setAiOpen(false);
            setSgRunId(runId);
            setSgOpen(true);
          }}
        />
      )}
    </div>
  );
}
