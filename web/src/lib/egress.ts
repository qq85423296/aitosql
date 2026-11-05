/**
 * 出境清单矩阵 E1-E8（C9.2，SSOT §12.5）——设置页 RouteSection 联动常量。
 * E6 红标：仅 verify_llm 开启且含云路由时出现。local_only（三路全本地）= 零出境，清单隐藏。
 */
export interface EgressItem {
  id: string; // E1..E8
  name: string;
  content: string;
  red?: boolean;
  onlyWhenVerifyLlm?: boolean;
}

export const EGRESS_ITEMS: EgressItem[] = [
  { id: 'E1', name: '问题文本', content: '自然语言原文' },
  { id: 'E2', name: 'schema 注入', content: '表名/字段名/注释摘录' },
  { id: 'E3', name: '契约与假设', content: 'reasoning/assumptions/SQL' },
  { id: 'E4', name: '修正错误历史', content: '已脱敏错误' },
  { id: 'E5', name: '样例', content: 'few-shot 样例' },
  { id: 'E6', name: '结果预览行（前 5 行）', content: 'verify_llm 输入', red: true, onlyWhenVerifyLlm: true },
  { id: 'E7', name: '元数据注释', content: 'AI 补全路由输入' },
  { id: 'E8', name: '系统提示', content: '模板文本' },
];

/** 绝不外发（C9.2） */
export const EGRESS_NEVER = ['凭证', '完整结果集', '连接地址明文'];
