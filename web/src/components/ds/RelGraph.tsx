import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { datasourcesApi } from '../../api/datasources';
import { relationsApi } from '../../api/relations';
import type { GraphLayout, Relation } from '../../api/types';
import { Button } from '../ui/Button';
import { Icon } from '../ui/icons';
import { toast } from '../ui/Toast';
import { fmtInt } from '../../lib/format';
import { AiCompleteModal } from './AiCompleteModal';
import { SuggestionDrawer } from './SuggestionDrawer';

/**
 * RelGraph（FR-D10，原型「关系图谱」Tab）：原生 SVG ER 图谱（D-18）——
 * 曲边 + 箭头 + 字段标签 / 三态边（外键实线蓝 · 推测虚线琥珀可隐藏 · 手动/已确认实线绿）/
 * 拖动平移 · 滚轮缩放 · 节点拖拽重排（落点即存 graph_layout，P1-4）/ 搜索定位 /
 * 点节点/连线出 g-info 详情卡 / 图例。不引图库（原型即 SVG 手写；非目标含力导向）。
 */
const NW = 180;
const NH = 64;

interface NodePos {
  x: number;
  y: number;
}

/** 矩形边缘交点（边端点落在节点边框上，原型 rectEdge） */
function rectEdge(cx: number, cy: number, tx: number, ty: number) {
  const dx = tx - cx;
  const dy = ty - cy;
  if (!dx && !dy) return { x: cx, y: cy };
  const sx = dx ? NW / 2 / Math.abs(dx) : Infinity;
  const sy = dy ? NH / 2 / Math.abs(dy) : Infinity;
  const s = Math.min(sx, sy);
  return { x: cx + dx * s, y: cy + dy * s };
}

export function RelGraph({
  datasourceId,
  onOpenTable,
}: {
  datasourceId: number;
  onOpenTable?: (tableId: number) => void;
}) {
  const qc = useQueryClient();
  const { data: graph } = useQuery({ queryKey: ['graph', datasourceId], queryFn: () => datasourcesApi.graph(datasourceId) });
  const { data: tables = [] } = useQuery({ queryKey: ['tables', datasourceId], queryFn: () => datasourcesApi.tables(datasourceId) });
  const { data: savedLayout } = useQuery({ queryKey: ['graph-layout', datasourceId], queryFn: () => datasourcesApi.graphLayout.get(datasourceId) });

  const [pos, setPos] = useState<Record<string, NodePos>>({});
  const posRef = useRef(pos);
  posRef.current = pos;
  const [view, setView] = useState({ x: 20, y: 10, k: 1 });
  const [showInferred, setShowInferred] = useState(true);
  const [selNode, setSelNode] = useState<string | null>(null);
  const [selEdge, setSelEdge] = useState<number | null>(null);
  const [aiOpen, setAiOpen] = useState(false);
  const [aiDrawerRun, setAiDrawerRun] = useState<number | null>(null);
  const dragNodeRef = useRef<{ table: string; dx: number; dy: number } | null>(null);
  const panningRef = useRef<{ sx: number; sy: number; px: number; py: number } | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  // 摆位初始化：savedLayout 优先；缺失按 3 列确定性平铺（C6.17 兜底）
  useEffect(() => {
    if (!graph) return;
    setPos((prev) => {
      const next = { ...prev };
      const names = graph.nodes.map((n) => n.table).sort();
      names.forEach((name, i) => {
        if (savedLayout?.[name]) next[name] = savedLayout[name];
        else if (!next[name]) next[name] = { x: 170 + (i % 3) * 300, y: 80 + Math.floor(i / 3) * 170 };
      });
      return next;
    });
  }, [graph, savedLayout]);

  const saveMut = useMutation({
    mutationFn: (layout: GraphLayout) => datasourcesApi.graphLayout.put(datasourceId, layout),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['graph-layout', datasourceId] });
      toast('图谱摆位已保存', 'ok');
    },
    // 静默失败面治理：保存失败必须显式提示
    onError: (e) => toast(`摆位保存失败：${(e as Error).message}`, 'err'),
  });
  const confirmRel = useMutation({
    mutationFn: (id: number) => relationsApi.confirm(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['graph', datasourceId] });
      void qc.invalidateQueries({ queryKey: ['relations', datasourceId] });
      setSelEdge(null);
      toast('关系已确认，将作为 join 依据注入提示词', 'ok');
    },
  });
  const delRel = useMutation({
    mutationFn: (id: number) => relationsApi.remove(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['graph', datasourceId] });
      void qc.invalidateQueries({ queryKey: ['relations', datasourceId] });
      setSelEdge(null);
      toast('关系已删除', 'ok');
    },
  });

  const edges: Relation[] = useMemo(
    () => (graph?.edges ?? []).filter((e) => showInferred || !(e.source === 'inferred' && e.status === 'pending')),
    [graph, showInferred],
  );
  const tableOf = useCallback((name: string) => tables.find((t) => t.name === name), [tables]);
  const cnOf = useCallback((name: string) => tableOf(name)?.comment || '（缺注释）', [tableOf]);

  /** 屏幕坐标 → 图坐标 */
  const toSvg = useCallback(
    (e: { clientX: number; clientY: number }) => {
      const rect = svgRef.current?.getBoundingClientRect();
      if (!rect) return { x: 0, y: 0 };
      return { x: (e.clientX - rect.left - view.x) / view.k, y: (e.clientY - rect.top - view.y) / view.k };
    },
    [view],
  );

  const onWheel = (e: React.WheelEvent) => {
    e.preventDefault?.();
    const f = e.deltaY < 0 ? 1.12 : 0.89;
    const rect = svgRef.current?.getBoundingClientRect();
    if (!rect) return;
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const wx = (mx - view.x) / view.k;
    const wy = (my - view.y) / view.k;
    const k = Math.min(2.2, Math.max(0.35, view.k * f));
    setView({ x: mx - wx * k, y: my - wy * k, k });
  };

  const onMouseMove = (e: React.MouseEvent) => {
    if (panningRef.current) {
      const p = panningRef.current;
      setView((v) => ({ ...v, x: p.px + e.clientX - p.sx, y: p.py + e.clientY - p.sy }));
    }
    if (dragNodeRef.current) {
      const p = toSvg(e);
      const { table, dx, dy } = dragNodeRef.current;
      setPos((prev) => ({ ...prev, [table]: { x: p.x - dx, y: p.y - dy } }));
    }
  };
  const onMouseUp = () => {
    if (dragNodeRef.current) {
      dragNodeRef.current = null;
      // 防御：仅保存真实表名键（历史曾把 pos["undefined"] 写入 graph_layout 致全图堆叠）
      const valid = new Set((graph?.nodes ?? []).map((n) => n.table));
      const clean = Object.fromEntries(
        Object.entries(posRef.current).filter(([k]) => valid.has(k)),
      );
      saveMut.mutate(clean); // 拖拽落点即保存摆位（graph_layout）
    }
    panningRef.current = null;
  };

  const locateNode = (q: string) => {
    const t = graph?.nodes.find((n) => n.table.includes(q) || (n.comment ?? '').includes(q));
    if (!t) {
      toast(`未找到表 ${q}`);
      return;
    }
    const p = pos[t.table];
    if (!p) return;
    const wrap = svgRef.current?.getBoundingClientRect();
    const k = 1.2;
    setView({ x: (wrap?.width ?? 800) / 2 - (p.x + NW / 2) * k, y: (wrap?.height ?? 500) / 2 - (p.y + NH / 2) * k, k });
    setSelNode(t.table);
    setSelEdge(null);
  };

  if (!graph) return <div className="empty-state">图谱加载中…</div>;

  const selRel = selEdge != null ? (graph.edges ?? []).find((r) => r.id === selEdge) : null;
  const selTableMeta = selNode ? tableOf(selNode) : undefined;

  return (
    <div className="graph-wrap">
      <div className="graph-hint">拖动平移 · 滚轮缩放 · 拖动节点重排 · 点击节点/连线查看详情</div>
      <div className="graph-tools">
        <div className="g-search">
          <Icon name="search" size={13} strokeWidth={2} />
          <input
            placeholder="定位表…"
            data-action="图谱定位"
            onKeyDown={(e) => {
              if (e.key === 'Enter') locateNode((e.target as HTMLInputElement).value.trim());
            }}
          />
        </div>
        <span className="g-toggle" data-action="切换推测关系显示" onClick={() => setShowInferred(!showInferred)}>
          <span className={`mini-switch${showInferred ? ' on' : ''}`}></span>
          推测关系
        </span>
        <button className="g-btn" data-action="重置图谱视图" onClick={() => setView({ x: 20, y: 10, k: 1 })}>
          <Icon name="refresh" size={12} strokeWidth={2} />
          重置视图
        </button>
        <button className="g-btn" data-action="推断关系" onClick={() => setAiOpen(true)}>
          <Icon name="spark" size={12} />
          推断关系
        </button>
      </div>
      <div className="graph-legend">
        <div className="legend-item">
          <span className="legend-line"></span>外键（探测）
        </div>
        <div className="legend-item">
          <span className="legend-line dashed"></span>推测关系（待确认）
        </div>
        <div className="legend-item">
          <span className="legend-line manual"></span>手动 / 已确认
        </div>
      </div>
      <svg
        ref={svgRef}
        className="graph-svg"
        role="img"
        aria-label="ER 关系图谱"
        onWheel={onWheel}
        onMouseMove={onMouseMove}
        onMouseUp={onMouseUp}
        onMouseLeave={onMouseUp}
        onMouseDown={(e) => {
          panningRef.current = { sx: e.clientX, sy: e.clientY, px: view.x, py: view.y };
        }}
        onClick={(e) => {
          if (e.target === svgRef.current || (e.target as Element).tagName === 'svg') {
            setSelNode(null);
            setSelEdge(null);
          }
        }}
      >
        <defs>
          <marker id="arr-a" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
            <path d="M0,0 L8,4 L0,8 z" fill="var(--accent)" />
          </marker>
          <marker id="arr-i" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
            <path d="M0,0 L8,4 L0,8 z" fill="var(--amber)" />
          </marker>
          <marker id="arr-m" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
            <path d="M0,0 L8,4 L0,8 z" fill="var(--green)" />
          </marker>
        </defs>
        <g transform={`translate(${view.x},${view.y}) scale(${view.k})`}>
          {edges.map((r) => {
            const a = pos[r.from_table];
            const b = pos[r.to_table];
            if (!a || !b) return null;
            const ac = { x: a.x + NW / 2, y: a.y + NH / 2 };
            const bc = { x: b.x + NW / 2, y: b.y + NH / 2 };
            const p1 = rectEdge(ac.x, ac.y, bc.x, bc.y);
            const p2 = rectEdge(bc.x, bc.y, ac.x, ac.y);
            const mx = (p1.x + p2.x) / 2;
            const my = (p1.y + p2.y) / 2;
            const nx = -(p2.y - p1.y);
            const ny = p2.x - p1.x;
            const nl = Math.hypot(nx, ny) || 1;
            const cx = mx + (nx / nl) * 26;
            const cy = my + (ny / nl) * 26;
            const cls = r.source === 'inferred' && r.status === 'pending' ? 'inferred' : 'manual';
            const marker = cls === 'inferred' ? 'arr-i' : 'arr-m';
            let dim = '';
            if (selNode && r.from_table !== selNode && r.to_table !== selNode) dim = 'dim';
            if (selEdge === r.id) dim = 'sel';
            const d = `M${p1.x},${p1.y} Q${cx},${cy} ${p2.x},${p2.y}`;
            return (
              <g key={r.id}>
                <path
                  d={d}
                  stroke="transparent"
                  strokeWidth={14}
                  fill="none"
                  style={{ cursor: 'pointer' }}
                  onClick={(e) => {
                    e.stopPropagation();
                    setSelEdge(r.id);
                    setSelNode(null);
                  }}
                />
                <path className={`gedge ${cls} ${dim}`} d={d} markerEnd={`url(#${marker})`} pointerEvents="none" />
                <text className="glabel" x={cx} y={cy - 6} textAnchor="middle">
                  {r.from_col} → {r.to_col}
                </text>
              </g>
            );
          })}
          {graph.nodes.map((n) => {
            const p = pos[n.table] ?? { x: 0, y: 0 };
            let cls = 'gnode';
            if (selNode) {
              const linked = (graph.edges ?? []).some(
                (r) => (r.from_table === selNode && r.to_table === n.table) || (r.to_table === selNode && r.from_table === n.table),
              );
              cls += n.table === selNode ? ' sel' : linked ? '' : ' dim';
            }
            const meta = tableOf(n.table);
            return (
              <g
                key={n.table}
                className={cls}
                transform={`translate(${p.x},${p.y})`}
                onMouseDown={(e) => {
                  const sp = toSvg(e);
                  dragNodeRef.current = { table: n.table, dx: sp.x - p.x, dy: sp.y - p.y };
                  e.stopPropagation();
                }}
                onClick={(e) => {
                  e.stopPropagation();
                  setSelNode(n.table);
                  setSelEdge(null);
                }}
              >
                <rect width={NW} height={NH} rx={10} />
                <text x={14} y={26} fontSize={13} fontWeight={700} fontFamily="var(--mono)">
                  {n.table}
                </text>
                <text className="g-fields" x={NW - 12} y={26} fontSize={10} textAnchor="end">
                  {meta ? `${meta.fields}f` : ''}
                </text>
                <text className="g-comment" x={14} y={46} fontSize={11}>
                  {n.comment || '（缺注释）'}
                </text>
              </g>
            );
          })}
        </g>
      </svg>

      {/* 节点详情卡 */}
      {selNode && (
        <div className="g-info">
          <button className="gi-close" aria-label="关闭详情" onClick={() => setSelNode(null)}>×</button>
          <h4 style={{ fontFamily: 'var(--mono)' }}>{selNode}</h4>
          <div className="gi-sub">
            {cnOf(selNode)} · {selTableMeta?.fields ?? '—'} 字段 · 约 {fmtInt(selTableMeta?.rows)} 行
          </div>
          <div className="gi-row">
            <span className="k">主键</span>
            <span className="v">{selTableMeta?.pk?.length ? selTableMeta.pk.join(' + ') : '—'}</span>
          </div>
          <div className="gi-row">
            <span className="k">分区键</span>
            <span className="v" style={selTableMeta?.partition ? { color: 'var(--amber)' } : undefined}>
              {selTableMeta?.partition ? `${selTableMeta.partition === 'month' ? '按月' : '按日'}` : '无（小表）'}
            </span>
          </div>
          {(graph.edges ?? [])
            .filter((r) => r.from_table === selNode || r.to_table === selNode)
            .map((r) => (
              <div className="gi-row" key={r.id}>
                <span className="k">{r.from_table === selNode ? '引出' : '引入'}</span>
                <span className="v">
                  {r.from_table}（{cnOf(r.from_table)}）.{r.from_col}
                  <br />→ {r.to_table}（{cnOf(r.to_table)}）.{r.to_col}
                </span>
              </div>
            ))}
          {onOpenTable && selTableMeta && (
            <div className="gi-actions">
              <Button size="sm" variant="accent" data-action="打开表详情" onClick={() => onOpenTable(selTableMeta.id)}>
                查看表详情
              </Button>
            </div>
          )}
        </div>
      )}

      {/* 边详情卡 */}
      {selRel && (
        <div className="g-info">
          <button className="gi-close" aria-label="关闭详情" onClick={() => setSelEdge(null)}>×</button>
          <h4 style={{ fontSize: 13 }}>关系详情</h4>
          <div className="gi-sub" style={{ fontFamily: 'var(--mono)' }}>
            {selRel.from_table}（{cnOf(selRel.from_table)}）.{selRel.from_col}
            <br />→ {selRel.to_table}（{cnOf(selRel.to_table)}）.{selRel.to_col}
          </div>
          <div className="gi-row">
            <span className="k">来源</span>
            <span className="v plain">{selRel.source === 'inferred' ? '推测（命名约定/AI）' : selRel.source === 'manual' ? '手动添加' : '外键探测'}</span>
          </div>
          <div className="gi-row">
            <span className="k">置信度</span>
            <span className="v">{Math.round(selRel.confidence * 100)}%</span>
          </div>
          <div className="gi-row">
            <span className="k">状态</span>
            <span className="v plain">{selRel.status === 'pending' ? '待确认' : '已确认'}</span>
          </div>
          <div className="gi-actions">
            {selRel.status === 'pending' && (
              <Button size="sm" variant="accent" data-action="关系确认" onClick={() => confirmRel.mutate(selRel.id)}>
                确认此关系
              </Button>
            )}
            <Button size="sm" variant="ghost" data-action="关系删除" onClick={() => delRel.mutate(selRel.id)}>
              删除
            </Button>
          </div>
        </div>
      )}

      {/* 推断关系（方案 v1.1 §7：scope=relations，规则零出境；完成可进评审抽屉） */}
      {aiOpen && (
        <AiCompleteModal
          datasourceId={datasourceId}
          dsName=""
          scope="relations"
          totalTables={tables.length}
          online
          open
          onClose={() => setAiOpen(false)}
          onReview={(runId) => {
            setAiOpen(false);
            setAiDrawerRun(runId);
          }}
        />
      )}
      <SuggestionDrawer
        datasourceId={datasourceId}
        open={aiDrawerRun !== null}
        filterRunId={aiDrawerRun}
        onClose={() => setAiDrawerRun(null)}
      />
    </div>
  );
}
