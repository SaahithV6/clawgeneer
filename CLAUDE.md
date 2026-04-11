# CLAUDE.md — ClawGeneer Project Intelligence

This file is the complete context document for AI assistants (Claude, Copilot, or any LLM) working on this codebase. Read this before touching any file. It contains every important architectural decision, design constraint, integration detail, and rationale from the founding conversation.

---

## What This Project Is

**ClawGeneer** is an engineering orchestration platform. It runs as a persistent daemon on a dedicated Ubuntu Server 24.04 bare-metal machine (16GB RAM). It connects open-source CAD, meshing, FEA, and CFD tools into a single AI-assisted pipeline controlled from a terminal.

The owner accesses the server via SSH from a laptop. GUI work (FreeCAD modelling) is done via VNC or NoMachine, streamed to the laptop. The server is not a VM — it is bare metal, dedicated hardware.

**ClawGeneer is not a simulation tool. It is a coordinator.** It never does engineering work itself. It tells the right tool to run at the right time with the right inputs, checks the outputs, and moves to the next stage.

---

## The Core Problem Being Solved

Commercial engineering software (ANSYS, SolidWorks, MATLAB, Aspen Plus) is:
- Expensive and licence-locked
- Non-scriptable end-to-end
- Not AI-integrated
- Siloed — no unified project format across tools

ClawGeneer replaces this stack with open-source equivalents, unified under a single Python orchestration layer, with an AI layer that can interpret natural language constraints, generate geometry, review results, and iterate toward optimal designs.

---

## The Three Tool Classes (Non-Negotiable)

The pipeline has exactly three classes of tools. This is fixed.

### Class 1: CAD
Produces geometry. Output is always `.STEP` (B-Rep, not mesh).

| Tool | When Used |
|---|---|
| **CadQuery** | AI-generated geometry from LLM-written Python code |
| **FreeCAD** | User models directly in GUI, or `.FCStd` files uploaded |
| **Upload adapter** | User uploads `.STEP`, `.IGES`, `.STL`, `.FCStd` from their laptop |

### Class 2: Mesh
Converts `.STEP` geometry to solver-ready mesh format.

| Tool | When Used |
|---|---|
| **Gmsh** (Python API) | Always — primary mesher for both FEA and CFD |
| **meshio** | Format conversion only: `.msh` → CalculiX `.inp` |
| **gmshToFoam** | Mesh conversion only: `.msh` → OpenFOAM `constant/polyMesh/` |

### Class 3: Solvers
Run sequentially. Never simultaneously. One solver uses full RAM at a time.

| Tool | Domain |
|---|---|
| **CalculiX (ccx)** | FEA: stress, displacement, strain, fatigue, thermal |
| **OpenFOAM** | CFD: flow, pressure, turbulence, heat transfer, drag |

---

## Sequential Job Execution — This Is Sacred

**Never run two solvers simultaneously.** The 16GB RAM machine must give each solver everything it has.

```
CAD → MESH → [FEA] → [CFD] → RESULTS
```

If both FEA and CFD are requested for one project, FEA runs first, finishes completely, memory is explicitly released (`del`, `gc.collect()`), then CFD starts.

---

## The Adapter Pattern — The Backbone

Every tool lives behind a `BaseAdapter` class. The pipeline only ever calls four methods:

```python
class BaseAdapter(ABC):
    def validate_inputs(self) -> bool
    def run(self) -> AdapterResult
    def parse_outputs(self) -> dict
    def check_installed(self) -> bool
```

`AdapterResult` is a dataclass:
```python
@dataclass
class AdapterResult:
    success: bool
    output_path: Path | None
    summary: dict
    logs: str
    error: str | None
```

**Adding a new tool** = one new class inheriting `BaseAdapter` + one entry in `tools/registry.yaml`. The pipeline runner does not change.

---

## The Project File — `project.yaml`

This is the single source of truth for every job. The pipeline reads it, every adapter reads it, the AI reads it, the optimizer writes to it. Nothing is passed between components as raw arguments — everything goes through this file.

Key sections:
- `geometry`: source (generate/upload/freecad_gui), tool, parameters
- `material`: engineering properties (E, nu, rho, yield strength)
- `mesh`: tool, element size, element order
- `jobs`: list of FEA and/or CFD jobs with boundary conditions
- `results`: populated by adapters after solving (initially null)
- `optimization`: enabled flag, objective, parameter bounds, constraints

The Pydantic model for this file lives in `clawgeneer/schema/project.py`. Always use the Pydantic model — never parse the YAML manually.

---

## The Surface Naming Problem — Most Critical Integration Issue

**This is the single biggest risk in the pipeline. Understand it completely.**

When Gmsh imports a STEP file, surfaces get arbitrary integer tags (1, 2, 7, 12...). But `project.yaml` boundary conditions reference surfaces by name (`bottom_face`, `inlet`, `wall`). There is no automatic mapping.

**Resolution strategy — two paths depending on geometry source:**

**Path A: AI-generated geometry (CadQuery)**
The LLM system prompt MUST instruct the model to tag faces in the CadQuery code using CadQuery's native tagging system. Example:
```python
result = (
    cq.Workplane("XY")
    .box(100, 60, 8)
    .faces(">Z").tag("top_face")
    .faces("<Z").tag("bottom_face")
)
```
When exported to STEP, these names are embedded in metadata. Gmsh reads them via `gmsh.model.getEntityName(dim, tag)`.

**Path B: Uploaded or GUI-modelled geometry**
Interactive labelling: ClawGeneer lists all detected surfaces with their bounding box, area, and normal vector. User (or AI) confirms which label maps to which surface. This confirmation is stored in `project.yaml` under `geometry.surface_map`.

**Never skip this step.** If surface names don't map correctly, boundary conditions are applied to the wrong faces and results are physically meaningless — no error is thrown.

---

## Gmsh Mesh Version — One Line, Critical

`gmshToFoam` (OpenFOAM's mesh importer) requires Gmsh format **v2.2**. The Gmsh Python API defaults to v4. This will silently corrupt the CFD mesh or fail with a cryptic error.

**Always set this in the Gmsh adapter:**
```python
gmsh.option.setNumber("Mesh.MshFileVersion", 2.2)
```

---

## OpenFOAM Patch Types — Silent Wrong Physics

`gmshToFoam` sets all boundary patches to type `patch`. But OpenFOAM requires:
- `wall` for no-slip surfaces (turbulence wall functions only apply to `wall` type)
- `symmetryPlane` for symmetry boundaries
- `patch` for inlets and outlets

If a wall is left as type `patch`, wall functions silently don't apply. Results look plausible but are physically wrong.

**The CFD adapter must post-process `constant/polyMesh/boundary`** after `gmshToFoam` runs, setting correct types based on `project.yaml` boundary condition definitions.

---

## Solver Divergence Detection — Exit Code Lies

Both CalculiX and OpenFOAM return exit code 0 even when they diverge. `subprocess.run()` success = True does not mean the physics converged.

**CalculiX**: After every run, scan `<jobname>.log` for:
```
"no convergence", "not achieved", "ERROR", "***"
```

**OpenFOAM**: After every run, parse `log.simpleFoam`:
- Extract final residuals for all fields (U, p, k, epsilon)
- Flag as diverged if any residual > 1e-2
- Hard failure if any residual contains `nan` or `inf`

Both must set `AdapterResult(success=False, error=...)` on divergence. The pipeline must handle this gracefully — not crash, but report to user and AI for diagnosis.

---

## LLM Code Validation Gate

LLM-generated CadQuery code must be validated BEFORE it is passed to Gmsh. A geometrically invalid solid will crash Gmsh with an unhelpful error.

Validation checks (in order):
```python
assert not result.val().isNull()     # LLM returned empty solid
assert result.val().isValid()        # OpenCASCADE geometry check
assert result.val().isSolid()        # Must be solid, not surface/wire
# Export to temp .STEP, re-import, check again — catches OCC export bugs
```
If any check fails: send the error back to the LLM with context and retry. Do not proceed to meshing.

---

## LLM Integration Points — Exactly Three

The LLM is used at exactly three points. Do not add LLM calls anywhere else without good reason.

### 1. CAD Generation (`ai/cad_gen.py`)
- **Input**: User prompt + `project.yaml` geometry section
- **Output**: Executable CadQuery Python code (JSON-wrapped)
- **Model**: GitHub Models API (interactive) or Ollama (optimization loops)
- **System prompt must include**: Face tagging instructions, unit conventions (mm), `make_part()` function signature, JSON output format

### 2. Constraint Interpretation (`ai/constraint_handler.py`)
- **Input**: Vague user description ("a hand-sized bracket for a small motor")
- **Output**: Populated `project.yaml` geometry parameters OR a list of clarifying questions
- **Logic**: If dimensions are underspecified, ask. If material is unspecified, default to mild steel. If load is unspecified, ask. Never guess critical safety-relevant values.

### 3. Result Review (`ai/result_review.py`)
- **Input**: `results/fea_summary.json` or `results/cfd_summary.json` + full `project.yaml`
- **Output**: Plain-English interpretation + specific suggested parameter changes with exact values
- **In optimization mode**: Returns updated `project.yaml` parameters directly for the next iteration

---

## LLM Client Architecture

```python
# ai/llm_client.py
# Two backends, same interface

class LLMClient:
    def __init__(self, mode="interactive"):
        if mode == "interactive":
            # GitHub Models API — free with GitHub PAT
            self.client = OpenAI(
                base_url="https://models.inference.ai.azure.com",
                api_key=os.environ["GITHUB_PAT"]
            )
            self.model = "gpt-4o"  # or whatever is in Copilot account
        elif mode == "optimization":
            # Ollama local — unlimited, no rate limits
            self.client = OpenAI(
                base_url="http://localhost:11434/v1",
                api_key="ollama"
            )
            self.model = "qwen2.5-coder:14b"
```

The model name for GitHub Models should be read from an environment variable (`CLAWGENEER_LLM_MODEL`) so the owner can update it as their Copilot account gains access to newer models (gpt-5-mini, gpt-5.1, etc.) without changing code.

---

## Three Modes of User Interaction

### Mode 1: Autonomous
User types a natural language prompt. ClawGeneer generates geometry, meshes, solves, reviews, and iterates. No further input required.

### Mode 2: Assisted
User uploads a `.STEP` file or syncs via `scp`/`rsync`. ClawGeneer accepts it via the upload adapter, meshes, solves, AI reviews and suggests changes. User approves or rejects changes interactively.

### Mode 3: Interactive GUI
User models in FreeCAD on the server via VNC or NoMachine. Saves to `~/projects/<name>/geometry/`. Runs `oc run <name>` from a separate SSH terminal. Can keep `oc chat <name>` open as a live engineering assistant during modelling.

All three modes use the same pipeline backend and the same `project.yaml` schema.

---

## Memory Budget (16GB RAM)

```
Interactive mode (GitHub Models handles LLM — offloaded):
├── OpenFOAM / CalculiX:  ~12–13 GB available
└── ClawGeneer daemon + OS: ~3–4 GB

Optimization mode (Ollama running locally):
├── qwen2.5-coder:14b:    ~10 GB
├── OpenFOAM / CalculiX:  ~4–5 GB (smaller meshes recommended)
└── ClawGeneer daemon + OS: ~1 GB
```

In optimization mode with Ollama, mesh sizes should be kept moderate. The AI reviews results and proposes parameter changes — it does not need to run simultaneously with the solver.

---

## Project Folder Structure

```
~/projects/<project-name>/
├── project.yaml          ← single source of truth
├── geometry/
│   └── part.step         ← output of CAD stage
├── mesh/
│   └── part.msh          ← output of mesh stage (Gmsh v2.2)
├── fea/
│   ├── model.inp         ← CalculiX input (meshio-generated)
│   ├── model.frd         ← CalculiX raw results
│   └── model.vtu         ← converted by ccx2paraview
├── cfd/
│   ├── 0/                ← OpenFOAM initial conditions
│   ├── constant/         ← mesh + physical properties
│   └── system/           ← solver controls
└── results/
    ├── fea_summary.json  ← max_stress, max_displacement, safety_factor
    ├── cfd_summary.json  ← max_pressure, avg_drag, wall_shear_stress
    ├── fea_stress.png    ← offscreen pyvista render
    └── cfd_pressure.png  ← offscreen pyvista render
```

---

## Tool Registry (`tools/registry.yaml`)

Every tool is declared here: its adapter class, install command, binary name, input/output formats. `tool_manager.py` reads this to check if tools are installed and to install missing ones. Never hardcode tool paths or install commands in adapter files.

---

## Assembly Support (Future Phase)

The `project.yaml` schema supports `type: assembly` from day one. Assembly projects have a `components` list, each with a file, material, position, and orientation. CadQuery supports assemblies natively — multiple components are positioned in space and exported as a single STEP assembly. This flows into Gmsh and the solvers identically to a single part.

Do not implement assembly-specific logic until Phase 8. But do not design schemas or data structures that would break when assembly support is added.

---

## What ClawGeneer Is NOT

- **Not a closed ecosystem**: Every tool is swappable. New tools are added by writing one adapter class.
- **Not a cloud service**: Everything runs on the owner's hardware. No data leaves the machine except LLM API calls.
- **Not a GUI application**: The primary interface is the `oc` CLI. GUI is for CAD modelling only, via existing tools (FreeCAD).
- **Not a replacement for engineering judgement**: The AI suggests. The user decides. Safety-critical values are never silently assumed.

---

## MCP Compatibility (Future)

The adapter interface is designed to be MCP (Model Context Protocol) compatible. Each adapter can be exposed as an MCP tool, allowing any MCP-compatible AI agent (GitHub Copilot, Claude, etc.) to call ClawGeneer's tools directly without going through the CLI. This is a future concern — do not over-engineer for it now, but do not make decisions that would prevent it.

---

## Build Order (Phased)

Build in this order. Do not skip phases.

1. `schema/project.py` — Pydantic model, validates `project.yaml`
2. `adapters/base.py` — `BaseAdapter` + `AdapterResult` contract
3. `tools/tool_manager.py` — install/check/invoke tools from registry
4. `adapters/mesh/gmsh_adapter.py` — easiest adapter, pure Python, test first
5. `adapters/fea/calculix_adapter.py` — meshio + subprocess
6. `adapters/cad/cadquery_adapter.py` — LLM call + code execution + validation gate
7. `ai/llm_client.py` — GitHub Models + Ollama wrapper
8. `ai/cad_gen.py` — prompt → CadQuery code
9. `adapters/cfd/openfoam_adapter.py` — case templating + gmshToFoam + solver
10. `adapters/results/pyvista_adapter.py` — .frd/.vtk → JSON + images
11. `pipeline/runner.py` — wire all adapters together
12. `cli/oc.py` — terminal interface
13. `ai/constraint_handler.py` — vague prompt → filled params
14. `ai/result_review.py` — results → suggestions
15. `tools/bootstrap.sh` — full server install script

---

## Key Dependencies

```
cadquery          # CAD geometry engine
gmsh              # Meshing (Python API)
meshio            # Mesh format conversion
PyFoam            # OpenFOAM Python automation
pyvista[all]      # Results visualisation (offscreen)
ccx2paraview      # CalculiX .frd → .vtk
openai            # LLM API client (GitHub Models + Ollama)
pydantic          # project.yaml schema validation
optuna            # Optimization loop
python-control    # Control systems (future)
```

---

## Environment Variables Required

```bash
GITHUB_PAT=<your_github_personal_access_token>
CLAWGENEER_LLM_MODEL=gpt-4o          # or gpt-5-mini, gpt-5.1, etc.
CLAWGENEER_LLM_MODE=interactive       # interactive | optimization
CLAWGENEER_PROJECTS_DIR=/home/engineer/projects
CLAWGENEER_INSTALL_DIR=/opt/clawgeneer
```

---

## Coding Conventions

- Python 3.11+
- All file I/O uses `pathlib.Path`, never `os.path`
- All config/schema uses Pydantic v2 models
- All subprocess calls use `subprocess.run(..., capture_output=True, text=True, timeout=N)`
- Always set a timeout on subprocess calls — solvers can hang
- Log parsing for convergence is mandatory after every solver run
- `gc.collect()` after every stage that loads large mesh data
- No hardcoded paths anywhere — all paths derived from `project.yaml` or env vars
- Type hints on all function signatures

