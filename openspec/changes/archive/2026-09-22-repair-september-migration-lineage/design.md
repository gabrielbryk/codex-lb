## Context

The SCIM and overflow-retirement revisions are already merged and may have been used by other builds. Their identifiers and operations remain fixed. See `proposal.md` for the observed CI failure.

## Goals / Non-Goals

**Goals:** Join both revision branches with one no-op merge revision, preserve their existing behavior, and make the topology guard recognize that explicit resolution.

**Non-Goals:** Rewriting migration history, changing runtime schemas beyond what the two existing migrations already do, or changing deployment orchestration.

## Decisions

- Add a timestamped Alembic merge revision with both existing revisions as parents. Alembic then applies whichever branch is missing before recording the single merge head.
- Keep the same-prefix guard for unresolved branches and chained revisions. Waive it only for incomparable colliding revisions that are all ancestors of a multi-parent merge revision.
- Test the guard with unresolved, chained, and explicitly merged fixtures, and exercise upgrade convergence from each September branch.

## Risks / Trade-offs

- The existing retirement migration drops columns expected by older application images. The deploy must stop the old container before migration; if the new image fails after migration, restore the pre-migration database backup before returning to the old image.
