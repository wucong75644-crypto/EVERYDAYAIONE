from pathlib import Path

from scripts.migration_runner import discover_migrations


def migration_paths_through(root: Path, target_identity: str) -> tuple[Path, ...]:
    """Return one migration lane through an exact identity in runner order."""
    lane = target_identity.split("_", 1)[0] + "_"
    migrations = [
        migration
        for migration in discover_migrations(root / "migrations")
        if migration.identity.startswith(lane)
    ]
    identities = [migration.identity for migration in migrations]
    try:
        target_index = identities.index(target_identity)
    except ValueError as exc:
        raise AssertionError(f"migration not discovered: {target_identity}") from exc
    return tuple(migration.path for migration in migrations[: target_index + 1])


def unique_migration_path(root: Path, prefix: str) -> Path:
    """Resolve a single migration identity without filesystem glob ordering."""
    matches = [
        migration.path
        for migration in discover_migrations(root / "migrations")
        if migration.identity.startswith(prefix)
    ]
    if len(matches) != 1:
        raise AssertionError(
            f"expected exactly one migration for {prefix}, found {len(matches)}"
        )
    return matches[0]
