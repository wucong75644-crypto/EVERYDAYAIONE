"""Permanent legacy-store retirement; deliberately not an environment flag.

Old names remain only for graceful handling of saved tasks/import compatibility.
The active knowledge system is knowledge_workspace.
"""
RETIRED = True
RETIRED_TOOLS = frozenset({'search_knowledge'})
