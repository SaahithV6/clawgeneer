# CLAUDE.md — ClawGeneer Project Intelligence

This file is the complete context document for AI assistants (Claude, Copilot, or any LLM) working on this codebase. Read this before touching any file. It contains every architectural decision, design constraint, integration detail, known pitfall, and rationale for this project.

---

## What This Project Is

**ClawGeneer** is an engineering orchestration platform. It runs as a persistent daemon on a dedicated Ubuntu Server 24.04 bare-metal machine (16 GB RAM). It connects open-source CAD, meshing, FEA, CFD, and (future) multibody dynamics tools into a single AI-assisted pipeline controlled from a terminal.

The owner accesses the server via SSH from a laptop. GUI work (FreeCAD modelling) is done via VNC or NoMachine. The server is bare metal, dedicated hardware — not a VM, not a cloud instance.

**ClawGeneer is not a simulation tool. It is a coordinator.** It never does engineering work itself. It tells the right tool to run at the right time with the right inputs, checks outputs for convergence and validity, and advances to the next stage.

---

## The Core Problem Being Solved

Commercial engineering software (ANSYS, SolidWorks, MATLAB, Aspen Plus) is:
- Expensive and licence-locked
- Not scriptable end-to-end
- Not AI-integrated
- Siloed — no unified project format across tools

ClawGeneer replaces this stack with open-source equivalents, unified under a single Python orchestration layer, with an AI layer that can interpret natural language constraints, generate geometry, review results, and iterate toward optimal designs.

---

## Tool Classes

The pipeline has four classes of tools. Classes 1–3 are implemented. Class 4 is documented for future phases.

### Class 1: CAD
Produces geometry. Output is always `.STEP` (B-Rep, not mesh).

| Tool | Priority | When Used |
|---|---|---|
| **Build123d** | **PRIMARY** | AI-generated geometry; preferred for LLM code generation |
| **CadQuery** | Secondary/fallback | Fallback; larger ecosystem, less ideal LLM syntax |
| **FreeCAD** | GUI only | User models in FreeCAD GUI; `.FCStd` exported to STEP |
| **Upload adapter** | Intake | User uploads `.STEP`, `.IGES`, `.STL`, `.FCStd` |

**Why Build123d is primary:** Build123d is the modern evolution of CadQuery. It uses context-manager syntax that produces fewer LLM hallucinations, and crucially, its face-level `.label` tagging (via `BuildPart`) is deeply integrated with OpenCASCADE — labels persist through Boolean operations and survive export to STEP. This directly solves the Surface Naming Problem (see below).

### Class 2: Mesh
Converts `.STEP` geometry to solver-ready mesh format.

| Tool | When Used |
|---|---|
| **Gmsh** (Python API) | Always — primary mesher for both FEA and CFD |
| **meshio** | Format conversion only: `.msh` → CalculiX `.inp` |
| **gmshToFoam** | Mesh conversion: `.msh` → OpenFOAM `constant/polyMesh/` |

### Class 3: Solvers
Run sequentially. Never simultaneously. One solver uses full RAM at a time.

| Tool | Domain | Notes |
|---|---|---|
| **CalculiX (ccx)** | FEA: stress, displacement, strain, fatigue, thermal | Abaqus-compatible input format |
| **OpenFOAM ESI v2312** | CFD: flow, pressure, turbulence, heat transfer, drag | ESI/openfoam.com version — use `simpleFoam`, not `foamRun` |

### Class 4: Multibody Dynamics (Future — Phase 12+)
Do not implement now. Document for schema future-proofing.

| Tool | Notes |
|---|---|
| **MBDyn** | General-purpose, mature, CLI-native on Linux. Good for mechanism analysis, linkage kinematics |
| **Project Chrono** | C++ multi-physics, GPU-capable, Python bindings. For heavy vibration and collision work |

When Class 4 is implemented, it slots in after FEA in the pipeline: CAD → Mesh → [FEA] → [MBD] → [CFD] → Results. The schema `jobs` list already supports this.

---

## Sequential Job Execution — This Is Sacred

**Never run two solvers simultaneously.** The 16 GB RAM machine must give each solver everything it has.

```
CAD → MESH → [FEA] → [CFD] → RESULTS
```

If both FEA and CFD are requested for one project, FEA runs first, finishes completely, memory is explicitly released (`del`, `gc.collect()`), then CFD starts.

---

## The Adapter Pattern — The Backbone

Every tool lives behind a `BaseAdapter` class. The pipeline only ever calls four methods:

```python
class BaseAdapter(ABC):
    def validate_inputs(self) -> bool: ...
    def run(self) -> AdapterResult: ...
    def parse_outputs(self) -> dict: ...
    def check_installed(self) -> bool: ...
```

`AdapterResult` is a dataclass:
```python
@dataclass
class AdapterResult:
    success: bool
    output_path: Path | None = None
    summary: dict = field(default_factory=dict)
    logs: str = ""
    error: str | None = None
```

**Adding a new tool** = one new class inheriting `BaseAdapter` + one entry in `tools/registry.yaml`. The pipeline runner does not change.

---

## The Project File — `project.yaml`

This is the single source of truth for every job. The pipeline reads it, every adapter reads it, the AI reads it, the optimizer writes to it. Nothing is passed between components as raw arguments — everything goes through this file.

Key sections:
- `geometry`: source (generate/upload/freecad_gui), tool (build123d/cadquery/freecad), parameters
- `material`: engineering properties (E, nu, rho, yield strength)
- `mesh`: tool, element size, element order
- `jobs`: list of FEA and/or CFD jobs with boundary conditions
- `results`: populated by adapters after solving (initially null)
- `optimization`: enabled flag, objective, parameter bounds, constraints
- `components`: list for future assembly support (optional, defaults empty)

The Pydantic model lives in `clawgeneer/schema/project.py`. Always use the Pydantic model — never parse the YAML manually.

---

## Surface Naming: The #1 Integration Risk

**This is the single biggest risk in the pipeline. Understand it completely.**

When Gmsh imports a STEP file, surfaces get arbitrary integer tags (1, 2, 7, 12...). But `project.yaml` boundary conditions reference surfaces by name (`bottom_face`, `inlet`, `wall`). There is no automatic mapping unless names are baked into the STEP file.

### Path A: AI-Generated Geometry (Build123d — PRIMARY)

Build123d's `.label` system tags faces at the OpenCASCADE level. These labels are written into the STEP file as entity names and survive the Gmsh import:

```python
from build123d import *

with BuildPart() as bracket:
    Box(100, 60, 8)
    faces().filter_by(Axis.Z).last.label = "top_face"
    faces().filter_by(Axis.Z).first.label = "bottom_face"
    faces().filter_by(Axis.X).last.label = "right_face"

export_step(bracket.part, "part.step")
```

After Gmsh imports this STEP, read labels back with:
```python
name = gmsh.model.getEntityName(2, surface_tag)  # dim=2 for surfaces
```

**This is reliable.** Labels created in Build123d persist through Boolean operations (union, cut, intersect) and survive export. CadQuery's `.tag()` mechanism is shallower and can be lost by Boolean ops — prefer Build123d.

### Path A (Fallback): AI-Generated Geometry (CadQuery)

CadQuery face tagging works for simple geometry but is fragile after Boolean operations:
```python
result = (
    cq.Workplane("XY")
    .box(100, 60, 8)
    .faces(">Z").tag("top_face")
    .faces("<Z").tag("bottom_face")
)
```

### Path B: Uploaded or GUI-Modelled Geometry

Interactive labelling: ClawGeneer lists all surfaces with bounding box, area, and normal vector. User (or AI) confirms which label maps to which surface. This confirmation is stored in `project.yaml` under `geometry.surface_map`.

**Never skip this step.** If surface names don't map correctly, boundary conditions are applied to wrong faces and results are physically meaningless — no error is thrown.

---

## Gmsh Mesh Version — One Line, Critical

`gmshToFoam` (OpenFOAM's mesh importer) requires Gmsh format **v2.2**. The Gmsh Python API defaults to v4. This silently corrupts the CFD mesh or fails with a cryptic error.

**Always set this in the Gmsh adapter:**
```python
gmsh.option.setNumber("Mesh.MshFileVersion", 2.2)
```

---

## OpenFOAM Version and Solver Names

**Use ESI/OpenCFD version (openfoam.com), v2312 or later.** Do NOT use the OpenFOAM Foundation version (openfoam.org v11+), which replaces solver names with the modular `foamRun` approach.

ESI solver binaries used in this project:
- `simpleFoam` — steady-state incompressible RANS
- `pimpleFoam` — transient incompressible
- `chtMultiRegionFoam` — conjugate heat transfer (future)

Install:
```bash
sudo sh -c "wget -O - https://dl.openfoam.com/add-debian-repo.sh | bash"
sudo apt install openfoam2312
source /usr/lib/openfoam/openfoam2312/etc/bashrc
```

---

## OpenFOAM Patch Types — Silent Wrong Physics

`gmshToFoam` sets all boundary patches to type `patch`. OpenFOAM requires:
- `wall` for no-slip surfaces (turbulence wall functions only apply to `wall` type)
- `symmetryPlane` for symmetry boundaries
- `patch` for inlets and outlets

If a wall is left as type `patch`, wall functions silently don't apply. Results look plausible but are physically wrong.

**The CFD adapter must post-process `constant/polyMesh/boundary`** after `gmshToFoam` runs, setting correct types based on `project.yaml` BC definitions.

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

LLM-generated CAD code (Build123d or CadQuery) must be validated BEFORE it is passed to Gmsh. A geometrically invalid solid will crash Gmsh with an unhelpful error.

Build123d validation sequence:
```python
# result is a BuildPart context or exported Compound/Solid
assert result is not None
assert len(result.solids()) > 0          # must produce at least one solid
# Export to temp .STEP, re-import, check again — catches OCC export bugs
```

CadQuery validation sequence:
```python
assert not result.val().isNull()
assert result.val().isValid()
assert result.val().isSolid()
```

If any check fails: send the error back to the LLM with context and retry. Do not proceed to meshing.

---

## pyvista — Headless Rendering on Ubuntu Server

pyvista uses VTK for rendering. On a headless server, VTK cannot open a display. Solution: use Xvfb (X virtual framebuffer).

```bash
# Install (bootstrap script handles this)
apt install xvfb

# In pyvista_adapter.py — must be called before any rendering
import pyvista as pv
pv.start_xvfb()
```

The bootstrap script installs `xvfb`. Do not skip this — offscreen rendering will crash without it.

An alternative (not used here) is an OSMesa-compiled VTK, which is complex to build and not worth it for this use case.

---

## LLM Integration Points — Exactly Three

The LLM is used at exactly three points. Do not add LLM calls anywhere else without good reason.

### 1. CAD Generation (`ai/cad_gen.py`)
- **Input**: User prompt + `project.yaml` geometry section
- **Output**: Executable Build123d Python code (JSON-wrapped, `code` field)
- **Model**: GitHub Models API (interactive) or Ollama (optimization loops)
- **System prompt must include**: Build123d face label instructions, unit conventions (mm), `make_part()` function signature, JSON output format

### 2. Constraint Interpretation (`ai/constraint_handler.py`)
- **Input**: Vague user description ("a hand-sized bracket for a small motor")
- **Output**: Populated `project.yaml` geometry parameters OR a list of clarifying questions
- **Logic**: If dimensions are underspecified, ask. If material is unspecified, default to mild steel. If load is unspecified, ask. **Never guess safety-critical values.**

### 3. Result Review (`ai/result_review.py`)
- **Input**: `results/fea_summary.json` or `results/cfd_summary.json` + full `project.yaml`
- **Output**: Plain-English interpretation + specific suggested parameter changes with exact values
- **In optimization mode**: Returns updated parameters directly for the next iteration

---

## LLM Client Architecture

```python
# ai/llm_client.py — two backends, same interface

class LLMClient:
    def __init__(self, mode: str = "interactive"):
        model = os.environ.get("CLAWGENEER_LLM_MODEL", "gpt-4o")
        if mode == "interactive":
            # GitHub Models API — free tier with GitHub PAT
            self.client = OpenAI(
                base_url="https://models.inference.ai.azure.com",
                api_key=os.environ["GITHUB_PAT"],
            )
            self.model = model
        elif mode == "optimization":
            # Ollama local — unlimited, no rate limits
            self.client = OpenAI(
                base_url="http://localhost:11434/v1",
                api_key="ollama",
            )
            self.model = os.environ.get("CLAWGENEER_OLLAMA_MODEL", "qwen2.5-coder:7b")
```

GitHub Models API now offers GPT-4o, GPT-5, gpt-5-mini, gpt-5-nano, and others. Set `CLAWGENEER_LLM_MODEL` to whichever model your Copilot account can access.

### Ollama Model Sizing (16 GB RAM, No GPU)

| Model | RAM required | Recommended for |
|---|---|---|
| `qwen2.5-coder:7b` | ~5 GB | **Default** — fits easily, fast |
| `qwen2.5-coder:14b` | ~10 GB | Higher quality, leaves ~6 GB for solvers |
| `deepseek-coder:6.7b` | ~5 GB | Alternative to qwen2.5-coder:7b |
| `starcoder2:7b` | ~5 GB | Specialised for code, no chat capability |

On a 16 GB machine with NO GPU, **default to qwen2.5-coder:7b** (pulled by bootstrap script). The 14b model can be used but leaves less headroom for solvers running concurrently.

---

## Memory Budget (16 GB RAM)

```
Interactive mode (GitHub Models handles LLM — offloaded to cloud):
├── OpenFOAM / CalculiX:  ~12–13 GB available
└── ClawGeneer daemon + OS: ~3–4 GB

Optimization mode (Ollama running locally):
├── qwen2.5-coder:7b:     ~5 GB
├── OpenFOAM / CalculiX:  ~9–10 GB
└── ClawGeneer daemon + OS: ~1 GB
```

In optimization mode, Ollama loads the model once and keeps it resident. The solver runs after the LLM proposes new parameters — they never run simultaneously.

---

## Future Tool Candidates (Do Not Implement Yet)

### Topology Optimization
- **ToPy**: Python SIMP-method topology optimizer. Produces density fields that need post-processing back to geometry (marching cubes → STEP export). Future Phase.

### Elmer FEM
- Multiphysics FEA (thermal + electromagnetic + structural coupling). Better than CalculiX for coupled problems. Future Class 3 addition.

### MCP Compatibility
The adapter interface is designed to be MCP (Model Context Protocol) compatible. Each adapter can be exposed as an MCP tool, allowing MCP-compatible AI agents to call ClawGeneer's tools directly without going through the CLI. Future concern — do not over-engineer now.

---

## Project Folder Structure (Per-Project, Not Repo)

```
~/projects/<project-name>/
├── project.yaml              <- single source of truth
├── pipeline_state.json       <- resumable state tracking
├── geometry/
│   └── part.step             <- output of CAD stage
├── mesh/
│   ├── part.msh              <- Gmsh output (v2.2 format)
│   └── surface_map.json      <- surface name -> Gmsh tag mapping
├── fea/
│   ├── model.inp             <- CalculiX input (meshio-generated)
│   ├── model.frd             <- CalculiX raw results
│   └── model.vtu             <- converted by ccx2paraview
├── cfd/
│   ├── 0/                    <- OpenFOAM initial conditions
│   ├── constant/             <- mesh + physical properties
│   └── system/               <- solver controls
└── results/
    ├── fea_summary.json      <- max_stress, max_displacement, safety_factor
    ├── cfd_summary.json      <- max_pressure, avg_drag, wall_shear_stress
    ├── fea_stress.png        <- offscreen pyvista render
    └── cfd_pressure.png      <- offscreen pyvista render
```

---

## Repo Structure

```
clawgeneer/                      <- repo root
├── CLAUDE.md
├── README.md
├── pyproject.toml               <- tool config only (pytest, ruff) — NOT a pip package
├── .gitignore
├── clawgeneer/                  <- Python package
│   ├── __init__.py
│   ├── schema/
│   │   ├── project.py           <- Pydantic v2 model
│   │   └── templates/
│   │       └── project.yaml     <- default template
│   ├── pipeline/
│   │   ├── runner.py            <- CAD->Mesh->FEA->CFD->Results orchestrator
│   │   └── state.py             <- resumable state tracking
│   ├── adapters/
│   │   ├── base.py
│   │   ├── cad/
│   │   │   ├── build123d_adapter.py   <- PRIMARY
│   │   │   ├── cadquery_adapter.py    <- fallback
│   │   │   └── upload_adapter.py
│   │   ├── mesh/
│   │   │   └── gmsh_adapter.py
│   │   ├── fea/
│   │   │   └── calculix_adapter.py
│   │   ├── cfd/
│   │   │   └── openfoam_adapter.py
│   │   └── results/
│   │       └── pyvista_adapter.py
│   ├── ai/
│   │   ├── llm_client.py
│   │   ├── cad_gen.py
│   │   ├── constraint_handler.py
│   │   └── result_review.py
│   ├── tools/
│   │   ├── registry.yaml
│   │   ├── tool_manager.py
│   │   └── bootstrap.sh
│   └── cli/
│       └── oc.py
├── templates/
│   ├── openfoam/
│   │   ├── 0/U, 0/p, 0/k, 0/epsilon
│   │   ├── constant/transportProperties
│   │   └── system/controlDict, fvSchemes, fvSolution
│   └── calculix/
│       └── base.inp
└── tests/
    ├── test_schema.py
    ├── test_tool_manager.py
    └── test_pipeline.py
```

---

## Assembly Support (Future — Phase 11)

The `project.yaml` schema supports `type: assembly` from day one. Assembly projects have a `components` list, each with a file, material, position, and orientation. Build123d's `BuildAssembly` context handles this natively. This flows into Gmsh and solvers identically to a single part.

Do not implement assembly-specific logic until Phase 11. Do not design schemas or data structures that block it.

---

## What ClawGeneer Is NOT

- **Not a closed ecosystem**: Every tool is swappable. Adding a tool = one adapter class + one registry entry.
- **Not a cloud service**: Everything runs on the owner's hardware. No data leaves the machine except LLM API calls.
- **Not a GUI application**: The primary interface is the `oc` CLI. GUI is for CAD modelling only.
- **Not a replacement for engineering judgement**: The AI suggests. The user decides. Safety-critical values are never silently assumed.
- **Not a pip-installable package**: Run from the cloned repo with a venv created by `bootstrap.sh`.

---

## Build Order (Phased)

Build in this order. Do not skip phases.

1. `schema/project.py` — Pydantic model, validates `project.yaml`
2. `adapters/base.py` — `BaseAdapter` + `AdapterResult` contract
3. `tools/tool_manager.py` + `registry.yaml` — install/check/invoke from registry
4. `adapters/mesh/gmsh_adapter.py` — pure Python, test first
5. `adapters/fea/calculix_adapter.py` — meshio + subprocess
6. `adapters/cad/build123d_adapter.py` — LLM code execution + validation gate
7. `ai/llm_client.py` — GitHub Models + Ollama dual backend
8. `ai/cad_gen.py` — prompt -> Build123d code
9. `adapters/cfd/openfoam_adapter.py` — case templating + gmshToFoam + simpleFoam
10. `adapters/results/pyvista_adapter.py` — .frd/.vtk -> JSON + PNG (xvfb required)
11. `pipeline/runner.py` — wire all adapters together
12. `cli/oc.py` — terminal interface
13. `ai/constraint_handler.py` — vague prompt -> filled params
14. `ai/result_review.py` — results -> suggestions
15. `tools/bootstrap.sh` — full Ubuntu 24.04 install script
16. Phase 11+: Assembly support, MBDyn/Chrono adapter, topology optimization (ToPy), Elmer FEM

---

## Key Dependencies

```
build123d         # PRIMARY CAD geometry engine (modern CadQuery successor)
cadquery          # Secondary CAD geometry engine (fallback)
gmsh              # Meshing (Python API)
meshio[all]       # Mesh format conversion
foamlib           # OpenFOAM Python automation (replaces PyFoam — actively maintained, JOSS 2025)
pyvista[all]      # Results visualisation (offscreen; requires xvfb on headless server)
ccx2paraview      # CalculiX .frd -> .vtk
openai            # LLM API client (GitHub Models + Ollama via OpenAI-compatible API)
pydantic          # project.yaml schema validation (v2)
optuna            # Parametric optimization loop
```

Note: `python-control` is deferred to future control systems phases and should not be installed in the base venv.

---

## Environment Variables Required

```bash
GITHUB_PAT=<your_github_personal_access_token>
CLAWGENEER_LLM_MODEL=gpt-4o          # GitHub Models model name
CLAWGENEER_OLLAMA_MODEL=qwen2.5-coder:7b  # Ollama model name (7b default for 16GB no-GPU)
CLAWGENEER_LLM_MODE=interactive       # interactive | optimization
CLAWGENEER_PROJECTS_DIR=~/projects
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
- `gc.collect()` after every stage that loads large mesh/solver data
- No hardcoded paths anywhere — all paths derived from `project.yaml` or env vars
- Type hints on all function signatures
- Docstrings on all public classes and methods
