#!/usr/bin/env bash
# ClawGeneer bootstrap script for Ubuntu 22.04 / 24.04 bare-metal server
# Installs all system dependencies, Python packages, and configures the environment.
# Idempotent — safe to run multiple times.
#
# Usage:
#   sudo bash clawgeneer/tools/bootstrap.sh

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

# ─── Root check ───────────────────────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
    error "This script must be run as root (sudo bash clawgeneer/tools/bootstrap.sh)"
    exit 1
fi

# ─── Resolve paths ────────────────────────────────────────────────────────────
# Script lives at <repo>/clawgeneer/tools/bootstrap.sh — repo root is two levels up
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Determine the calling user (the one who ran sudo)
REAL_USER="${SUDO_USER:-${USER}}"
REAL_HOME=$(getent passwd "${REAL_USER}" | cut -d: -f6)

VENV_DIR="${REPO_ROOT}/.venv"
PROJECTS_DIR="${REAL_HOME}/projects"
OPENFOAM_VERSION="openfoam2312"
OPENFOAM_BASHRC="/usr/lib/openfoam/${OPENFOAM_VERSION}/etc/bashrc"

# ─── 0. OS detection ─────────────────────────────────────────────────────────
section "0. OS detection"

if [[ -f /etc/os-release ]]; then
    # shellcheck source=/dev/null
    source /etc/os-release
    OS_NAME="${NAME:-unknown}"
    OS_VERSION="${VERSION_ID:-unknown}"
    info "Detected OS: ${OS_NAME} ${OS_VERSION}"
    if [[ "${ID:-}" == "ubuntu" ]] && [[ "${OS_VERSION}" == "22.04" || "${OS_VERSION}" == "24.04" ]]; then
        success "Supported Ubuntu version: ${OS_VERSION}"
    elif [[ "${ID:-}" == "ubuntu" ]]; then
        warn "Ubuntu ${OS_VERSION} is not officially tested. Proceeding anyway."
    else
        warn "Non-Ubuntu OS detected (${OS_NAME} ${OS_VERSION}). Proceeding, but expect issues."
    fi
else
    warn "Cannot determine OS version. Proceeding anyway."
fi

# Auto-detect available Python 3 binary (3.11, 3.12, 3.13 all work)
PYTHON3_BIN="$(command -v python3 2>/dev/null || true)"
if [[ -z "${PYTHON3_BIN}" ]]; then
    error "python3 not found. Install it with: sudo apt install python3"
    exit 1
fi
PYTHON_VERSION="$("${PYTHON3_BIN}" --version 2>&1)"
info "Using ${PYTHON_VERSION} at ${PYTHON3_BIN}"

section "ClawGeneer Bootstrap — Ubuntu 22.04 / 24.04"
info "Repo root    : ${REPO_ROOT}"
info "Python venv  : ${VENV_DIR}"
info "Projects dir : ${PROJECTS_DIR}"
info "Calling user : ${REAL_USER}"
echo

# ─── 1. System packages ───────────────────────────────────────────────────────
section "1. System packages"

apt-get update -qq

SYSTEM_PKGS=(
    build-essential
    git
    wget
    curl
    software-properties-common
    apt-transport-https
    ca-certificates
    gnupg
    python3
    python3-venv
    python3-dev
    python3-pip
    xvfb
    libgl1-mesa-glx
    libglu1-mesa
    libxrender1
    libxcursor1
    libxft2
    libxinerama1
    libsm6
    libice6
    freecad
)

for pkg in "${SYSTEM_PKGS[@]}"; do
    if dpkg -s "$pkg" &>/dev/null; then
        info "  $pkg — already installed"
    else
        info "  Installing $pkg..."
        if ! apt-get install -y -qq "$pkg" 2>/dev/null; then
            warn "  $pkg — failed to install (may not exist on this Ubuntu release, skipping)"
        else
            success "  $pkg installed"
        fi
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
    apt-get update -qq

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

if [[ -d "${VENV_DIR}" ]]; then
    info "Venv already exists at ${VENV_DIR}"
else
    info "Creating Python venv at ${VENV_DIR}..."
    python3 -m venv "${VENV_DIR}"
    success "Venv created"
fi

# Fix ownership so the real user can use it without sudo
chown -R "${REAL_USER}:${REAL_USER}" "${VENV_DIR}" 2>/dev/null || true

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
    pkg_name="${pkg%%\[*}"
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
    if [[ -t 0 ]]; then
        read -r -p "  Install Ollama for local LLM support? [y/N]: " INSTALL_OLLAMA_PROMPT || true
        INSTALL_OLLAMA="${INSTALL_OLLAMA_PROMPT:-n}"
    else
        info "Non-interactive mode — skipping Ollama install. Run later: curl -fsSL https://ollama.com/install.sh | sh"
        INSTALL_OLLAMA="n"
    fi
fi

if [[ "${INSTALL_OLLAMA,,}" == "y" || "${INSTALL_OLLAMA,,}" == "yes" ]]; then
    info "Installing Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh
    success "Ollama installed"

    if [[ -t 0 ]]; then
        read -r -p "  Pull qwen2.5-coder:7b model (~5 GB)? [y/N]: " PULL_MODEL_PROMPT || true
        PULL_MODEL="${PULL_MODEL_PROMPT:-n}"
    else
        info "Non-interactive mode — skipping model pull. Run later: ollama pull qwen2.5-coder:7b"
        PULL_MODEL="n"
    fi
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

# ─── 7. Environment configuration ────────────────────────────────────────────
section "7. Environment configuration"

PROFILE_D="/etc/profile.d/clawgeneer.sh"

# Write /etc/profile.d/clawgeneer.sh (system-wide, loaded for all login shells)
cat > "${PROFILE_D}" << ENVEOF
# ClawGeneer environment — auto-generated by bootstrap.sh
export CLAWGENEER_PROJECTS_DIR="\${CLAWGENEER_PROJECTS_DIR:-\$HOME/projects}"
export CLAWGENEER_LLM_MODE="\${CLAWGENEER_LLM_MODE:-interactive}"
export CLAWGENEER_LLM_MODEL="\${CLAWGENEER_LLM_MODEL:-gpt-4o}"
export CLAWGENEER_OLLAMA_MODEL="\${CLAWGENEER_OLLAMA_MODEL:-qwen2.5-coder:7b}"
export PYTHONPATH="${REPO_ROOT}\${PYTHONPATH:+:\$PYTHONPATH}"

# ClawGeneer venv on PATH (provides 'python', 'pip', and installed scripts)
export PATH="${VENV_DIR}/bin:\$PATH"

# OpenFOAM ESI v2312
if [[ -f "${OPENFOAM_BASHRC}" ]]; then
    source "${OPENFOAM_BASHRC}"
fi

# 'oc' shorthand for the ClawGeneer CLI
alias oc="python3 -m clawgeneer.cli.oc"
ENVEOF
chmod 644 "${PROFILE_D}"
success "System profile written to ${PROFILE_D}"

# Also append to the real user's ~/.bashrc so it works in non-login interactive shells
USER_BASHRC="${REAL_HOME}/.bashrc"
BASHRC_MARKER="# ClawGeneer — added by bootstrap.sh"
if grep -qF "${BASHRC_MARKER}" "${USER_BASHRC}" 2>/dev/null; then
    info "~/.bashrc already contains ClawGeneer config"
else
    cat >> "${USER_BASHRC}" << BASHRCEOF

${BASHRC_MARKER}
if [[ -f "${PROFILE_D}" ]]; then
    source "${PROFILE_D}"
fi
BASHRCEOF
    chown "${REAL_USER}:${REAL_USER}" "${USER_BASHRC}"
    success "ClawGeneer config sourced from ${USER_BASHRC}"
fi

info "To set your GitHub PAT for interactive LLM mode, add to ~/.bashrc:"
info "  export GITHUB_PAT=your_personal_access_token"

# ─── 8. Projects directory ────────────────────────────────────────────────────
section "8. Projects directory"

if [[ -d "${PROJECTS_DIR}" ]]; then
    info "Projects directory already exists: ${PROJECTS_DIR}"
else
    mkdir -p "${PROJECTS_DIR}"
    chown "${REAL_USER}:${REAL_USER}" "${PROJECTS_DIR}"
    success "Projects directory created: ${PROJECTS_DIR}"
fi

# ─── 9. Run oc check ─────────────────────────────────────────────────────────
section "9. Verification (oc check)"

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

check "python3"            "python3 --version"
check "ccx (CalculiX)"     "command -v ccx"
check "freecad"            "command -v freecad || command -v FreeCAD || command -v freecadcmd"
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

# Run oc check if the CLI is importable
if PYTHONPATH="${REPO_ROOT}" "${VENV_PYTHON}" -m clawgeneer.cli.oc check 2>/dev/null; then
    success "oc check passed"
else
    warn "oc check reported missing tools (see output above)"
fi

# ─── Summary ──────────────────────────────────────────────────────────────────
section "Bootstrap Complete"

echo -e "  ${GREEN}Passed: ${PASS}${RESET}  ${RED}Failed: ${FAIL}${RESET}"
echo
if [[ ${FAIL} -gt 0 ]]; then
    warn "Some checks failed. Review the output above."
    warn "Common fixes:"
    warn "  - OpenFOAM: Re-run the OpenFOAM repo script manually"
    warn "  - cadquery/build123d: Can be slow to install; re-run bootstrap.sh if it timed out"
fi

echo
echo -e "${GREEN}${BOLD}✓ ClawGeneer installed successfully.${RESET}"
echo
echo -e "  To activate:"
echo -e "    ${CYAN}source ~/.bashrc${RESET}"
echo -e "    ${CYAN}source ${VENV_DIR}/bin/activate${RESET}"
echo
echo -e "  Quick start:"
echo -e "    ${CYAN}oc init my_bracket${RESET}"
echo -e "    ${CYAN}oc check${RESET}"
echo -e "    ${CYAN}oc run my_bracket${RESET}"
echo
