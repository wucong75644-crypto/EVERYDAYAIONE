"""Knowledge access instructions and legacy deferred scope compatibility.

The actual knowledge snapshot is restored from an explicit obligation or
created by the existing discover tool when the model requests knowledge.
"""
from dataclasses import dataclass


INSTRUCTIONS = ('[Knowledge access policy]\n'
    '知识是按需工具，不是每项任务的前置流程。结合用户原文、相关历史、附件和已有成果判断信息缺口。'
    '用户已提供的资料足够时直接处理，不重复检索；需要内部知识且尚无根入口时使用knowledge_discover，'
    '再按返回入口搜索、读取原文。目录和可用工具说明不证明任何业务事实。'
    '明确引用和已保存的知识范围仍须遵守，不能自行豁免依据、权限或审批。'
    '历史资料和偏好不是本题已经核实的业务事实；需要内部事实时先查询或恢复原来源。'
    '资料不足时说明缺口或澄清，不编造制度、私有数据或声称已读取未取得的资料。')


@dataclass(frozen=True)
class DeferredKnowledgeScope:
    repo: object
    conversation_id: str
    input_message_id: str
    refs: tuple[dict, ...]

    def revalidate(self):
        from .chat import check_roots, unavailable
        with self.repo.cursor() as cursor:
            cursor.execute("SELECT knowledge_refs FROM public.messages WHERE id=%s AND conversation_id=%s AND role='user'",
                           (self.input_message_id, self.conversation_id))
            message = cursor.fetchone()
            # Explicit references must be restored as a real KnowledgeRun,
            # never downgraded to this legacy availability-only scope.
            if message is None or message['knowledge_refs']:
                unavailable('KNOWLEDGE_SNAPSHOT_MISMATCH')
            check_roots(cursor, self.refs)

    def grounding_sources(self):
        self.revalidate()
        return None
