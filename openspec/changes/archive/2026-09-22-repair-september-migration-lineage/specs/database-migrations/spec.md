## ADDED Requirements

### Requirement: Resolved timestamp collisions pass migration topology validation

The migration topology check MUST continue to reject revisions that share an enforced timestamp prefix when they remain on separate, unmerged branches or when they are ordered in one chain. It MUST accept incomparable revisions with the same prefix when a later explicit Alembic merge revision has all of them in its ancestry. The merge revision MUST leave each branch's migration operations and revision identifiers unchanged.

#### Scenario: Parallel same-slot revisions are explicitly merged

- **GIVEN** incomparable revisions share an enforced timestamp prefix
- **AND** a merge revision descends from every colliding revision
- **WHEN** migration topology validation runs
- **THEN** it MUST NOT fail solely because of that resolved timestamp collision
- **AND** it MUST still validate the complete graph for a single connected head

#### Scenario: Parallel same-slot revisions remain unmerged

- **GIVEN** incomparable revisions share an enforced timestamp prefix
- **AND** no merge revision descends from all of them
- **WHEN** migration topology validation runs
- **THEN** it MUST report the timestamp collision as an error

#### Scenario: Same-slot revisions are chained

- **GIVEN** revisions in one chain share an enforced timestamp prefix
- **WHEN** migration topology validation runs
- **THEN** it MUST report the timestamp collision as an error
