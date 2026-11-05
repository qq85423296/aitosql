import { useEffect, useRef, useState } from 'react';
import { EditorView, keymap, lineNumbers, highlightActiveLine } from '@codemirror/view';
import { EditorState } from '@codemirror/state';
import { defaultKeymap, history, historyKeymap } from '@codemirror/commands';
import { sql as sqlLang } from '@codemirror/lang-sql';
import { clsx } from 'clsx';
import type { Dialect } from '../../../api/types';
import { toast } from '../../ui/Toast';
import { Button } from '../../ui/Button';

/**
 * SqlBlock（FR-Q7）：只读高亮（行号 + 关键字/函数/字符串/数字四色，对齐原型）；
 * 编辑态挂 CodeMirror 6（lang-sql），可重跑；离线库主按钮为「复制 SQL」（F7/D04）。
 * 所验即所执：展示串 = confirm_gate 展示串 = execute 串（C5.sql）。
 */

const KW_WORDS =
  'SELECT|FROM|WHERE|GROUP|BY|ORDER|JOIN|LEFT|RIGHT|INNER|OUTER|ON|AND|OR|NOT|IN|AS|WITH|LIMIT|OFFSET|INSERT|UPDATE|DELETE|SET|VALUES|INTO|CREATE|TABLE|PARTITION|OVER|CASE|WHEN|THEN|ELSE|END|DISTINCT|HAVING|UNION|ALL|DESC|ASC|IS|NULL|LIKE|BETWEEN|EXISTS';
const FN_WORDS =
  'SUM|COUNT|AVG|MIN|MAX|DATE_TRUNC|LAG|LEAD|ROUND|COALESCE|CAST|NOW|DATE|DATE_FORMAT|SUBSTRING|CONCAT|IFNULL|ROW_NUMBER';
/** 单遍分词（Q-15 修复）：字符串整体优先成 token，字面量内的数字/关键字不再被二次着色 */
const TOKEN = new RegExp(`('[^']*')|(\\b\\d+(?:\\.\\d+)?\\b)|(\\b(?:${KW_WORDS})\\b)|(\\b(?:${FN_WORDS})\\b)`, 'gi');

function esc(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

/** 只读高亮：交替正则单遍扫描——字符串 → 数字 → 关键字 → 函数，未命中片段原样转义输出 */
export function highlightSql(code: string): string {
  let out = '';
  let last = 0;
  for (const m of code.matchAll(TOKEN)) {
    out += esc(code.slice(last, m.index));
    const cls = m[1] ? 'sql-s' : m[2] ? 'sql-n' : m[3] ? 'sql-k' : 'sql-f';
    const text = m[3] ? m[0].toUpperCase() : m[0];
    out += `<span class="${cls}">${esc(text)}</span>`;
    last = m.index + m[0].length;
  }
  return out + esc(code.slice(last));
}

export function SqlBlock({
  sql,
  dialect,
  writeOp,
  offline,
  unstructured,
  onRerun,
}: {
  sql: string;
  dialect?: Dialect;
  writeOp?: boolean; // 写操作：琥珀边（F2）
  offline?: boolean; // 离线库：只生成不执行（F7）
  unstructured?: boolean; // E2002 降级标注「未结构化输出」
  onRerun?: (edited: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const editorRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);

  useEffect(() => {
    if (!editing || !editorRef.current) return;
    const view = new EditorView({
      state: EditorState.create({
        doc: sql,
        extensions: [
          lineNumbers(),
          history(),
          highlightActiveLine(),
          keymap.of([...defaultKeymap, ...historyKeymap]),
          sqlLang(),
          EditorView.theme({}, { dark: true }),
        ],
      }),
      parent: editorRef.current,
    });
    viewRef.current = view;
    return () => {
      view.destroy();
      viewRef.current = null;
    };
  }, [editing, sql]);

  const copy = async () => {
    await navigator.clipboard.writeText(sql);
    toast('SQL 已复制', 'ok');
  };

  return (
    <div className={clsx('sql-block', writeOp && 'write-warn')}>
      <div className="sql-hd">
        <span className="badge dialect-badge">{dialect ?? 'sql'}</span>
        {writeOp && <span style={{ color: 'var(--amber)' }}>写操作</span>}
        {unstructured && <span style={{ color: 'var(--amber)' }}>未结构化输出（E2002 降级）</span>}
        {offline && <span style={{ color: 'var(--amber)' }}>离线库 · 仅生成不执行</span>}
        <span className="ops">
          <Button size="sm" variant="ghost" data-action="复制SQL" onClick={copy}>
            复制 SQL
          </Button>
          {!offline && onRerun && (
            <Button
              size="sm"
              variant="ghost"
              data-action="编辑重跑SQL"
              onClick={() => {
                if (editing && viewRef.current) {
                  onRerun(viewRef.current.state.doc.toString());
                  setEditing(false);
                } else {
                  setEditing(true);
                }
              }}
            >
              {editing ? '重跑' : '编辑重跑'}
            </Button>
          )}
        </span>
      </div>
      {editing ? (
        <div ref={editorRef} />
      ) : (
        <div className="sql-code" role="code">
          {sql.split('\n').map((line, i) => (
            <div className="sql-line" key={i}>
              <span className="sql-ln">{i + 1}</span>
              <span className="sql-txt" dangerouslySetInnerHTML={{ __html: highlightSql(line) || ' ' }} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
