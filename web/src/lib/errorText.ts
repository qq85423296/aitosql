/**
 * 错误码 → 前端表现（C8，SSOT §18.1）+ E3010 分方言五模板文案（SSOT §12.7 原文）。
 */
export type Dialect = 'mysql' | 'doris' | 'clickhouse';

export interface ErrorSpec {
  code: string;
  title: string;
  hint: string;
  retryable: boolean;
}

export const ERROR_SPECS: Record<string, ErrorSpec> = {
  E1001: { code: 'E1001', title: '数据源连接失败', hint: '向导红卡 + 下一步锁定', retryable: true },
  E1002: { code: 'E1002', title: '摄取中断', hint: '进度条标红，可重试（续摄）', retryable: true },
  E2001: { code: 'E2001', title: '模型端点无响应', hint: '可重试或切换路由；自动续跑场景见 SSOT §13', retryable: true },
  E2002: { code: 'E2002', title: '契约解析失败（重试后）', hint: '降级 SQL 卡（标注「未结构化输出」）', retryable: false },
  E2004: { code: 'E2004', title: '修正轮数耗尽/振荡终止', hint: '最近一版 SQL 已保留，可编辑重跑或调整提问', retryable: false },
  E3001: { code: 'E3001', title: 'EXPLAIN 不支持/失败', hint: '跳过干跑，标注「未经验证」', retryable: false },
  E3010: { code: 'E3010', title: 'R15 硬阻断', hint: '不进修正回路、不进确认门；原 SQL 照常交付（只读）', retryable: false },
  E4001: { code: 'E4001', title: '密钥解密失败', hint: '请在设置页重新录入（不迁移）', retryable: false },
};

/** R15 五断言（E3010 正文模板，SSOT §12.7 原文逐字） */
export const R15_ASSERTIONS: { id: string; text: (d: Dialect) => string }[] = [
  {
    id: 'B1',
    text: () => '检测到多条语句。为保证可复核性，每次只执行一条 SQL，请拆分后分别提问',
  },
  {
    id: 'B2',
    text: () => '语句无法安全解析（可能含文件读写/插件/存储过程类语法），已阻断',
  },
  {
    id: 'B3',
    text: (d) =>
      d === 'doris'
        ? 'INTO OUTFILE s3:// 属于出境通道，已阻断'
        : 'INTO OUTFILE/DUMPFILE 会写服务器文件，已阻断；导出请用结果卡「导出 CSV」',
  },
  {
    id: 'B4',
    text: () => 'EXPLAIN ANALYZE 会真实执行语句，已禁用；干跑仅用静态 EXPLAIN',
  },
  {
    id: 'B5',
    text: () => '存储过程/DO 调用不透明、无法校验，已硬阻断',
  },
];

export function errorSpec(code: string | undefined): ErrorSpec {
  if (code && ERROR_SPECS[code]) return ERROR_SPECS[code];
  return { code: code ?? 'E????', title: '未知错误', hint: '', retryable: false };
}
