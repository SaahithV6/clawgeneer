# ClawGeneer 

**An open-source engineering orchestration platform for Ubuntu Server.**

ClawGeneer connects open-source CAD, meshing, FEA, and CFD tools into a single AI-assisted, scriptable pipeline — turning a bare-metal Ubuntu Server into a fully capable engineering workstation.

> Designed for undergraduate and professional engineers who want parametric, reproducible, AI-optimised simulations without commercial licences.

---

## What It Does

You describe what you want. ClawGeneer generates the geometry, meshes it, runs the physics solver, reviews the results, and iterates — all from a single terminal command.

```bash
oc "Design a steel mounting bracket, fixed at the base, 5000N downward load. Optimise for minimum mass."
```

Or upload your own CAD file and let ClawGeneer handle the rest:

```bash
scp my_part.step engineer@server:~/projects/my_bracket/geometry/
oc run my_bracket
```

Or model directly on the server in FreeCAD (via VNC/NoMachine) and run solvers on demand.

---

## The Stack

All tools are open-source and run fully locally on your hardware.

| Stage | Tool | Replaces |
|---|---|---|
| **CAD** | Build123d (AI-generated, primary) / CadQuery (fallback) / FreeCAD (GUI) | SolidWorks, Inventor |
| **Meshing** | Gmsh | ANSYS Meshing, HyperMesh |
| **FEA** | CalculiX | ANSYS Mechanical, Abaqus |
| **CFD** | OpenFOAM ESI v2312 | ANSYS Fluent, STAR-CCM+ |
| **Results** | pyvista (headless via Xvfb) | ANSYS Post, EnSight |
| **AI** | GitHub Models API (interactive) / Ollama (offline) | — |
| **Optimisation** | Optuna | MATLAB Optimisation Toolbox |

---

## Architecture

ClawGeneer is a **coordinator, not a container**. It does not do engineering work itself. It tells the right tool to run at the right time, checks outputs, and moves to the next stage.

```
User Prompt / Uploaded File / FreeCAD GUI
                    │
                    ▼
         ┌─────────────────────┐
         │  Constraint Handler  │  ← AI fills in missing dimensions
         └──────────┬──────────┘
                    │
         ┌──────────▼──────────┐
         │    CAD Generation    │  ← CadQuery (LLM-written Python)
         │    or File Ingest    │  ← Uploaded STEP / FreeCAD file
         └──────────┬──────────┘
                    │  .STEP
         ┌──────────▼──────────┐
         │       Meshing        │  ← Gmsh Python API
         └──────────┬──────────┘
                    │  .msh
         ┌──────────▼──────────┐
         │    FEA (CalculiX)    │  ← Sequential
         │    CFD (OpenFOAM)    │  ← One at a time
         └──────────┬──────────┘
                    │
         ┌──────────▼──────────┐
         │   Results + Review   │  ← pyvista + AI analysis
         └──────────┬──────────┘
                    │
         ┌──────────▼──────────┐
         │   Iterate / Report   │  ← Optimisation loop or user review
         └─────────────────────┘
```

Every tool lives behind a standard `BaseAdapter` interface. Swapping a solver means writing one new adapter class — the pipeline does not change.

---

## Repository Layout

```
clawgeneer/
├── clawgeneer/
│   ├── schema/
│   │   ├── project.py              ← Pydantic model for project.yaml
│   │   └── templates/
│   │       └── project.yaml        ← default project template
│   ├── pipeline/
│   │   ├── runner.py               ← sequential job orchestrator
│   │   ├── job_queue.py
│   │   └── state.py
│   ├── adapters/
│   │   ├── base.py                 ← BaseAdapter + AdapterResult
│   │   ├── cad/
│   │   │   ├── cadquery_adapter.py
│   │   │   ├── freecad_adapter.py
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
│   │   ├── llm_client.py           ← GitHub Models + Ollama wrapper
│   │   ├── cad_gen.py              ← prompt → CadQuery code
│   │   ├── constraint_handler.py   ← vague prompt → project params
│   │   └── result_review.py        ← results → suggestions
│   ├── tools/
│   │   ├── registry.yaml           ← tool manifest
│   │   ├── tool_manager.py         ← install / check / invoke
│   │   └── bootstrap.sh            ← full server setup script
│   └── cli/
│       └── oc.py                   ← $ oc [...] terminal interface
├── templates/
│   ├── openfoam/                   ← base case templates
│   └── calculix/                   ← base .inp snippets
├── tests/
├── CLAUDE.md                       ← full project context for AI assistants
├── pyproject.toml
└── README.md
```

---

## Three Modes of Use

### 1. Autonomous — AI drives everything
```bash
oc "A steel rectangular bracket, fixed base, 5000N load on top, optimise for minimum mass under 200MPa stress"
# → asks clarifying questions if dimensions missing
# → generates CadQuery geometry
# → meshes, runs FEA, reviews results
# → iterates geometry until constraints are met
```

### 2. Assisted — You provide the geometry
```bash
scp bracket.step engineer@192.168.1.x:~/projects/bracket/geometry/
oc run bracket
# → meshes your STEP file
# → runs FEA/CFD as defined in project.yaml
# → AI reviews results and suggests changes
# → you approve/reject and re-run
```

### 3. Interactive — Full GUI + AI chat
```bash
# On your laptop, connect via VNC/NoMachine to the server
# Open FreeCAD on the server — model your part
# In a separate SSH terminal:
oc chat my_part     # live AI engineering assistant
oc run my_part      # run solvers when ready
```

---

## Project File (`project.yaml`)

Every job is defined by a single YAML file. This is the source of truth for the geometry, material, mesh settings, boundary conditions, and results.

```yaml
project:
  name: my_bracket
  type: part

geometry:
  source: generate        # generate | upload | freecad_gui
  tool: build123d         # build123d (primary) | cadquery | freecad
  parameters:
    length: 100           # mm
    width: 60
    thickness: 8

material:
  name: steel_mild
  youngs_modulus: 210000  # MPa
  poisson_ratio: 0.3
  yield_strength: 250     # MPa

mesh:
  tool: gmsh
  element_size: 3.0       # mm

jobs:
  - type: fea
    solver: calculix
    boundary_conditions:
      - surface: bottom_face
        type: fixed
      - surface: top_face
        type: force
        magnitude: 5000   # N
        direction: [0, -1, 0]

optimization:
  enabled: true
  objective: minimize_mass
  constraints:
    max_stress_mpa: 200
  parameters:
    thickness: [3, 15]
```

---

## Hardware Requirements

| Spec | Minimum | Recommended |
|---|---|---|
| OS | Ubuntu Server 22.04 | Ubuntu Server 24.04 LTS |
| RAM | 8 GB | 16 GB |
| CPU | 4 cores | 8+ cores |
| Storage | 50 GB | 100+ GB |
| GPU | Not required | Not required |
| Network | LAN SSH access | LAN SSH access |

---

## Setup

Install all dependencies on a fresh Ubuntu Server 24.04 machine:

```bash
git clone https://github.com/SaahithV6/clawgeneer.git
cd clawgeneer
bash clawgeneer/tools/bootstrap.sh
```

The bootstrap script installs OpenFOAM ESI v2312, CalculiX, Python venv with all dependencies, and optionally Ollama for local LLM inference.

### Environment Variables

```bash
export GITHUB_PAT=<your_github_personal_access_token>
export CLAWGENEER_LLM_MODEL=gpt-4o              # or gpt-5-mini, gpt-5, etc.
export CLAWGENEER_OLLAMA_MODEL=qwen2.5-coder:7b # default for 16GB no-GPU
export CLAWGENEER_PROJECTS_DIR=~/projects
```

### Quick Start

```bash
# Add clawgeneer to PYTHONPATH
export PYTHONPATH=/path/to/clawgeneer:$PYTHONPATH

# Create a new project
python -m clawgeneer.cli.oc init my_bracket

# Edit ~/projects/my_bracket/project.yaml, then run:
python -m clawgeneer.cli.oc run my_bracket

# Check tool installation status
python -m clawgeneer.cli.oc check
```

---

## Design Principles

- **Coordinator, not container**: ClawGeneer orchestrates. Tools do the work.
- **Adapter pattern**: Every tool behind a standard interface. Swap anything without touching the pipeline.
- **Sequential execution**: One solver at a time. Full RAM to each stage.
- **Single source of truth**: `project.yaml` drives everything.
- **Open ecosystem**: Add any tool by writing one adapter class and one registry entry.
- **No vendor lock-in**: Every tool is OSS. No licences. No cloud dependency (except optional LLM API).
- **AI as assistant, not authority**: AI suggests geometry, interprets results, proposes changes. User approves.

---

## Current Status

🚧 **Pre-development — architecture and design phase complete.**

See `CLAUDE.md` for the full architectural specification, all design decisions, known integration issues, and build order.

---

## Roadmap

| Phase | Focus | Status |
|---|---|---|
| 1 | Pydantic schema + BaseAdapter contract | ✅ |
| 2 | Gmsh adapter (mesh) | ✅ |
| 3 | CalculiX adapter (FEA) | ✅ |
| 4 | Build123d adapter (primary CAD) + LLM CAD gen | ✅ |
| 5 | OpenFOAM ESI v2312 adapter (CFD) | ✅ |
| 6 | pyvista results adapter (Xvfb headless) | ✅ |
| 7 | Pipeline runner + CLI (`oc` command) | ✅ |
| 8 | Constraint handler + Result review AI | ✅ |
| 9 | Optimisation loop (Optuna) | 🔲 |
| 10 | Bootstrap script + systemd daemon | ✅ |
| 11 | Assembly support (Build123d BuildAssembly) | 🔲 |
| 12 | Multibody dynamics (MBDyn / Chrono) | 🔲 |
| 13 | Topology optimisation (ToPy) | 🔲 |
| 14 | Elmer FEM (multiphysics coupling) | 🔲 |
| 15 | Web dashboard | 🔲 |

---

## Contributing

This project is in active early design. If you want to contribute, read `CLAUDE.md` first — it contains the complete architectural context.

---

## Licence

MIT
