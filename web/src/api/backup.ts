/** C1.6 备份：VACUUM INTO 滚动 keep=10；剥离密文；恢复提示含「不适用自动续跑」 */
import { api } from './client';
import type { BackupListRes } from './types';

export const backupApi = {
  /** C1.6 冻结（P1-10）：POST /backup 返回 `{file}`（文件名） */
  create: () => api.post<{ file: string }>('/backup'),
  /** C1.6 冻结（P1-10）：GET /backup/list 返回 `{items, restore_hint}`（FR-C8 恢复提示随列表下发） */
  list: () => api.get<BackupListRes>('/backup/list'),
};
