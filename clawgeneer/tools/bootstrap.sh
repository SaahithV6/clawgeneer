#!/usr/bin/env bash
# ClawGeneer bootstrap script for Ubuntu 24.04 bare-metal server
# Installs all system dependencies, Python packages, and configures the environment.
# Idempotent — safe to run multiple times.

set -euo pipefail

# ─── Colour helpers ──────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

info()    { echo -e "${CYAN}[INFO]${RESET}  $*"; }
success() { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error()   { echo -e "${RED}[ERROR]${RESET} $*" >&2; }
section() { echo -e "\n${BOLD}${CYAN}══════════════════════════════════════════════════${RESET}"; \
            echo -e "${BOLD}${CYAN}  $*${RESET}"; \
            echo -e "${BOLD}${CYAN}══════════════════════════════════════════════════${RESET}"; }

# ─── Configuration ────────────────────────────────────────────────────────────
INSTALL_DIR="${CLAWGENEER_INSTALL_DIR:-/opt/clawgeneer}"
PROJECTS_DIR="${CLAWGENEER_PROJECTS_DIR:-$HOME/projects}"
VENV_DIR="${INSTALL_DIR}/venv"
PYTHON_MIN="3.11"
OPENFOAM_VERSION="openfoam2312"
OPENFOAM_BASHRC="/usr/lib/openfoam/${OPENFOAM_VERSION}/etc/bashrc"

# ─── Root check ───────────────────────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
    error "This script must be run as root (sudo ./bootstrap.sh)"
    exit 1
fi

section "ClawGeneer Bootstrap — Ubuntu 24.04"
info "Install dir  : ${INSTALL_DIR}"
info "Projects dir : ${PROJECTS_DIR}"
info "Python venv  : ${VENV_DIR}"
echo

# ─── 1. System packages ───────────────────────────────────────────────────────
section "1. System packages"

apt-get update -qq

SYSTEM_PKGS=(
    build-essential
    python3.11
    python3.11-venv
    python3.11-dev
    python3-pip
    xvfb
    git
    wget
    curl
    apt-transport-https
    ca-certificates
    gnupg
    libgl1-mesa-glx
    libglu1-mesa
    libxrender1
    libxcursor1
    libxft2
    libxinerama1
)

for pkg in "${SYSTEM_PKGS[@]}"; do
    if dpkg -s "$pkg" &>/dev/null; then
        info "  $pkg — already installed"
    else
        info "  Installing $pkg..."
        apt-get install -y -qq "$pkg"
        success "  $pkg installed"
    fi
done

success "System packages ready"

# ─── 2. CalculiX ──────────────────────────────────────────────────────────────
section "2. CalculiX FEA solver"

if command -v ccx &>/dev/null; then
    info "CalculiX already installed: $(ccx --version 2>&1 | head -1 || echo 'version unknown')"
else
    info "Installing calculix-ccx..."
    apt-get install -y -qq calculix-ccx
    success "CalculiX installed"
fi

# ─── 3. OpenFOAM ESI v2312 ────────────────────────────────────────────────────
section "3. OpenFOAM ESI v2312"

if [[ -f "${OPENFOAM_BASHRC}" ]]; then
    info "OpenFOAM ${OPENFOAM_VERSION} already installed"
else
    info "Adding OpenFOAM ESI repository..."
    wget -q -O - https://dl.openfoam.com/add-debian-repo.sh | bash

    info "Installing ${OPENFOAM_VERSION}..."
    apt-get install -y -qq "${OPENFOAM_VERSION}"
    success "OpenFOAM ${OPENFOAM_VERSION} installed"
fi

# Verify simpleFoam is accessible
if bash -c "source ${OPENFOAM_BASHRC} && command -v simpleFoam" &>/dev/null; then
    success "simpleFoam accessible via ESI environment"
else
    warn "simpleFoam not found after install — check ${OPENFOAM_BASHRC}"
fi

# ─── 4. Python virtual environment ────────────────────────────────────────────
section "4. Python virtual environment"

mkdir -p "${INSTALL_DIR}"

if [[ -d "${VENV_DIR}" ]]; then
    info "Venv already exists at ${VENV_DIR}"
else
    info "Creating Python venv at ${VENV_DIR}..."
    python3.11 -m venv "${VENV_DIR}"
    success "Venv created"
fi

VENV_PIP="${VENV_DIR}/bin/pip"
VENV_PYTHON="${VENV_DIR}/bin/python"

info "Upgrading pip..."
"${VENV_PIP}" install --quiet --upgrade pip

# ─── 5. Python packages ───────────────────────────────────────────────────────
section "5. Python packages"

PYTHON_PKGS=(
    "build123d"
    "cadquery"
    "gmsh"
    "meshio[all]"
    "foamlib"
    "pyvista[all]"
    "openai"
    "pydantic>=2.0"
    "optuna"
    "ccx2paraview"
    "pyyaml"
    "pytest"
    "ruff"
)

for pkg in "${PYTHON_PKGS[@]}"; do
    pkg_name="${pkg%%[*}"
    pkg_name="${pkg_name%%>=*}"
    if "${VENV_PIP}" show "${pkg_name}" &>/dev/null; then
        info "  ${pkg_name} — already installed"
    else
        info "  Installing ${pkg}..."
        "${VENV_PIP}" install --quiet "${pkg}"
        success "  ${pkg_name} installed"
    fi
done

success "Python packages ready"

# ─── 6. Ollama (optional) ─────────────────────────────────────────────────────
section "6. Ollama local LLM (optional)"

if command -v ollama &>/dev/null; then
    success "Ollama already installed"
    INSTALL_OLLAMA="no"
else
    read -r -p "  Install Ollama for local LLM support? [y/N]: " INSTALL_OLLAMA_PROMPT
    INSTALL_OLLAMA="${INSTALL_OLLAMA_PROMPT:-n}"
fi

if [[ "${INSTALL_OLLAMA,,}" == "y" || "${INSTALL_OLLAMA,,}" == "yes" ]]; then
    info "Installing Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh
    success "Ollama installed"

    read -r -p "  Pull qwen2.5-coder:7b model (~5 GB)? [y/N]: " PULL_MODEL_PROMPT
    PULL_MODEL="${PULL_MODEL_PROMPT:-n}"
    if [[ "${PULL_MODEL,,}" == "y" || "${PULL_MODEL,,}" == "yes" ]]; then
        info "Pulling qwen2.5-coder:7b (this may take several minutes)..."
        ollama pull qwen2.5-coder:7b
        success "Model qwen2.5-coder:7b ready"
    else
        info "Skipping model pull. Run later: ollama pull qwen2.5-coder:7b"
    fi
else
    info "Skipping Ollama. To use local LLM later: curl -fsSL https://ollama.com/install.sh | sh"
fi

# ─── 7. Environment variables ─────────────────────────────────────────────────
section "7. Environment configuration"

PROFILE_D="/etc/profile.d/clawgeneer.sh"

if [[ -f "${PROFILE_D}" ]]; then
    info "Profile already exists at ${PROFILE_D}"
else
    cat > "${PROFILE_D}" << EOF
# ClawGeneer environment variables
export CLAWGENEER_INSTALL_DIR="${INSTALL_DIR}"
export CLAWGENEER_PROJECTS_DIR="${PROJECTS_DIR}"
export CLAWGENEER_LLM_MODE="interactive"
export CLAWGENEER_LLM_MODEL="gpt-4o"
export CLAWGENEER_OLLAMA_MODEL="qwen2.5-coder:7b"

# Activate ClawGeneer venv when using 'oc' command
export PATH="${VENV_DIR}/bin:\$PATH"

# OpenFOAM ESI v2312 (source explicitly when needed — not activated globally)
# To use: source ${OPENFOAM_BASHRC}
EOF
    chmod 644 "${PROFILE_D}"
    success "Environment profile written to ${PROFILE_D}"
fi

info "To set your GitHub PAT for interactive LLM mode, add to ~/.bashrc:"
info "  export GITHUB_PAT=your_personal_access_token"

# ─── 8. Projects directory ────────────────────────────────────────────────────
section "8. Projects directory"

# Use the original SUDO_USER home if available
if [[ -n "${SUDO_USER:-}" ]]; then
    USER_HOME=$(getent passwd "${SUDO_USER}" | cut -d: -f6)
    USER_PROJECTS="${USER_HOME}/projects"
else
    USER_PROJECTS="${PROJECTS_DIR}"
fi

if [[ -d "${USER_PROJECTS}" ]]; then
    info "Projects directory already exists: ${USER_PROJECTS}"
else
    mkdir -p "${USER_PROJECTS}"
    [[ -n "${SUDO_USER:-}" ]] && chown "${SUDO_USER}:${SUDO_USER}" "${USER_PROJECTS}"
    success "Projects directory created: ${USER_PROJECTS}"
fi

# ─── 9. Verify installation ───────────────────────────────────────────────────
section "9. Verification"

PASS=0
FAIL=0

check() {
    local name="$1"
    local cmd="$2"
    if eval "${cmd}" &>/dev/null; then
        success "  ${name}"
        ((PASS++)) || true
    else
        warn "  ${name} — NOT FOUND"
        ((FAIL++)) || true
    fi
}

check "Python 3.11"        "python3.11 --version"
check "ccx (CalculiX)"     "command -v ccx"
check "OpenFOAM bashrc"    "[[ -f '${OPENFOAM_BASHRC}' ]]"
check "build123d"          "'${VENV_PYTHON}' -c 'import build123d'"
check "gmsh"               "'${VENV_PYTHON}' -c 'import gmsh'"
check "meshio"             "'${VENV_PYTHON}' -c 'import meshio'"
check "pyvista"            "'${VENV_PYTHON}' -c 'import pyvista'"
check "openai"             "'${VENV_PYTHON}' -c 'import openai'"
check "pydantic v2"        "'${VENV_PYTHON}' -c 'import pydantic; assert int(pydantic.__version__[0]) >= 2'"
check "optuna"             "'${VENV_PYTHON}' -c 'import optuna'"
check "foamlib"            "'${VENV_PYTHON}' -c 'import foamlib'"
check "xvfb-run"           "command -v xvfb-run"

# ─── Summary ──────────────────────────────────────────────────────────────────
section "Bootstrap Complete"

echo -e "  ${GREEN}Passed: ${PASS}${RESET}  ${RED}Failed: ${FAIL}${RESET}"
echo
if [[ ${FAIL} -gt 0 ]]; then
    warn "Some checks failed. Review the output above."
    warn "Common fixes:"
    warn "  - OpenFOAM: Re-run the OpenFOAM repo script manually"
    warn "  - cadquery: May need: pip install cadquery (can be slow to build)"
fi

echo
info "Next steps:"
info "  1. Set GITHUB_PAT in ~/.bashrc for interactive LLM mode"
info "  2. source /etc/profile.d/clawgeneer.sh  (or re-login)"
info "  3. cd /path/to/clawgeneer && oc init my_first_project"
info "  4. Edit ~/projects/my_first_project/project.yaml"
info "  5. oc run my_first_project"
echo
success "ClawGeneer bootstrap complete!"
