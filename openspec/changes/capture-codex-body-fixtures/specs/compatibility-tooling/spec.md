## MODIFIED Requirements

### Requirement: Traffic captures fail safe for credentials and body content

The capture addon MUST replace authorization, API-key, cookie, and proxy
credential header values in every capture mode. Metadata-only body capture MUST
be the default and MUST replace sensitive prompt, generated text, tool argument,
tool output, and encrypted-content strings with deterministic digest-and-length
metadata while retaining protocol structure. Raw body capture MUST require an
explicit full mode, and generated capture and report artifacts MUST be excluded
from version control, with exactly one exception: a sanitised request body MAY
be committed under `tests/fixtures/codex_bodies/` when it carries a recorded
provenance entry and the fixture privacy scan passes over the whole corpus.
Raw captures, header sidecars and run manifests MUST NOT be committed even when
their credential values were disposable.

#### Scenario: Default capture observes an authenticated request

- **WHEN** metadata mode captures a request with bearer credentials and prompt
  text
- **THEN** the credential value is absent from the record
- **AND** the raw prompt is absent
- **AND** stable digest-and-length metadata remains available for same-run
  equality comparison

#### Scenario: Operator explicitly requests full bodies

- **WHEN** the addon is configured for full body capture
- **THEN** request and response bodies are retained for deep investigation
- **AND** credential headers remain redacted

#### Scenario: A sanitised fixture body is committed

- **GIVEN** a captured request body that has been sanitised
- **AND** a provenance entry recording its origin, model slug, transport,
  client version, catalog digest and sanitisation list
- **WHEN** the fixture privacy scan runs over the corpus in strict mode, with no
  arguments beyond the corpus root, because the bodies permitted to keep bare
  client telemetry key names are read from their own provenance entries
- **THEN** the scan passes and the body may be committed
- **AND** the raw capture, its header sidecar and its run manifest remain
  excluded from version control

#### Scenario: A fixture body carrying an operator identifier is rejected

- **GIVEN** a candidate fixture body under `tests/fixtures/codex_bodies/`
- **WHEN** it contains a credential shape, a non-placeholder UUID, an operator
  home or workspace path, an email address, the scanning host's own identity,
  or a Codex telemetry key the fixture has not declared it carries
- **THEN** the fixture privacy scan fails and names the finding kinds
- **AND** the report does not echo the offending value

## ADDED Requirements

### Requirement: Codex request bodies are captured without credentials or upstream contact

The repository MUST provide a single command that captures a real Codex
Responses request body without ChatGPT credentials, without contacting any
upstream service, and without consuming subscription quota. The command MUST
serve the Codex client from a loopback origin that answers model discovery from
an operator-supplied catalog file and answers Responses with a deterministic
lifecycle, and MUST persist the decoded request bytes together with a header
sidecar and a run manifest recording the client version, the requested and the
observed transport, the catalog digest and a SHA-256 attestation per artifact.
The origin MUST serve every transport the client may choose from the provider
configuration the run generates, and the recorded transport MUST be the one the
body arrived on rather than the one the run asked for. Where a transport primes
the request context before sending the turn, the captured body MUST be the frame
that carries the transcript. Isolation MUST be
enforced by an unprivileged network namespace whose only interface is loopback,
so that external egress is impossible rather than unconfigured; disabling the
namespace MUST require a second, explicit acknowledgement flag. The command
MUST refuse, before starting any subprocess or server, an output directory
inside the repository, under a temporary filesystem, or reached through a
symlink; an exported Codex home directory holding stored credentials, which MUST
be refused rather than silently overwritten by the throwaway home the run
creates; an environment carrying proxy or upstream configuration, including any
outbound proxy variable in either spelling, because the Codex client routes even
a loopback request through it; a non-loopback origin address; and a
repository configuration or environment file supplied as the model catalog. The
model catalog MUST be pinned to a file and its digest recorded, because the
Codex model manager refetches discovery whenever the client version differs
from its cache; the client version MUST be recorded as the primary provenance
key, because the client layers bundled model overrides on top of the served
catalog and the catalog therefore does not determine the body.

#### Scenario: A capture run produces a body with no upstream contact

- **GIVEN** a pinned model catalog file and an installed Codex client
- **WHEN** the capture command runs for a model slug
- **THEN** the request body is persisted verbatim after content-encoding
  decoding
- **AND** the run manifest records the client version, transport, catalog
  digest and per-artifact digests
- **AND** no request leaves the loopback interface

#### Scenario: A websocket run captures the turn and not the context prewarm

- **GIVEN** a provider configuration that offers websockets and a client that
  chooses them
- **WHEN** the client primes the request context and then sends the turn
- **THEN** the captured body is the frame carrying the transcript
- **AND** the priming frame is retained under its own artifact name, because on
  the Responses-Lite lane it is where the additional-tools bundle travels
- **AND** the manifest records the transport the body arrived on, so a run that
  fell back to HTTP is not reported as a websocket capture

#### Scenario: The capture refuses an exported credentialed home

- **GIVEN** an exported Codex home variable naming a directory that holds
  stored credentials
- **WHEN** the capture command runs
- **THEN** the command refuses before creating the capture directory, the
  throwaway home, the origin or the client
- **AND** no capture artifact is written

#### Scenario: The capture refuses an unsafe output location or catalog

- **WHEN** the output directory resolves inside the repository, under a
  temporary filesystem, or through a symlink, or the supplied catalog is a
  repository configuration or environment file
- **THEN** the command refuses before starting any subprocess
- **AND** the refusal names the rejected path and the accepted alternatives

#### Scenario: The capture refuses a polluted environment

- **WHEN** the invoking environment carries a proxy or upstream configuration
  variable
- **THEN** the command refuses and names every offending variable
- **AND** the child process it would have started never inherits one

### Requirement: Captured bodies are sanitised by a positive allowlist before commit

The sanitiser MUST fail closed on any top-level request field outside its
reviewed allowlist rather than forwarding it. It MUST remove at least the Codex
telemetry fields the proxy strips from a source-routed body, MUST remove the
websocket frame envelope from a websocket capture — which is persisted verbatim
because on that transport the frame is the request body — MUST remove the
same stream-option keys the proxy removes and MUST drop that object only when
the removal empties it, and MUST replace the prompt cache key, every input-item
identifier and every nested identifier with fixed placeholders drawn from a
recognisable placeholder family. It MUST rewrite the operator-identifying
strings inside message content — workspace and home paths, the capture date,
the timezone, the shell, the agent-instructions file heading, and the skill
roots table together with the skill inventory. It MUST NOT add a field that the
captured body did not carry, because the portability view declines unknown
top-level fields and a fabricated key would change the recorded verdict. It
MUST leave the standard tool array and the Responses-Lite additional-tools
bundle byte-identical, MUST be idempotent, and MUST report what it changed
without echoing any removed value.

#### Scenario: An unreviewed top-level field stops the sanitiser

- **WHEN** a captured body carries a top-level field the sanitiser does not
  recognise
- **THEN** sanitisation fails and names the field
- **AND** no fixture is written

#### Scenario: Absence is preserved

- **GIVEN** a captured Responses-Lite body with no instructions, no tool array
  and no stream options
- **WHEN** it is sanitised
- **THEN** none of those fields is present in the output

#### Scenario: Tool declarations survive byte for byte

- **GIVEN** a captured body whose tool declarations carry the fields that
  decide its portability verdict
- **WHEN** it is sanitised
- **THEN** the tool array and any additional-tools bundle are unchanged
- **AND** re-running the sanitiser produces an identical result
