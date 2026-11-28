"""Current-input completion evidence for saved drafts, including before adoption.

The model chooses and authors. Only committed records and native receipts can
satisfy the already selected action; this module never selects or dispatches.
"""

def saved_revision(runtime, request, target):
    from .content_revision import saved_reference
    return saved_reference(runtime, request, target)


def phase(runtime, request):
    from .content_action import evaluate
    return evaluate(runtime, request).completion


def incomplete_text(pending):
    if pending['stage'] == 'execute_required':
        return '本次要求的图片尚未提交，已保存的稿件仍保留。'
    if pending['stage'] == 'author_required':
        return '本次方案尚未完整保存，未提交图片任务。'
    return '本次要求的稿件修订尚未保存成功，没有提交新的图片任务。'
