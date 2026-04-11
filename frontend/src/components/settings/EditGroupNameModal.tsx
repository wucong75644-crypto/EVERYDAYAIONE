/**
 * 编辑群名 Modal — 子 modal
 *
 * 单字段编辑：chat_name
 */
import { useState } from 'react';
import Modal from '../common/Modal';
import { Input } from '../ui/Input';
import { Button } from '../ui/Button';
import { wecomChatTargetsService } from '../../services/wecomChatTargets';
import { logger } from '../../utils/logger';
import type { WecomGroup } from '../../types/wecomChatTargets';

interface Props {
  group: WecomGroup;
  onClose: () => void;
  onSaved: () => void;
}

export default function EditGroupNameModal({ group, onClose, onSaved }: Props) {
  const [name, setName] = useState(group.chat_name || '');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async () => {
    setError(null);
    if (!name.trim()) {
      setError('群名不能为空');
      return;
    }

    setSubmitting(true);
    try {
      await wecomChatTargetsService.updateName(group.id, {
        chat_name: name.trim(),
      });
      onSaved();
    } catch (e) {
      logger.error('edit-group', '保存失败', e);
      setError('保存失败，请重试');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      isOpen={true}
      onClose={onClose}
      title="编辑群名"
      maxWidth="max-w-md"
    >
      <div className="space-y-4">
        <div>
          <label className="block text-sm font-medium text-[var(--s-text-secondary)] mb-1.5">
            群名 *
          </label>
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="给这个群起个名字（如：运营群、客服群）"
            autoFocus
          />
          <p className="text-xs text-[var(--s-text-tertiary)] mt-1">
            企微 API 拿不到群名，所有群名都靠手动标注。<br />
            chatid: <code className="text-[10px]">{group.chatid}</code>
          </p>
        </div>

        {error && (
          <div className="text-xs text-[var(--s-error)] bg-[var(--s-error-soft)] px-3 py-2 rounded">
            {error}
          </div>
        )}

        <div className="flex items-center justify-end gap-2 pt-2">
          <Button variant="secondary" size="sm" onClick={onClose}>
            取消
          </Button>
          <Button
            variant="accent"
            size="sm"
            loading={submitting}
            onClick={handleSubmit}
          >
            保存
          </Button>
        </div>
      </div>
    </Modal>
  );
}
