import { ulid } from 'ulid';

/** 业务 ID：`s_`/`q_` 前缀 + ULID（Crockford Base32 26 字符），C0/C2 */
export function newSessionId(): string {
  return `s_${ulid()}`;
}

/** client_request_id = thread_id = query_id，前端缺省生成（D-22） */
export function newQueryId(): string {
  return `q_${ulid()}`;
}
