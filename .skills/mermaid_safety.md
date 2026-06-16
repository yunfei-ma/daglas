# SKILL: mermaid_safety — Avoid Common Mermaid Parse Errors

## Purpose

This skill is a pre-flight checklist. Read it before writing ANY Mermaid diagram. The errors listed here are silent or cryptic at runtime — the parser fails with a positional error and no clear explanation. Avoid them by construction.

---

## 1. Edge labels — forbidden characters

Mermaid parses `|label|` on arrows character by character. These characters have structural meaning inside labels and will break the parser:

| Character | Problem | Safe alternative |
|-----------|---------|-----------------|
| `[` `]`   | Node shape syntax | Use plain words: `articles` not `Article[]` |
| `{` `}`   | Subgraph / hexagon syntax | Use plain words: `config map` not `Config{}` |
| `(` `)`   | Rounded node / circle syntax | Use plain words: `event` not `event()` |
| `<` `>`   | Arrow or HTML tag | Use plain words: `response` not `<Response>` |
| `"`       | String delimiter | Wrap the whole label in quotes if needed |
| `#`       | Hex color / comment ambiguity | Avoid in labels entirely |

**Rule:** Edge labels must be plain words and spaces only. If you need type notation, use English plurals or wrap the entire label in double quotes.

```
Fetcher -->|Article[]| Pool        ✗  parse error on ]
Fetcher -->|articles| Pool         ✓
Fetcher -->|"Article[]"| Pool      ✓  quotes escape special chars
```

---

## 2. Node IDs vs node labels

Node IDs (the bare word before the shape brackets) must be alphanumeric with no spaces or special characters. The display label inside the shape brackets can be a sentence.

```
fetch_context[fetch context]       ✓  ID: fetch_context, label: fetch context
fetch context[fetch context]       ✗  space in ID breaks parser
svt.se{{svt.se}}                   ✗  dot in ID causes issues
Site{{svt.se / dn.se}}             ✓  dot is safe inside label, not in ID
```

**Rule:** IDs use `snake_case` or `CamelCase`. Put human-readable text inside the shape `[]` `()` `{{}}` etc.

---

## 3. Colons and semicolons in labels

A bare colon inside a node label or edge label can be misread as a link definition.

```
A[Config: host, port]              ✗  colon may confuse parser
A["Config: host, port"]            ✓  quotes make it safe
A[Config host and port]            ✓  rephrase without colon
```

---

## 4. `classDef` must come before node declarations

In `graph` diagrams, `classDef` lines must appear before any node or edge that references them. Placing them after causes silent failures where colors are not applied.

```
graph LR
    classDef core fill:#E1F5EE,stroke:#0F6E56,color:#085041   ✓ defined first
    A[Module]:::core
    B[Module]:::core
    A --> B
```

```
graph LR
    A[Module]:::core      ✗ classDef not defined yet
    classDef core fill:#E1F5EE,stroke:#0F6E56,color:#085041
```

---

## 5. `:::` class assignment placement

Apply `:::className` directly on the node declaration line, not on a separate line and not on an edge.

```
A[Module]:::core               ✓
A:::core                       ✓  (if A was declared earlier without a label)
A --> B:::core                 ✗  can misapply class to edge, not node
```

---

## 6. Subgraph names must not clash with node IDs

If you use `subgraph`, its identifier must be unique and not reuse any node ID.

```
subgraph core
    A[ModuleA]
end
graph LR
    core --> B         ✗  'core' is ambiguous — subgraph or node?
```

---

## 7. `sequenceDiagram` — participant aliases

Always declare participants explicitly with `participant X as Label` before using them. Mermaid infers undeclared participants, but order is unpredictable and styling won't apply.

```
sequenceDiagram
    participant S as Scheduler
    participant G as Generator
    S->>G: generate(ctx)          ✓

    S->>Generator: generate(ctx)  ✗  mixes alias and full name
```

---

## 8. `%%{init}%%` block must be the first line

The theme init block must appear before `sequenceDiagram` or `graph`. Anything before it (even a blank line) causes it to be ignored silently.

```
%%{init: {"theme": "base", "themeVariables": {...}}}%%
sequenceDiagram
    ...                            ✓

sequenceDiagram
%%{init: ...}%%                    ✗  ignored — too late
    ...
```

---

## 9. Arrow types must match diagram type

Using the wrong arrow syntax for the diagram type produces a parse error with no clear message.

| Diagram type    | Correct arrows              | Wrong                  |
|-----------------|-----------------------------|------------------------|
| `graph`         | `-->` `---` `-.->` `==>`   | `->>` `-->>` (sequence)|
| `sequenceDiagram`| `->>` `-->>`               | `-->` `==>`  (graph)  |
| `classDiagram`  | `<|--` `-->` `*--` `o--`   | `->>`                  |

---

## 10. Never define nodes inline on edge lines

Declaring a node with its shape directly in an edge line causes parse errors,
especially for stadium `((Label))` or hexagon `{{Label}}` shapes when the label
contains spaces.

```
Smtp -->|SMTP| ((SMTP Server))     ✗  inline stadium with space = parse error
Smtp -->|SMTP| MySmtp              ✓  reference pre-declared node ID
MySmtp((SMTP Server))              ✓  declare node separately on its own line
```

**Rule:** Always declare every node on its own line before referencing it in an
edge. Never use shape syntax inline on an arrow target or source.

---

## 11. Pre-write checklist

Before outputting any Mermaid block, verify:

- [ ] All edge labels contain only plain words, spaces, and hyphens — no `[]{}()<>#`
- [ ] All node IDs are `snake_case` or `CamelCase` — no spaces, dots, or slashes
- [ ] `classDef` lines appear before any node declaration
- [ ] `:::className` is on the node declaration line, not on edges
- [ ] `%%{init}%%` is the absolute first line for sequence diagrams
- [ ] Arrow syntax matches the diagram type (`-->` for graph, `->>` for sequence)
- [ ] No colon inside node or edge labels unless the whole label is in double quotes
- [ ] Participant aliases in sequence diagrams are declared before first use
- [ ] Subgraph IDs do not reuse node IDs

---

## 12. Supported diagram types — reference

| Type | Keyword | Use for |
|------|---------|---------|
| Flowchart | `graph` | System topology, data flow, use-case-like actor diagrams |
| Sequence | `sequenceDiagram` | Call order over time |
| Class | `classDiagram` | Object structure, interfaces, inheritance |
| State | `stateDiagram-v2` | State machines, lifecycles |
| Architecture | `architecture-beta` | Service topology, infrastructure, groups |

**Not supported** (do not use): `usecaseDiagram`, `flowchart-v2`,
`requirementDiagram` — these either do not exist in Mermaid or are not
rendered by the available renderer. Use `graph` instead for any actor /
use-case style diagram (actors use `((actor))` double-circle shape).
