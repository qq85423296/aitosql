import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { clsx } from 'clsx';
import { sessionsApi } from '../../api/sessions';
import type { Session } from '../../api/types';
import { useSessionStore } from '../../stores/sessionStore';
import { toast } from '../ui/Toast';
import { Button } from '../ui/Button';
import { Modal } from '../ui/Modal';

/**
 * SessionDrawer（FR-Q1）：分组 置顶/今天/昨天/更早/归档折叠；行内 置顶/重命名/归档/删除（二次确认）；
 * ⌘K 过滤聚焦（D11）；列表排序 pinned>updated_at（C1.1）。
 */
export function SessionDrawer({
  currentId,
  onSelect,
  onNew,
}: {
  currentId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
}) {
  const qc = useQueryClient();
  const open = useSessionStore((s) => s.drawerOpen);
  const filter = useSessionStore((s) => s.filter);
  const setFilter = useSessionStore((s) => s.setFilter);
  const [archivedOpen, setArchivedOpen] = useState(false);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [renameVal, setRenameVal] = useState('');
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const filterRef = useRef<HTMLInputElement>(null);

  const { data: sessions = [] } = useQuery({ queryKey: ['sessions'], queryFn: sessionsApi.list });

  const patchMut = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Parameters<typeof sessionsApi.patch>[1] }) =>
      sessionsApi.patch(id, body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['sessions'] }),
  });
  const delMut = useMutation({
    mutationFn: (id: string) => sessionsApi.remove(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['sessions'] });
      setConfirmDelete(null);
      toast('会话已删除', 'ok');
    },
    onError: (e) => {
      // 409 = 会话内有进行中的查询；其余 = 服务端错误——弹层内可见反馈，不再静默
      toast(`删除失败：${(e as Error).message}`, 'err');
    },
  });

  // ⌘K 聚焦过滤（D11）
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        filterRef.current?.focus();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const groups = useMemo(() => {
    const kw = filter.trim().toLowerCase();
    const list = sessions.filter((s) => !kw || s.title.toLowerCase().includes(kw));
    const active = list.filter((s) => !s.archived);
    const archived = list.filter((s) => s.archived);
    const pinned = active.filter((s) => s.pinned);
    const rest = active.filter((s) => !s.pinned);
    const today = new Date().toDateString();
    const yesterday = new Date(Date.now() - 86400_000).toDateString();
    const g = {
      置顶: pinned,
      今天: rest.filter((s) => new Date(s.updated_at).toDateString() === today),
      昨天: rest.filter((s) => new Date(s.updated_at).toDateString() === yesterday),
      更早: rest.filter(
        (s) =>
          new Date(s.updated_at).toDateString() !== today &&
          new Date(s.updated_at).toDateString() !== yesterday,
      ),
      归档: archived,
    };
    return g;
  }, [sessions, filter]);

  if (!open) return null;

  const renderItem = (s: Session) => (
    <div
      key={s.id}
      className={clsx('session-item', s.id === currentId && 'active')}
      data-action="打开会话"
      onClick={() => onSelect(s.id)}
    >
      {renaming === s.id ? (
        <input
          className="form-input"
          style={{ padding: '2px 6px', fontSize: 12 }}
          value={renameVal}
          autoFocus
          onChange={(e) => setRenameVal(e.target.value)}
          onClick={(e) => e.stopPropagation()}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              patchMut.mutate({ id: s.id, body: { title: renameVal } });
              setRenaming(null);
            }
            if (e.key === 'Escape') setRenaming(null);
          }}
        />
      ) : (
        <>
          {!!s.pinned && <span aria-label="置顶">📌</span>}
          <span className="t">{s.title}</span>
          <span className="ops" onClick={(e) => e.stopPropagation()}>
            <button
              className="icon-btn"
              title="置顶"
              data-action="置顶会话"
              onClick={() => patchMut.mutate({ id: s.id, body: { pinned: !s.pinned } })}
            >
              📌
            </button>
            <button
              className="icon-btn"
              title="重命名"
              data-action="重命名会话"
              onClick={() => {
                setRenaming(s.id);
                setRenameVal(s.title);
              }}
            >
              ✎
            </button>
            <button
              className="icon-btn"
              title="归档"
              data-action="归档会话"
              onClick={() => patchMut.mutate({ id: s.id, body: { archived: true } })}
            >
              🗄
            </button>
            <button
              className="icon-btn"
              title="删除"
              data-action="删除会话"
              onClick={() => setConfirmDelete(s.id)}
            >
              🗑
            </button>
          </span>
        </>
      )}
    </div>
  );

  return (
    <aside className="session-drawer" aria-label="会话列表">
      <div className="hd">
        <input
          ref={filterRef}
          className="form-input"
          style={{ flex: 1 }}
          placeholder="搜索会话（⌘K）"
          data-action="搜索会话"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
        <Button size="sm" variant="accent" data-action="新建会话" onClick={onNew}>
          ＋
        </Button>
      </div>
      <div className="session-list">
        {(['置顶', '今天', '昨天', '更早'] as const).map(
          (g) =>
            groups[g].length > 0 && (
              <div key={g}>
                <div className="session-group-hd">{g}</div>
                {groups[g].map(renderItem)}
              </div>
            ),
        )}
        {groups.归档.length > 0 && (
          <div>
            <div
              className="session-group-hd"
              style={{ cursor: 'pointer' }}
              data-action="折叠归档组"
              onClick={() => setArchivedOpen(!archivedOpen)}
            >
              {archivedOpen ? '▾' : '▸'} 归档（{groups.归档.length}）
            </div>
            {archivedOpen && groups.归档.map(renderItem)}
          </div>
        )}
        {sessions.length > 0 && Object.values(groups).every((g) => g.length === 0) && (
          <div className="empty-state" style={{ padding: 24 }}>
            无匹配会话
          </div>
        )}
        {sessions.length === 0 && (
          <div className="empty-state" style={{ padding: 24 }}>
            <div className="big">还没有会话</div>
            <div>点击「＋」新建会话开始提问</div>
          </div>
        )}
      </div>
      <Modal
        open={!!confirmDelete}
        title="删除会话"
        onClose={() => setConfirmDelete(null)}
        footer={
          <>
            <Button variant="ghost" data-action="取消删除会话" onClick={() => setConfirmDelete(null)}>
              取消
            </Button>
            <Button variant="danger" data-action="确认删除会话" onClick={() => delMut.mutate(confirmDelete!)}>
              确认删除
            </Button>
          </>
        }
      >
        删除后不可恢复（含消息与事件溯源记录），确认删除？
      </Modal>
    </aside>
  );
}
