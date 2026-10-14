"""Server-owned discovery boundaries; published documents and pins stay immutable."""

SOCIAL_WORKFLOW_SKILLS = frozenset({
    'easel-competitor-analysis', 'easel-content-gap-analysis', 'easel-topic-evaluator',
    'easel-content-calendar', 'easel-xhs-note-creator', 'easel-text-polisher',
    'easel-content-postmortem', 'easel-comment-insights', 'easel-strategy-advisor',
    'easel-quality-gate', 'easel-risk-scanner', 'easel-card-design', 'easel-card-xiaohongshu',
})
TEST_ONLY_SKILLS = frozenset({
    'a001', 'asset-test-0921', 'recommendation-smoke-20260926', 'test-daily-report',
})


def discoverable(skill_key: str, scope_kind: str, execution_mode: str) -> bool:
    # Personal methods are owned by their creator; never classify them by name.
    if scope_kind == 'personal':
        return True
    if skill_key in TEST_ONLY_SKILLS:
        return False
    return skill_key not in SOCIAL_WORKFLOW_SKILLS or execution_mode in {'scheduled', 'preflight'}
