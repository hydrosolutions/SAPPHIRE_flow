# Claude Agent Guidelines

## Project Overview

SAPPHIRE Flow is an operational hydrological forecasting system that ingests weather and station data, runs ensemble forecast models, checks flood thresholds, and serves results via a REST API with an optional review dashboard. Currently in **design phase** (no implementation yet — only design docs in `docs/design/` and scaffolding in `src/`). The immediate goal is **v0**: a working end-to-end pipeline using Swiss public data (MeteoSwiss weather, BAFU stations) with simple models, validating the architecture before Nepal deployment in Oct 2026. See `docs/planning-steps.md` for the planning-to-implementation roadmap and `docs/design/00-overview.md` for the full design overview.

## Workflow and Review

See `docs/workflow.md` for the full workflow conventions. Key points:

- **Status lifecycle**: `planned → in-progress → review → done → archived`
- **Every code change updates affected docs** — no exceptions.
- **Run `/review` on plans before implementation** — dispatches `plan-reviewer` (adversarial review) alongside relevant specialist agents.
- **Run `/review` on design docs** — dispatches `design-reviewer` (data flow tracing, interface completeness, junior dev implementability) alongside specialist agents.
- **Run `/review` before committing** — dispatches specialist agents (domain, security, developer, testing, data-eng, ops, CI/CD, docs) in parallel.
- **Blocking findings must be resolved** before transitioning to `done`.
- **`review-docs` always runs** — documentation hygiene is mandatory.

Agents live in `.claude/agents/`. Commands: `/plan`, `/review`.

## Task Management Principles

### Avoid Task Jags

**Critical**: Avoid task jags at all cost. Jags are semantic changes in task direction:

- Going from implementing A to testing A
- Switching from implementing A to implementing B
- Any mid-stream change in the core task focus

Stay focused on the current task until completion. Delegate tasks to sub agents aggressively (3+ agents at a time), and remain at a higher level of abstraction and coordination, resisting the temptation of jumping in yourself for quick fixes.

### Delegation Strategy

**Always delegate orthogonal tasks to sub-agents**. Use the most appropriate agent from `agents/` for each task:

- Break down complex work into focused sub-tasks
- Route each sub-task to the specialist agent best suited for it
- Maintain clear task boundaries between agents
- Give agents all the context they need, and instruction on where to gather more context if needed.

### Plan and Design Maturity Gate (hard rule)

**NEVER declare a plan or design doc "ready" prematurely.** Both must survive independent adversarial review before downstream work begins. A plan that a junior developer cannot implement without asking questions is not ready. A design doc that a junior developer cannot read and understand the full data flow is not ready.

**Rules (apply to both plans and design docs):**

1. **Nothing is ready after one iteration.** After drafting, assume it has major gaps.
2. **Run `/review` on every plan and every design doc change** before suggesting readiness. Round count is tracked via a `## Review History` section appended to the document.
3. **A document is ready ONLY when** all of these are true:
   - The document has `status: DRAFT` frontmatter (all new plans and design docs start as DRAFT)
   - At least **2 review-fix cycles** completed (tracked in the document's Review History)
   - The most recent round surfaces **zero blocking findings**
   - The **Junior Dev Readiness Checklist** passes (see below)
   - The `## Open Questions` section has no unchecked items (design docs)
   - The user has explicitly confirmed readiness by replying `confirm ready` (no other phrase counts)
   - Only after user confirmation: change frontmatter to `status: READY` and remove the DRAFT banner
4. **After each review round, report the status honestly**: findings by severity, what was fixed, what remains.
5. **Never say** "this plan looks ready" or "we can start implementing" unless criterion 3 above is fully met.

### Readiness Declaration Protocol (mandatory sequence)

Before suggesting that ANY plan or design doc might be ready, Claude MUST mechanically execute this sequence. Skipping steps is a hard violation.

1. **Check DRAFT status**: Read the document's frontmatter. If `status: READY` already, someone skipped the protocol — flag this as suspicious and re-verify. If `status: DRAFT`, proceed.
2. **Count review rounds**: Read the document's `## Review History` table. If fewer than 2 rows exist, STOP — the document is not ready. State: "Only N review cycle(s) completed, minimum 2 required."
3. **Check latest round**: Read the most recent row in the Review History table. If Blocking > 0, STOP — the document is not ready. State: "N blocking finding(s) remain from round M."
4. **Run the Junior Dev Readiness Checklist**: For every task (plans) or component (design docs), verify each item. List any failures. If any item fails, STOP — the document is not ready.
5. **Report status to user**: Present the checklist results honestly. Do NOT suggest readiness — instead say: "All mechanical checks pass. Awaiting your confirmation before marking as ready."
6. **Wait for explicit user confirmation**: Only after the user says the document is ready (e.g., "looks good", "mark it ready", "let's implement") may you change the frontmatter from `status: DRAFT` to `status: READY` and remove the DRAFT banner.

**Implementation gate**: Any agent or conversation that encounters a plan with `status: DRAFT` must refuse to implement it. See the Implementation Gate in `docs/workflow.md`.

**Anti-patterns — NEVER do these:**

- "This plan looks solid, we can start implementing." (Skips the protocol entirely.)
- "After fixing these issues, the plan should be ready." (Predicts future readiness — you don't know what the next review will find.)
- "I've addressed all the findings, so the plan is now ready." (Fixing findings ≠ passing review. You must re-run `/review` and it must come back clean.)
- "This is a minor issue, the plan is essentially ready." (No such thing. Either zero blocking findings or not ready.)
- Declaring readiness without showing the Review History table status.
- Running only 1 review cycle and suggesting readiness because findings were minor.
- Starting implementation from a plan that has `status: DRAFT` frontmatter.
- Removing the DRAFT banner or changing `status: READY` without explicit user confirmation.

**What to say instead:**

- After a first draft: "Draft complete. This needs at least 2 review-fix cycles before it can be considered ready. Running `/review` now."
- After fixing findings: "Findings from round N addressed. Running `/review` again — round N+1."
- After a clean review: "Round N complete with zero blocking findings. Review History shows N rounds completed. All Junior Dev Readiness checklist items pass. Document is still DRAFT. Awaiting your confirmation to mark READY."

### Junior Dev Readiness Checklist (hard rule)

Every task in a plan and every component in a design doc must satisfy **all** of these. If any item is missing, the document is **not ready** — this is a blocking finding.

**Per task (plans):**

1. **Inputs fully specified**: Exact types, where they come from (which module, function, store, or external source), and how they are obtained (function call, query, API request).
2. **Outputs fully specified**: Exact return types and where they go (who consumes them, how they are stored or passed downstream).
3. **Data flow trace**: For any task that moves or transforms data, the full path is spelled out: source → parse → transform → store/return, with the **type at each step**. No gaps, no "and then it gets processed."
4. **Error cases enumerated**: Every error that can occur is listed with the expected behavior (raise, log-and-skip, retry with backoff — and if retry, how many times, what backoff, what happens after exhaustion).
5. **File paths**: Every file to create or modify is listed with its full path relative to project root.
6. **Import dependencies**: Which modules and Protocols the task depends on, so the developer knows what to import without searching.
7. **Mechanical verification**: A copy-pasteable command that passes/fails. The command must verify the actual deliverable, not a proxy:
   - Code tasks: `uv run pytest tests/test_<module>.py` and/or `uv run pyright --strict src/sapphire_flow/<module>.py`
   - Schema/migration tasks: migration up+down against test DB (e.g., `uv run alembic upgrade head && uv run alembic downgrade -1`)
   - Config/infra tasks: a validation script or config-check command
   - A `pyright` command alone does NOT count as verification for non-code deliverables.
8. **No implicit decisions**: If two reasonable implementations exist, the plan picks one and explains why. A developer should never have to guess.

**Per component (design docs):**

1. **Data flow is end-to-end**: From external source to final consumer, every hop is documented with types.
2. **Interfaces are exact**: Protocol methods have full signatures (parameters, return types, exceptions).
3. **Boundary behavior is explicit**: What happens at every system boundary — parsing, validation, error handling, retries.
4. **Configuration is specified**: What is configurable, what the defaults are, where config comes from.
5. **Concurrency and ordering**: If multiple things can happen in parallel or order matters, it is stated explicitly.
6. **Cross-references are consistent**: Every type, Protocol, and field name matches `docs/spec/types-and-protocols.md` and other design docs.
7. **No implicit decisions**: Where two reasonable designs exist, the doc picks one and explains why.
8. **Open Questions resolved**: The `## Open Questions` section is mandatory. Any unchecked item (`- [ ]`) is a blocking finding. Absence of the section is also blocking.

## Context Awareness

### Library Implementation Details

`.context` contains git submodules of libraries used. Agents are **highly encouraged** to grep for implementation details of the files they work with to ensure consistency with library conventions.

## Use Skills and Ask Questions

Use all skills that make semantic sense for the task.
Ask clarifying questions often to fill gaps. Better to clarify upfront than to implement the wrong solution.

---

## Python Package Management with `uv`

- **Use `uv` exclusively** for Python package management.
- Do **not** use `pip`, `pip-tools`, `poetry`, or `conda` directly.
- Commands:
  - Install: `uv add <package>`
  - Remove: `uv remove <package>`
  - Sync lockfile: `uv sync`
- Running:
  - Python scripts: `uv run <script>.py`
  - Tools: `uv run pytest`, `uv run ruff`
  - REPL: `uv run python`

---

## Ad-hoc Analyses and One-Time Scripts

- **Use shell heredoc syntax** for one-time data analyses and exploratory work.
- Do **not** create throwaway `.py` files or use alternative shell tools (awk, sed, etc.) for data manipulation.
- Python is more readable, maintainable, and powerful for these tasks.

**Preferred pattern:**

```bash
uv run python3 << 'EOF'
import pandas as pd

# Your analysis code here
df = pd.read_csv('data.csv')
print(df.describe())
EOF
```

**Why this matters:**

- **No file clutter** — no orphaned `temp.py` or `test_script.py` files
- **Self-documenting** — the command and its context live together in shell history or docs
- **Efficient** — Claude can generate complete, working analyses inline
- **Reproducible** — easy to copy-paste entire commands

This approach is **mandatory** for:

- Quick data inspections
- One-time transformations
- Exploratory analyses
- Data quality checks

For **reusable** logic that runs regularly, create proper Python scripts or modules.

---

## Python Coding Style

### Type Hints (mandatory)

- Always annotate function parameters and return types.
- Use built-in generics (`list`, `dict`, `tuple`, `set`) — **never** import `List`, `Dict`, etc. from `typing`.
- Use `|` for unions (Python 3.10+).
- Annotate variables where type is not obvious.

```python
def process_data(items: list[str]) -> dict[str, int]:
    ...

value: str | None = None
```

### Error Handling

- **Never** use bare `except`.
- Always raise meaningful errors with context.
- Prefer explicit error classes over generic `Exception`.

### Logging

- Use `logging` — never `print` — for runtime diagnostics.

### Formatting & Linting

- Use `ruff` for both linting and formatting:
  - Format: `uv run ruff format`
  - Lint + fix: `uv run ruff check --fix`

### Version Bumping (mandatory)

**Every commit MUST include a patch version bump.** No exceptions.

Before committing, follow this exact sequence:

1. `uv run bump-my-version bump patch` — modifies `pyproject.toml` and `src/mypackage/__init__.py`
2. Stage version files alongside code changes
3. Commit with a conventional commit message
4. `git tag v$(uv run bump-my-version show current_version)` — tag the commit

**Rules:**
- **Patch bumps**: Automatic with every commit. Claude MUST do this.
- **Minor/major bumps**: Only when the user explicitly requests. Use `uv run bump-my-version bump minor` or `major`.
- **Never let bump-my-version create its own commit** — config has `commit = false`. Fold version changes into the real commit.
- **Always tag** after every commit.

---

## Type Driven Development

**Principle:** Encode domain invariants in the type system as far as Python allows. Invalid states should be unrepresentable. Types are the first line of documentation and the first line of defense — they steer both humans and LLM agents toward correct code by making wrong code fail static analysis.

Python's type system is not enforced at compile time like Rust's, but with `mypy`/`pyright` in strict mode and runtime validation at boundaries, you can get surprisingly close.

### Parse, don't validate (hard rule)

Raw input (strings, numbers from files/CLI/APIs) is converted into typed domain representations **at the system boundary**. Internal functions never accept raw primitives when a domain type exists.

```python
# WRONG — raw primitives leak into domain logic
def delineate(comid: int, lat: float, lon: float) -> dict: ...

# RIGHT — parsed at the boundary, domain types from here on
def delineate(comid: ComId, pour_point: GeoCoord) -> Watershed: ...
```

Parsing happens once, at the edge. Everything downstream receives types that are **valid by construction**. Use `__new__` to enforce invariants at creation time.

### NewType and wrapper types

Use `NewType` for lightweight semantic distinction and `NamedTuple` for structured domain values:

```python
from typing import NewType, NamedTuple

# Lightweight — zero runtime cost, caught by static analysis
UserId = NewType("UserId", int)
Meters = NewType("Meters", float)

# Plain value type — no invariants to enforce
class GridCoord(NamedTuple):
    col: int
    row: int

# Validated domain type — invariants enforced via __new__
class GeoCoord(NamedTuple):
    lon: float
    lat: float

    def __new__(cls, lon: float, lat: float) -> "GeoCoord":
        if not (-180 <= lon <= 180):
            raise ValueError(f"longitude {lon} out of range")
        if not (-90 <= lat <= 90):
            raise ValueError(f"latitude {lat} out of range")
        return super().__new__(cls, lon, lat)
```

A function accepting `GeoCoord` cannot be confused with one accepting `GridCoord`. `NewType` catches `UserId`/`int` swaps in type checkers without runtime overhead. `__new__` ensures no invalid `GeoCoord` can ever exist — unlike a factory classmethod, there is no escape hatch.

**When to wrap:**
- Two parameters of the same primitive type could be swapped (IDs, coordinates, thresholds)
- A value has domain invariants (ranges, formats, non-empty)
- Semantic meaning is not obvious from the primitive type alone

**When bare primitives are fine:**
- Unambiguous locals (loop counters, intermediate arithmetic)
- Single-use values with obvious meaning from context

### Enums over booleans

Never use `bool` to represent a domain state with two named possibilities. Use `enum.Enum`.

```python
from enum import Enum, auto

# WRONG
def trace(upstream: bool) -> list[Node]: ...

# RIGHT
class TraceDirection(Enum):
    UPSTREAM = auto()
    DOWNSTREAM = auto()

def trace(direction: TraceDirection) -> list[Node]: ...
```

This applies to function parameters, NamedTuple fields, and return values. A `bool` says nothing about intent; an enum is self-documenting and extensible.

### Literal types for constrained strings

When a parameter accepts a fixed set of string values, use `Literal` instead of `str`:

```python
from typing import Literal

# WRONG
def resample(method: str) -> Raster: ...

# RIGHT
def resample(method: Literal["nearest", "bilinear", "cubic"]) -> Raster: ...
```

### Protocols for structural typing

Prefer `Protocol` over inheritance for defining interfaces. This gives you structural ("duck") typing that is still checkable by static analysis:

```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class Readable(Protocol):
    def read(self, n: int = -1) -> bytes: ...

def process(source: Readable) -> Result:
    data = source.read()
    ...
```

Any object with a matching `.read()` method satisfies this — no inheritance required.

### NamedTuple as the default value type

`NamedTuple` is the default for domain value types. It gives you immutability, hashing, unpacking, and low overhead for free — no decorators or dependencies needed.

```python
class TimeRange(NamedTuple):
    start: datetime
    end: datetime

    def __new__(cls, start: datetime, end: datetime) -> "TimeRange":
        if start >= end:
            raise ValueError(f"start {start} must precede end {end}")
        return super().__new__(cls, start, end)

# Immutable, hashable, unpackable
start, end = TimeRange(t0, t1)
```

**When to use each type tool:**

| Need | Tool |
|---|---|
| Semantic distinction on a primitive | `NewType` |
| Structured value, no invariants | Plain `NamedTuple` |
| Structured value with invariants | `NamedTuple` + `__new__` validation |
| Fixed set of string options | `Literal` |
| Domain state with named possibilities | `Enum` |
| Interface / capability contract | `Protocol` |
| External data parsing / validation | `pydantic.BaseModel` |

### Summary of rules

| Rule | Strictness |
|---|---|
| Parse, don't validate | **Hard rule** — no raw primitives past the boundary |
| NewType / wrapper types | Wrap where confusion is plausible; bare primitives OK for unambiguous locals |
| Enums over booleans | **Always** — no `bool` for domain states |
| Literal over raw strings | **Always** — when the set of valid values is fixed and known |
| Protocols over inheritance | **Prefer** — use inheritance only for shared implementation |
| NamedTuple for value types | **Default** — the go-to for all domain value types |
| Pydantic at boundaries only | **Hard rule** — never in domain logic |

### Pydantic for boundary validation

Pydantic is used **exclusively at system boundaries** — API requests/responses, JSONB schema validation, external data ingestion (MeteoSwiss, BAFU, config files). It is **never** used for internal domain types.

The flow is: **External data → Pydantic model (validate) → NamedTuple domain type (internal)**.

```python
from pydantic import BaseModel

# Boundary: parse external API response
class StationResponse(BaseModel):
    id: str
    name: str
    latitude: float
    longitude: float

# Convert to domain type at the boundary
def parse_station(raw: dict) -> Station:
    resp = StationResponse.model_validate(raw)
    return Station(
        id=StationId(resp.id),
        name=resp.name,
        location=GeoCoord(lon=resp.longitude, lat=resp.latitude),
    )
```

**Use Pydantic for:**
- API request/response schemas (FastAPI integration)
- JSONB field validation (DB storage)
- External data source parsing (weather APIs, station feeds, config files)

**Do NOT use Pydantic for:**
- Internal domain value types (use `NamedTuple`)
- Function signatures in domain logic
- Anything that doesn't cross a system boundary

---

## Code Quality Standards

### High Signal-to-Noise Ratio

Strive for high signal-to-noise ratio in code:

- Clear, purposeful implementations
- Direct, readable solutions
- Declarative over imperative styles

### Structural Preferences

- **Avoid nested loops**: Prefer flat, pipeline-style code
- **Avoid deep nesting**: Keep nesting shallow (max 2-3 levels)
- **Prefer comprehensions and generators**: Use list/dict comprehensions for transformations
- **Use dataclasses and Protocols**: Leverage structural typing for clean interfaces

### Example Pipeline Style

```python
from functools import reduce

result = reduce(
    combine,
    filter(predicate, map(transform, data))
)

# Or with comprehensions
result = [transform(x) for x in data if predicate(x)]
```

### Example Pattern Matching (Python 3.10+)

```python
match command:
    case {"action": "create", "name": name}:
        return create_resource(name)
    case {"action": "delete", "id": id}:
        return delete_resource(id)
    case _:
        raise ValueError(f"Unknown command: {command}")
```

---

## Testability Requirements

### Control Side Effects via Dependency Injection

**CRITICAL**: Never use `datetime.now()` or `random.random()` directly in business logic. Always inject dependencies:

```python
# WRONG - untestable
def create_record():
    return {"created_at": datetime.now(), "id": random.randint(1, 1000)}

# CORRECT - testable
def create_record(clock: Callable[[], datetime], rng: random.Random) -> dict:
    return {"created_at": clock(), "id": rng.randint(1, 1000)}
```

**Why this matters**: Direct calls to `datetime.now()` and `random` are impure and non-deterministic, making tests flaky. Dependency injection allows:

- Controlled time in tests via fake clocks
- Deterministic random values via seeded RNGs
- Proper composition and testing

---

## Testing Philosophy

Good tests do not just check code; they shape its design. Tests are **contracts**: they describe what must stay true even if the implementation changes.

### Golden Rules

1. **Test behavior, not implementation**
   - Assert on outputs and public APIs.
   - Do not inspect private attributes like `_steps` unless no public API exists. If needed, add a public `.spec()` for testability.

2. **Each test should fail for one reason**
   - Keep assertions focused. Split broad tests into smaller ones.

3. **Prefer fast, deterministic tests**
   - No `sleep()`; control time with libraries like `freezegun` or dependency injection.
   - Control randomness by seeding or injecting RNGs.

4. **Use fakes over mocks**
   - Fake implementations are easier to read and maintain than heavy mocking.
   - Mock only at external boundaries (HTTP, file I/O, external services).

5. **Structure tests for readability**
   - Setup (Arrange) -> Action -> Assertion.
   - Use fixtures for repeated setup, but don't hide complexity in `conftest.py`.

### Test Coverage

- Use `pytest-cov` to measure coverage.
- Run with coverage: `uv run pytest --cov=src/<package> --cov-report=term-missing tests/`
- Coverage should be **used to find gaps**, not chased to 100%. A brittle 100% is worse than 85% meaningful coverage.

### Testing Conventions

#### File & Class Organization

- **One test file per module**: `test_<module>.py`
- **One test class per function/class under test**: `Test<ThingUnderTest>`
- Test methods: descriptive, snake_case, explain the behavior.
  Example: `test_fails_with_empty_dataframe`, not `test1`.

#### Categories of Tests

1. **Basic functionality**: happy paths with simple inputs.
2. **Error handling**: invalid inputs should raise the right exception with the right message.
3. **Edge cases**: empty data, null values, large inputs, unexpected types.
4. **Data preservation**: non-transformed fields, schema, and order remain intact.
5. **Integration paths**: small number of tests where the real pipeline runs end-to-end.

#### Assert Patterns

- Prefer **direct comparisons** for clarity.
- For complex structures, use `.to_dict()` or `.spec()` for clarity.
- Check types explicitly when relevant.

#### Error Testing

Always assert both **exception type** and **message fragment**:

```python
with pytest.raises(ValueError, match="no steps"):
    builder.build()
```

#### Fixtures

- Use fixtures sparingly and descriptively (`simple_df`, `df_with_missing_values`).
- Avoid fixture over-engineering; clarity > DRY.

---

## Anti-Patterns (Avoid These)

- Asserting on private attributes (`._steps`, `._internal_state`).
- Overly specific error message checks (brittle wording).
- Giant integration tests covering all cases — push most variation down into unit tests.
- 100s of trivial tests (getter/setter, boilerplate) — test behaviors that matter.
- Hiding critical setup in nested fixtures.
- Using bare `except:` clauses.
- Importing deprecated typing generics (`List`, `Dict`, `Optional`).

**Tests should describe contracts, not internals.**
If your test breaks after a refactor that doesn't change behavior, the test was wrong.

---

## Documentation Standards

### Minimal Documentation During Prototyping

**CRITICAL**: Forego excessive docstrings unless specifically asked to.

- **NO lengthy docstrings** - They add significant context overhead
- **NO detailed comments** for self-explanatory code
- Focus on clean, self-documenting implementations
- Add documentation only when:
  - Explicitly requested by the user
  - Code is ready for production/publishing
  - Public API requires clarification

**Rationale**: During prototyping and development, verbose documentation significantly bloats context. Write clear, readable code first. Documentation can be added later when actually needed.
