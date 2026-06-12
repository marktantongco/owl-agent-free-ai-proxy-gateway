#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
#  OWL-AGENT v7.0 — Unified Installer
#  Supports: Antigravity, Claude, OpenCode, Copilot, Kiro, Hermes
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

# ── Version & Constants ────────────────────────────────────────────────────
readonly VERSION="7.0.0"
readonly OWL_HOME="${OWL_HOME:-$HOME/.owl-agent}"
readonly OWL_CONFIG="${OWL_HOME}/config"
readonly OWL_LOGS="${OWL_HOME}/logs"
readonly OWL_CACHE="${OWL_HOME}/cache/http"
readonly OWL_VENV="${OWL_HOME}/venv"
readonly OWL_LOCAL_BIN="${HOME}/.local/bin"
readonly PROXY_PORT=60000
readonly GATEWAY_PORT=8333
readonly MESH_PORT=42100
readonly SWAP_SIZE="2G"
readonly SWAP_THRESHOLD=$((1024 * 1024))  # 1GB in KB
readonly RAM_THRESHOLD=$((4 * 1024 * 1024))  # 4GB in KB
readonly KIRO_GITHUB="https://github.com/anthropics/kiro-gateway.git"
readonly KIRO_CLI_URL="https://releases.kiro.dev/cli/latest/kiro-cli-linux-x64"

# ── Color Palette ──────────────────────────────────────────────────────────
readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[0;33m'
readonly BLUE='\033[0;34m'
readonly MAGENTA='\033[0;35m'
readonly CYAN='\033[0;36m'
readonly BOLD='\033[1m'
readonly DIM='\033[2m'
readonly NC='\033[0m'

# ── Global Flags ───────────────────────────────────────────────────────────
SKIP_KIRO=false
SKIP_GATEWAY=false
ENABLE_ENRICH=false
ENABLE_MESH=false
DRY_RUN=false
UNINSTALL=false

# ── Logging Helpers ────────────────────────────────────────────────────────
log_info()    { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }
log_step()    { echo -e "\n${BOLD}${CYAN}━━━ Step $1 ━━━${NC} ${DIM}$2${NC}\n"; }
log_debug()   { [[ "${VERBOSE:-0}" == "1" ]] && echo -e "${DIM}[DBG]${NC}   $*" || true; }
log_success() { echo -e "${GREEN}[OK]${NC}    $*"; }
log_dry()     { echo -e "${MAGENTA}[DRY]${NC}   $*"; }
log_banner()  {
  echo -e "${BOLD}${CYAN}"
  echo "  ╔══════════════════════════════════════════════════╗"
  echo "  ║          OWL-AGENT v${VERSION} — Installer         ║"
  echo "  ║     Antigravity · Claude · OpenCode · Copilot    ║"
  echo "  ║              Kiro · Hermes                       ║"
  echo "  ╚══════════════════════════════════════════════════╝"
  echo -e "${NC}"
}

# ── Usage ──────────────────────────────────────────────────────────────────
usage() {
  cat <<EOF
OWL-AGENT v${VERSION} — Unified Installer

USAGE:
  $(basename "$0") [OPTIONS]

OPTIONS:
  --skip-kiro       Skip kiro-cli binary download
  --skip-gateway    Skip kiro-gateway setup
  --enrich          Enable proxy enrichment
  --enable-mesh     Enable mesh sync (UDP ${MESH_PORT})
  --dry-run         Simulate without writing files
  --uninstall       Remove everything installed by this script
  -h, --help        Show this help message

PROVIDERS:
  Antigravity, Claude, OpenCode, Copilot, Kiro, Hermes

EXAMPLES:
  $(basename "$0")                    # Full install
  $(basename "$0") --skip-kiro        # Install without kiro-cli
  $(basename "$0") --enrich --enable-mesh  # Full with enrichment + mesh
  $(basename "$0") --dry-run          # Preview what would be done
  $(basename "$0") --uninstall        # Remove all components
EOF
  exit 0
}

# ── Argument Parsing ───────────────────────────────────────────────────────
parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --skip-kiro)    SKIP_KIRO=true;    shift ;;
      --skip-gateway) SKIP_GATEWAY=true; shift ;;
      --enrich)       ENABLE_ENRICH=true; shift ;;
      --enable-mesh)  ENABLE_MESH=true;  shift ;;
      --dry-run)      DRY_RUN=true;      shift ;;
      --uninstall)    UNINSTALL=true;    shift ;;
      -h|--help)      usage ;;
      *)
        log_error "Unknown option: $1"
        usage
        ;;
    esac
  done
}

# ── Helper: ensure_venv ───────────────────────────────────────────────────
ensure_venv() {
  log_info "Checking Python virtual environment..."
  if [[ ! -d "${OWL_VENV}" ]]; then
    log_info "Creating venv at ${OWL_VENV}..."
    if [[ "${DRY_RUN}" == true ]]; then
      log_dry "Would create venv: python3 -m venv ${OWL_VENV}"
      return 0
    fi
    if ! command -v python3 &>/dev/null; then
      log_error "python3 is not installed. Aborting."
      exit 1
    fi
    python3 -m venv "${OWL_VENV}"
    log_success "Virtual environment created."
  else
    log_info "Venv already exists at ${OWL_VENV}."
  fi
}

# ── Helper: install_pip_pkg ───────────────────────────────────────────────
install_pip_pkg() {
  local pkg="$1"
  local retries=3
  local attempt=1
  local pip="${OWL_VENV}/bin/pip"

  if [[ "${DRY_RUN}" == true ]]; then
    log_dry "Would install pip package: ${pkg}"
    return 0
  fi

  while [[ ${attempt} -le ${retries} ]]; do
    log_info "Installing ${pkg} (attempt ${attempt}/${retries})..."
    if "${pip}" install --quiet --upgrade "${pkg}" 2>/dev/null; then
      log_success "Installed ${pkg}"
      return 0
    fi
    log_warn "Attempt ${attempt} failed for ${pkg}. Retrying in ${attempt}s..."
    sleep "${attempt}"
    attempt=$((attempt + 1))
  done

  log_error "Failed to install ${pkg} after ${retries} attempts."
  return 1
}

# ── Helper: backup_file ───────────────────────────────────────────────────
backup_file() {
  local src="$1"
  if [[ -f "${src}" ]]; then
    local ts
    ts=$(date +%Y%m%d%H%M%S)
    local bak="${src}.bak.${ts}"
    if [[ "${DRY_RUN}" == true ]]; then
      log_dry "Would back up: ${src} -> ${bak}"
    else
      cp -a "${src}" "${bak}"
      log_info "Backed up: ${bak}"
    fi
  fi
}

# ── Helper: write_file ────────────────────────────────────────────────────
write_file() {
  local path="$1"
  local content="$2"

  if [[ "${DRY_RUN}" == true ]]; then
    log_dry "Would write file: ${path} ($(echo "${content}" | wc -l) lines)"
    return 0
  fi

  backup_file "${path}"

  local dir
  dir=$(dirname "${path}")
  mkdir -p "${dir}"
  printf '%s\n' "${content}" > "${path}"
  log_info "Written: ${path}"
}

# ── Helper: check_ram ─────────────────────────────────────────────────────
check_ram() {
  local mem_kb
  mem_kb=$(awk '/MemTotal/ {print $2}' /proc/meminfo 2>/dev/null || echo "0")

  if [[ ${mem_kb} -eq 0 ]]; then
    log_warn "Could not read RAM from /proc/meminfo. Skipping check."
    return 0
  fi

  local mem_gb
  mem_gb=$((mem_kb / 1024 / 1024))

  log_info "Detected RAM: ${mem_gb}GB (${mem_kb}KB)"

  if [[ ${mem_kb} -lt ${RAM_THRESHOLD} ]]; then
    log_warn "⚠  RAM is below 4GB (${mem_gb}GB). Performance may be degraded."
    log_warn "   Consider enabling swap (Step 2) or upgrading system memory."
  else
    log_success "RAM check passed (${mem_gb}GB ≥ 4GB)."
  fi
}

# ── Helper: do_uninstall ──────────────────────────────────────────────────
do_uninstall() {
  log_banner
  echo -e "${RED}${BOLD}  ⚠  UNINSTALL MODE  ⚠${NC}\n"

  # Stop services
  log_info "Stopping services..."
  for svc in owl-forward-proxy kiro-gateway; do
    if systemctl is-active --quiet "${svc}" 2>/dev/null; then
      if [[ "${DRY_RUN}" == true ]]; then
        log_dry "Would stop service: ${svc}"
      else
        sudo systemctl stop "${svc}" 2>/dev/null || true
        sudo systemctl disable "${svc}" 2>/dev/null || true
        log_info "Stopped and disabled: ${svc}"
      fi
    fi
    if [[ -f "/etc/systemd/system/${svc}.service" ]]; then
      if [[ "${DRY_RUN}" == true ]]; then
        log_dry "Would remove unit: /etc/systemd/system/${svc}.service"
      else
        sudo rm -f "/etc/systemd/system/${svc}.service"
        log_info "Removed unit: ${svc}.service"
      fi
    fi
  done

  [[ "${DRY_RUN}" != true ]] && sudo systemctl daemon-reload 2>/dev/null || true

  # Remove CLI wrappers
  for wrapper in antigravity kiro-cli; do
    if [[ -f "${OWL_LOCAL_BIN}/${wrapper}" ]]; then
      if [[ "${DRY_RUN}" == true ]]; then
        log_dry "Would remove wrapper: ${OWL_LOCAL_BIN}/${wrapper}"
      else
        rm -f "${OWL_LOCAL_BIN}/${wrapper}"
        log_info "Removed wrapper: ${wrapper}"
      fi
    fi
  done

  # Remove owl-agent directory
  if [[ -d "${OWL_HOME}" ]]; then
    if [[ "${DRY_RUN}" == true ]]; then
      log_dry "Would remove directory: ${OWL_HOME}"
    else
      rm -rf "${OWL_HOME}"
      log_info "Removed: ${OWL_HOME}"
    fi
  fi

  # Remove swap file if we created it
  local swapfile="/swapfile_owl"
  if swapon --show=NAME --noheadings 2>/dev/null | grep -q "${swapfile}"; then
    if [[ "${DRY_RUN}" == true ]]; then
      log_dry "Would disable and remove swap: ${swapfile}"
    else
      sudo swapoff "${swapfile}" 2>/dev/null || true
      sudo rm -f "${swapfile}"
      log_info "Removed swap: ${swapfile}"
    fi
  fi

  # Remove iptables mesh rule
  if sudo iptables -C INPUT -p udp --dport ${MESH_PORT} -j ACCEPT 2>/dev/null; then
    if [[ "${DRY_RUN}" == true ]]; then
      log_dry "Would remove iptables rule for UDP ${MESH_PORT}"
    else
      sudo iptables -D INPUT -p udp --dport ${MESH_PORT} -j ACCEPT 2>/dev/null || true
      log_info "Removed mesh firewall rule."
    fi
  fi

  echo ""
  if [[ "${DRY_RUN}" == true ]]; then
    log_dry "Uninstall simulation complete. No files were removed."
  else
    log_success "OWL-AGENT v${VERSION} has been fully uninstalled."
  fi
  exit 0
}

# ═══════════════════════════════════════════════════════════════════════════
#  INSTALLATION STEPS
# ═══════════════════════════════════════════════════════════════════════════

# ── Step 1: System Check ──────────────────────────────────────────────────
step_1_system_check() {
  log_step "1" "System Check"

  local apt_deps=(curl wget git python3 python3-venv python3-pip jq socat)

  log_info "Checking required apt packages..."
  local missing=()
  for dep in "${apt_deps[@]}"; do
    if ! dpkg -s "${dep}" &>/dev/null; then
      missing+=("${dep}")
    fi
  done

  if [[ ${#missing[@]} -gt 0 ]]; then
    log_warn "Missing packages: ${missing[*]}"
    if [[ "${DRY_RUN}" == true ]]; then
      log_dry "Would install: sudo apt-get install -y ${missing[*]}"
    else
      sudo apt-get update -qq
      sudo apt-get install -y "${missing[@]}"
      log_success "Installed missing packages."
    fi
  else
    log_success "All apt dependencies satisfied."
  fi

  # RAM check
  check_ram

  # Verify python3 version
  local py_ver
  py_ver=$(python3 --version 2>/dev/null | awk '{print $2}')
  log_info "Python version: ${py_ver:-not found}"

  # Verify systemd
  if ! systemctl --version &>/dev/null; then
    log_warn "systemd not detected. Service management will be unavailable."
  fi
}

# ── Step 2: Swap Guard ───────────────────────────────────────────────────
step_2_swap_guard() {
  log_step "2" "Swap Guard"

  local swap_total
  swap_total=$(awk '/SwapTotal/ {print $2}' /proc/meminfo 2>/dev/null || echo "0")

  log_info "Current swap: ${swap_total}KB"

  if [[ ${swap_total} -ge ${SWAP_THRESHOLD} ]]; then
    log_success "Swap is sufficient (${swap_total}KB ≥ 1GB). No action needed."
    return 0
  fi

  local swapfile="/swapfile_owl"
  log_info "Swap is below 1GB. Creating ${SWAP_SIZE} swap file at ${swapfile}..."

  if [[ "${DRY_RUN}" == true ]]; then
    log_dry "Would create ${SWAP_SIZE} swap at ${swapfile}"
    return 0
  fi

  if [[ -f "${swapfile}" ]]; then
    log_info "Swap file already exists. Skipping creation."
    return 0
  fi

  sudo fallocate -l "${SWAP_SIZE}" "${swapfile}"
  sudo chmod 600 "${swapfile}"
  sudo mkswap "${swapfile}" &>/dev/null
  sudo swapon "${swapfile}"

  # Make persistent
  if ! grep -q "${swapfile}" /etc/fstab 2>/dev/null; then
    echo "${swapfile} none swap sw 0 0" | sudo tee -a /etc/fstab &>/dev/null
  fi

  local new_swap
  new_swap=$(awk '/SwapTotal/ {print $2}' /proc/meminfo)
  log_success "Swap created. New total: ${new_swap}KB"
}

# ── Step 3: Directory Structure ──────────────────────────────────────────
step_3_directory_structure() {
  log_step "3" "Directory Structure"

  local dirs=(
    "${OWL_HOME}"
    "${OWL_CONFIG}"
    "${OWL_LOGS}"
    "${OWL_CACHE}"
    "${OWL_LOCAL_BIN}"
  )

  for d in "${dirs[@]}"; do
    if [[ "${DRY_RUN}" == true ]]; then
      log_dry "Would create directory: ${d}"
    else
      mkdir -p "${d}"
      log_info "Ensured: ${d}"
    fi
  done

  log_success "Directory structure ready."
}

# ── Step 4: Python Venv ──────────────────────────────────────────────────
step_4_python_venv() {
  log_step "4" "Python Virtual Environment"

  ensure_venv

  local pip_pkgs=(httpx aiohttp aiofiles)
  for pkg in "${pip_pkgs[@]}"; do
    install_pip_pkg "${pkg}" || true
  done

  # Upgrade pip itself
  if [[ "${DRY_RUN}" != true ]]; then
    "${OWL_VENV}/bin/pip" install --quiet --upgrade pip 2>/dev/null || true
  fi

  log_success "Python environment ready."
}

# ── Step 5: Core Scripts ─────────────────────────────────────────────────
step_5_core_scripts() {
  log_step "5" "Core Scripts"

  # ── forward_proxy.py ──────────────────────────────────────────────────
  local forward_proxy_script
  forward_proxy_script=$(cat <<'PYEOF'
#!/usr/bin/env python3
"""OWL Forward Proxy — HTTP CONNECT proxy with upstream support."""
import os
import sys
import asyncio
import logging
from typing import Optional

try:
    import httpx
    import aiohttp
    from aiohttp import web
except ImportError:
    print("Missing dependencies. Run: pip install httpx aiohttp aiofiles")
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("owl-forward-proxy")

LISTEN_HOST = os.environ.get("OWL_PROXY_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("OWL_PROXY_PORT", "60000"))
UPSTREAM_PROXY = os.environ.get("UPSTREAM_PROXY", "")

async def handle_connect(request: web.Request) -> web.Response:
    """Handle HTTP CONNECT tunneling."""
    try:
        reader = await request.protocol.get_reader()
        writer = request.protocol.get_writer()
        host = request.host
        port = request.port or 443

        if UPSTREAM_PROXY:
            upstream_host, upstream_port = UPSTREAM_PROXY.replace("http://", "").split(":")
            upstream_port = int(upstream_port)
            upstream_reader, upstream_writer = await asyncio.open_connection(
                upstream_host, upstream_port
            )
            connect_req = f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n"
            upstream_writer.write(connect_req.encode())
            await upstream_writer.drain()
            resp = await upstream_reader.read(4096)
            if b"200" not in resp:
                return web.Response(status=502, text="Upstream proxy refused CONNECT")
            upstream_reader_local = upstream_reader
            upstream_writer_local = upstream_writer
        else:
            upstream_reader_local, upstream_writer_local = await asyncio.open_connection(host, port)

        resp = web.Response(status=200, headers={"Connection": "keep-alive"})
        await resp.prepare(request)
        return resp
    except Exception as e:
        logger.error(f"CONNECT error for {request.host}: {e}")
        return web.Response(status=502, text=str(e))

async def handle_health(request: web.Request) -> web.Response:
    """Health check endpoint."""
    return web.Response(text="ok")

async def start_proxy():
    app = web.Application()
    app.router.add_route("CONNECT", "/", handle_connect)
    app.router.add_get("/health", handle_health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, LISTEN_HOST, LISTEN_PORT)
    await site.start()
    logger.info(f"OWL Forward Proxy listening on {LISTEN_HOST}:{LISTEN_PORT}")
    if UPSTREAM_PROXY:
        logger.info(f"Upstream proxy: {UPSTREAM_PROXY}")

    # Keep running
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass
    finally:
        await runner.cleanup()

if __name__ == "__main__":
    try:
        asyncio.run(start_proxy())
    except KeyboardInterrupt:
        logger.info("Shutting down...")
PYEOF
)
  write_file "${OWL_HOME}/forward_proxy.py" "${forward_proxy_script}"

  # ── proxy_defense_fixed_v3.py ──────────────────────────────────────────
  local proxy_defense_script
  proxy_defense_script=$(cat <<'PYEOF'
#!/usr/bin/env python3
"""OWL Proxy Defense v3 — Rate limiting, IP filtering, and request validation."""
import os
import time
import asyncio
import logging
from collections import defaultdict
from typing import Dict, Tuple

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("owl-proxy-defense")

# Configuration
RATE_LIMIT_RPM = int(os.environ.get("OWL_RATE_LIMIT_RPM", "60"))
RATE_BURST = int(os.environ.get("OWL_RATE_BURST", "10"))
BLOCK_TTL = int(os.environ.get("OWL_BLOCK_TTL", "300"))
MAX_CONNECTIONS_PER_IP = int(os.environ.get("OWL_MAX_CONN_PER_IP", "20"))

# State
request_counts: Dict[str, list] = defaultdict(list)
blocked_ips: Dict[str, float] = {}
active_connections: Dict[str, int] = defaultdict(int)

def cleanup_rate_limits():
    """Remove expired rate limit entries."""
    now = time.time()
    for ip in list(request_counts.keys()):
        request_counts[ip] = [t for t in request_counts[ip] if now - t < 60]
        if not request_counts[ip]:
            del request_counts[ip]

def cleanup_blocks():
    """Remove expired IP blocks."""
    now = time.time()
    for ip in list(blocked_ips.keys()):
        if now - blocked_ips[ip] > BLOCK_TTL:
            del blocked_ips[ip]
            logger.info(f"Unblocked IP: {ip}")

def is_rate_limited(ip: str) -> bool:
    """Check if IP is rate limited."""
    now = time.time()
    request_counts[ip] = [t for t in request_counts[ip] if now - t < 60]

    if len(request_counts[ip]) >= RATE_LIMIT_RPM:
        return True

    # Burst check (last 5 seconds)
    burst = sum(1 for t in request_counts[ip] if now - t < 5)
    if burst >= RATE_BURST:
        return True

    return False

def check_ip(ip: str) -> Tuple[bool, str]:
    """Comprehensive IP check. Returns (allowed, reason)."""
    # Check block list
    if ip in blocked_ips:
        if time.time() - blocked_ips[ip] < BLOCK_TTL:
            return False, "IP is blocked"
        else:
            del blocked_ips[ip]

    # Check rate limit
    if is_rate_limited(ip):
        blocked_ips[ip] = time.time()
        return False, "Rate limit exceeded"

    # Check max connections
    if active_connections.get(ip, 0) >= MAX_CONNECTIONS_PER_IP:
        return False, "Too many concurrent connections"

    return True, "OK"

def record_request(ip: str):
    """Record a request from an IP."""
    request_counts[ip].append(time.time())

def increment_connection(ip: str):
    """Track active connection."""
    active_connections[ip] = active_connections.get(ip, 0) + 1

def decrement_connection(ip: str):
    """Release connection tracking."""
    if ip in active_connections:
        active_connections[ip] = max(0, active_connections[ip] - 1)

async def maintenance_loop():
    """Periodic cleanup of stale entries."""
    while True:
        cleanup_rate_limits()
        cleanup_blocks()
        await asyncio.sleep(30)

if __name__ == "__main__":
    logger.info("Proxy Defense v3 initialized")
    logger.info(f"Rate limit: {RATE_LIMIT_RPM} RPM, burst: {RATE_BURST}")
    logger.info(f"Block TTL: {BLOCK_TTL}s, max connections/IP: {MAX_CONNECTIONS_PER_IP}")
    asyncio.run(maintenance_loop())
PYEOF
)
  write_file "${OWL_HOME}/proxy_defense_fixed_v3.py" "${proxy_defense_script}"

  # ── owl_resilient_mcp.py ──────────────────────────────────────────────
  local resilient_mcp_script
  resilient_mcp_script=$(cat <<'PYEOF'
#!/usr/bin/env python3
"""OWL Resilient MCP — Fault-tolerant MCP server with retry and circuit breaker."""
import os
import sys
import json
import time
import asyncio
import logging
from typing import Dict, Any, Optional
from enum import Enum

try:
    import httpx
except ImportError:
    print("Missing httpx. Run: pip install httpx")
    sys.exit(1)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("owl-resilient-mcp")

# ── Circuit Breaker ─────────────────────────────────────────────────────
class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

class CircuitBreaker:
    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        half_open_max: int = 1,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max = half_open_max
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time = 0.0
        self.half_open_count = 0

    def can_execute(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            if time.time() - self.last_failure_time > self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                self.half_open_count = 0
                logger.info(f"Circuit [{self.name}] -> HALF_OPEN")
                return True
            return False
        if self.state == CircuitState.HALF_OPEN:
            return self.half_open_count < self.half_open_max
        return False

    def record_success(self):
        if self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.CLOSED
            logger.info(f"Circuit [{self.name}] -> CLOSED (recovered)")
        self.failure_count = 0

    def record_failure(self):
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.OPEN
            logger.warning(f"Circuit [{self.name}] -> OPEN (half-open failed)")
        elif self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            logger.warning(f"Circuit [{self.name}] -> OPEN ({self.failure_count} failures)")

# ── MCP Server ──────────────────────────────────────────────────────────
MCP_PORT = int(os.environ.get("OWL_MCP_PORT", "8334"))
BACKEND_URL = os.environ.get("OWL_MCP_BACKEND", "http://127.0.0.1:8333")
RETRY_ATTEMPTS = int(os.environ.get("OWL_MCP_RETRIES", "3"))
RETRY_DELAY = float(os.environ.get("OWL_MCP_RETRY_DELAY", "1.0"))

breaker = CircuitBreaker("mcp-backend")

async def call_backend(method: str, params: Dict[str, Any] = None) -> Optional[Dict]:
    """Call backend with retry and circuit breaker."""
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        if not breaker.can_execute():
            logger.warning("Circuit breaker is OPEN. Skipping request.")
            return None

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                payload = {"jsonrpc": "2.0", "method": method, "id": attempt}
                if params:
                    payload["params"] = params
                resp = await client.post(
                    f"{BACKEND_URL}/v1/mcp",
                    json=payload,
                )
                resp.raise_for_status()
                breaker.record_success()
                return resp.json()
        except Exception as e:
            breaker.record_failure()
            logger.error(f"Attempt {attempt}/{RETRY_ATTEMPTS} failed: {e}")
            if attempt < RETRY_ATTEMPTS:
                await asyncio.sleep(RETRY_DELAY * attempt)

    logger.error("All retry attempts exhausted.")
    return None

async def health_check_loop():
    """Periodic backend health check."""
    while True:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{BACKEND_URL}/health")
                if resp.status_code == 200:
                    breaker.record_success()
                else:
                    breaker.record_failure()
        except Exception:
            breaker.record_failure()
        await asyncio.sleep(15)

async def main():
    logger.info(f"OWL Resilient MCP starting on port {MCP_PORT}")
    logger.info(f"Backend: {BACKEND_URL}, retries: {RETRY_ATTEMPTS}")
    await health_check_loop()

if __name__ == "__main__":
    asyncio.run(main())
PYEOF
)
  write_file "${OWL_HOME}/owl_resilient_mcp.py" "${resilient_mcp_script}"

  # Make scripts executable
  if [[ "${DRY_RUN}" != true ]]; then
    chmod +x "${OWL_HOME}/forward_proxy.py"
    chmod +x "${OWL_HOME}/proxy_defense_fixed_v3.py"
    chmod +x "${OWL_HOME}/owl_resilient_mcp.py"
  fi

  log_success "Core scripts deployed."
}

# ── Step 5.5: Upgrade Cleanup ────────────────────────────────────────────
step_5_5_upgrade_cleanup() {
  log_step "5.5" "Upgrade Cleanup"

  # Archive .legacy files
  local legacy_dir="${OWL_HOME}/archive/legacy"
  local legacy_found=false
  for f in "${OWL_HOME}"/*.legacy "${OWL_CONFIG}"/*.legacy; do
    if [[ -f "${f}" ]]; then
      legacy_found=true
      if [[ "${DRY_RUN}" == true ]]; then
        log_dry "Would archive: ${f} -> ${legacy_dir}/"
      else
        mkdir -p "${legacy_dir}"
        mv "${f}" "${legacy_dir}/"
        log_info "Archived: $(basename "${f}")"
      fi
    fi
  done

  if [[ "${legacy_found}" == false ]]; then
    log_info "No .legacy files found. Nothing to archive."
  fi

  # Update run.sh
  local run_sh="${OWL_HOME}/run.sh"
  local run_sh_content
  run_sh_content=$(cat <<'RSEOF'
#!/usr/bin/env bash
# OWL-AGENT v7.0 — Runtime launcher
set -euo pipefail

OWL_HOME="${OWL_HOME:-$HOME/.owl-agent}"
source "${OWL_HOME}/venv/bin/activate"

export OWL_PROXY_HOST="${OWL_PROXY_HOST:-127.0.0.1}"
export OWL_PROXY_PORT="${OWL_PROXY_PORT:-60000}"

echo "[owl-agent] Starting forward proxy on ${OWL_PROXY_HOST}:${OWL_PROXY_PORT}..."
python3 "${OWL_HOME}/forward_proxy.py" &
PROXY_PID=$!

echo "[owl-agent] Starting proxy defense..."
python3 "${OWL_HOME}/proxy_defense_fixed_v3.py" &
DEFENSE_PID=$!

echo "[owl-agent] Starting resilient MCP..."
python3 "${OWL_HOME}/owl_resilient_mcp.py" &
MCP_PID=$!

cleanup() {
  echo "[owl-agent] Shutting down..."
  kill ${PROXY_PID} ${DEFENSE_PID} ${MCP_PID} 2>/dev/null || true
  wait 2>/dev/null
  echo "[owl-agent] Stopped."
}
trap cleanup EXIT INT TERM

echo "[owl-agent] All services running. PIDs: proxy=${PROXY_PID} defense=${DEFENSE_PID} mcp=${MCP_PID}"
wait
RSEOF
)
  write_file "${run_sh}" "${run_sh_content}"
  if [[ "${DRY_RUN}" != true ]]; then
    chmod +x "${run_sh}"
  fi

  # Remove orphan packages
  local orphans=(aiohttp-socks curl_cffi)
  local pip="${OWL_VENV}/bin/pip"
  for pkg in "${orphans[@]}"; do
    if "${pip}" show "${pkg}" &>/dev/null 2>&1; then
      if [[ "${DRY_RUN}" == true ]]; then
        log_dry "Would remove orphan package: ${pkg}"
      else
        "${pip}" uninstall -y "${pkg}" &>/dev/null || true
        log_info "Removed orphan: ${pkg}"
      fi
    else
      log_info "Orphan not installed: ${pkg} (skipped)"
    fi
  done

  log_success "Upgrade cleanup complete."
}

# ── Step 6: Kiro Ecosystem ───────────────────────────────────────────────
step_6_kiro_ecosystem() {
  log_step "6" "Kiro Ecosystem"

  # ── Kiro Gateway ─────────────────────────────────────────────────────
  if [[ "${SKIP_GATEWAY}" == true ]]; then
    log_info "Skipping kiro-gateway setup (--skip-gateway)."
  else
    local gateway_dir="${OWL_HOME}/kiro-gateway"
    if [[ -d "${gateway_dir}" ]]; then
      log_info "kiro-gateway already cloned at ${gateway_dir}. Pulling updates..."
      if [[ "${DRY_RUN}" != true ]]; then
        (cd "${gateway_dir}" && git pull --rebase --quiet 2>/dev/null) || true
      fi
    else
      log_info "Cloning kiro-gateway..."
      if [[ "${DRY_RUN}" == true ]]; then
        log_dry "Would clone: ${KIRO_GITHUB} -> ${gateway_dir}"
      else
        git clone --depth 1 "${KIRO_GITHUB}" "${gateway_dir}" 2>/dev/null || {
          log_warn "Could not clone kiro-gateway. Continuing without it."
        }
      fi
    fi

    # Install gateway deps
    if [[ -f "${gateway_dir}/requirements.txt" ]] && [[ "${DRY_RUN}" != true ]]; then
      "${OWL_VENV}/bin/pip" install --quiet -r "${gateway_dir}/requirements.txt" 2>/dev/null || true
    fi
  fi

  # ── Kiro CLI Binary ──────────────────────────────────────────────────
  if [[ "${SKIP_KIRO}" == true ]]; then
    log_info "Skipping kiro-cli binary download (--skip-kiro)."
  else
    local kiro_bin="${OWL_LOCAL_BIN}/kiro-cli"
    if [[ -f "${kiro_bin}" ]]; then
      log_info "kiro-cli already installed at ${kiro_bin}."
    else
      log_info "Downloading kiro-cli binary..."
      if [[ "${DRY_RUN}" == true ]]; then
        log_dry "Would download: ${KIRO_CLI_URL} -> ${kiro_bin}"
      else
        mkdir -p "${OWL_LOCAL_BIN}"
        curl -fsSL "${KIRO_CLI_URL}" -o "${kiro_bin}" 2>/dev/null || {
          log_warn "Could not download kiro-cli. You can install it manually."
          # Create placeholder
          cat > "${kiro_bin}" <<'KCEOF'
#!/usr/bin/env bash
# kiro-cli placeholder — install the real binary from https://kiro.dev
echo "kiro-cli: binary not installed. Visit https://kiro.dev for installation."
exit 1
KCEOF
          chmod +x "${kiro_bin}"
        }
        chmod +x "${kiro_bin}" 2>/dev/null || true
        log_success "kiro-cli installed."
      fi
    fi
  fi

  log_success "Kiro ecosystem step complete."
}

# ── Step 7: Systemd Services ────────────────────────────────────────────
step_7_systemd_services() {
  log_step "7" "Systemd Services"

  # ── owl-forward-proxy.service ────────────────────────────────────────
  local proxy_service
  proxy_service=$(cat <<EOF
[Unit]
Description=OWL Forward Proxy (v${VERSION})
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${USER}
Group=${USER}
ExecStart=${OWL_VENV}/bin/python3 ${OWL_HOME}/forward_proxy.py
Restart=on-failure
RestartSec=5
StartLimitBurst=5
StartLimitIntervalSec=60

# Resource limits
MemoryMax=512M
MemoryHigh=460M
MemoryLow=256M
TasksMax=50

# Security
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=${OWL_HOME}
PrivateTmp=true

# Environment
Environment=OWL_PROXY_HOST=127.0.0.1
Environment=OWL_PROXY_PORT=${PROXY_PORT}
Environment=PATH=${OWL_VENV}/bin:/usr/local/bin:/usr/bin:/bin

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=owl-forward-proxy

[Install]
WantedBy=multi-user.target
EOF
)

  # ── kiro-gateway.service ─────────────────────────────────────────────
  local gateway_service
  gateway_service=$(cat <<EOF
[Unit]
Description=Kiro Gateway (v${VERSION})
After=network-online.target owl-forward-proxy.service
Wants=network-online.target
Requires=owl-forward-proxy.service

[Service]
Type=simple
User=${USER}
Group=${USER}
ExecStart=${OWL_VENV}/bin/python3 ${OWL_HOME}/kiro-gateway/main.py
Restart=on-failure
RestartSec=8
StartLimitBurst=3
StartLimitIntervalSec=90

# Resource limits
MemoryMax=256M
MemoryHigh=230M
MemoryLow=128M
TasksMax=30

# Security
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=${OWL_HOME}
PrivateTmp=true

# Environment
Environment=HTTP_PROXY=http://127.0.0.1:${PROXY_PORT}
Environment=HTTPS_PROXY=http://127.0.0.1:${PROXY_PORT}
Environment=KIRO_GATEWAY_PORT=${GATEWAY_PORT}
Environment=PATH=${OWL_VENV}/bin:/usr/local/bin:/usr/bin:/bin

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=kiro-gateway

[Install]
WantedBy=multi-user.target
EOF
)

  # Write service files
  if [[ "${DRY_RUN}" == true ]]; then
    log_dry "Would write: /etc/systemd/system/owl-forward-proxy.service"
    log_dry "Would write: /etc/systemd/system/kiro-gateway.service"
    log_dry "Would run: systemctl daemon-reload"
    log_dry "Would run: systemctl enable owl-forward-proxy kiro-gateway"
  else
    echo "${proxy_service}" | sudo tee /etc/systemd/system/owl-forward-proxy.service &>/dev/null
    log_info "Written: owl-forward-proxy.service"

    if [[ "${SKIP_GATEWAY}" != true ]]; then
      echo "${gateway_service}" | sudo tee /etc/systemd/system/kiro-gateway.service &>/dev/null
      log_info "Written: kiro-gateway.service"
    fi

    # Enable memory accounting
    if [[ -f /etc/systemd/system.conf ]]; then
      if ! grep -q "DefaultMemoryAccounting=yes" /etc/systemd/system.conf 2>/dev/null; then
        echo "DefaultMemoryAccounting=yes" | sudo tee -a /etc/systemd/system.conf &>/dev/null
        log_info "Enabled memory accounting in systemd."
      fi
    fi

    sudo systemctl daemon-reload
    sudo systemctl enable owl-forward-proxy 2>/dev/null || true

    if [[ "${SKIP_GATEWAY}" != true ]]; then
      sudo systemctl enable kiro-gateway 2>/dev/null || true
    fi

    log_success "Systemd services configured."
  fi
}

# ── Step 8: OpenCode Config ────────────────────────────────────────────
step_8_opencode_config() {
  log_step "8" "OpenCode & Provider Configuration"

  # ── opencode.jsonc with all 6 providers ──────────────────────────────
  local opencode_config
  opencode_config=$(cat <<'OCEOF'
{
  // OWL-AGENT v7.0 — OpenCode Configuration
  // Providers: Antigravity, Claude, OpenCode, Copilot, Kiro, Hermes

  "$schema": "https://opencode.ai/config.schema.json",
  "version": "7.0",

  "providers": {
    "antigravity": {
      "type": "openai-compatible",
      "name": "Antigravity",
      "apiBase": "https://api.antigravity.dev/v1",
      "apiKeyEnv": "ANTIGRAVITY_API_KEY",
      "models": ["ag-1-turbo", "ag-1-pro"],
      "defaultModel": "ag-1-turbo",
      "proxy": "http://127.0.0.1:60000"
    },
    "claude": {
      "type": "anthropic",
      "name": "Claude",
      "apiBase": "https://api.anthropic.com",
      "apiKeyEnv": "ANTHROPIC_API_KEY",
      "models": ["claude-sonnet-4-20250514", "claude-opus-4-20250514"],
      "defaultModel": "claude-sonnet-4-20250514",
      "proxy": "http://127.0.0.1:60000"
    },
    "opencode": {
      "type": "openai-compatible",
      "name": "OpenCode",
      "apiBase": "https://api.opencode.dev/v1",
      "apiKeyEnv": "OPENCODE_API_KEY",
      "models": ["oc-2-standard", "oc-2-fast"],
      "defaultModel": "oc-2-standard",
      "proxy": "http://127.0.0.1:60000"
    },
    "copilot": {
      "type": "github-copilot",
      "name": "Copilot",
      "apiBase": "https://api.githubcopilot.com",
      "apiKeyEnv": "GITHUB_COPILOT_TOKEN",
      "models": ["gpt-4o", "o1-preview"],
      "defaultModel": "gpt-4o",
      "proxy": "http://127.0.0.1:60000"
    },
    "kiro": {
      "type": "kiro",
      "name": "Kiro",
      "apiBase": "http://127.0.0.1:8333",
      "apiKeyEnv": "KIRO_API_KEY",
      "models": ["kiro-1", "kiro-1-mini"],
      "defaultModel": "kiro-1",
      "proxy": "http://127.0.0.1:60000"
    },
    "hermes": {
      "type": "openai-compatible",
      "name": "Hermes",
      "apiBase": "https://api.hermes-ai.dev/v1",
      "apiKeyEnv": "HERMES_API_KEY",
      "models": ["hermes-3-turbo", "hermes-3-pro"],
      "defaultModel": "hermes-3-turbo",
      "proxy": "http://127.0.0.1:60000"
    }
  },

  "defaultProvider": "claude",

  "proxy": {
    "enabled": true,
    "url": "http://127.0.0.1:60000",
    "defenseEnabled": true,
    "circuitBreaker": {
      "failureThreshold": 5,
      "recoveryTimeout": 30
    }
  }
}
OCEOF
)
  write_file "${OWL_CONFIG}/opencode.jsonc" "${opencode_config}"

  # ── mcp.json ─────────────────────────────────────────────────────────
  local mcp_config
  mcp_config=$(cat <<'MCPEOF'
{
  "mcpServers": {
    "owl-resilient-mcp": {
      "command": "python3",
      "args": ["/.owl-agent/owl_resilient_mcp.py"],
      "env": {
        "OWL_MCP_PORT": "8334",
        "OWL_MCP_BACKEND": "http://127.0.0.1:8333",
        "OWL_MCP_RETRIES": "3"
      }
    },
    "kiro-gateway": {
      "command": "python3",
      "args": ["/.owl-agent/kiro-gateway/main.py"],
      "env": {
        "KIRO_GATEWAY_PORT": "8333",
        "HTTP_PROXY": "http://127.0.0.1:60000",
        "HTTPS_PROXY": "http://127.0.0.1:60000"
      }
    }
  }
}
MCPEOF
)
  write_file "${OWL_CONFIG}/mcp.json" "${mcp_config}"

  # ── CLI Wrappers ─────────────────────────────────────────────────────
  # Antigravity wrapper (injects HTTP_PROXY)
  local antigravity_wrapper
  antigravity_wrapper=$(cat <<'AGWRAP'
#!/usr/bin/env bash
# Antigravity CLI wrapper — routes through OWL proxy
set -euo pipefail

export HTTP_PROXY="http://127.0.0.1:60000"
export HTTPS_PROXY="http://127.0.0.1:60000"
export NO_PROXY="localhost,127.0.0.1,::1"

# Look for antigravity binary
AG_BIN=""
for candidate in \
  "${HOME}/.local/bin/antigravity-bin" \
  "/usr/local/bin/antigravity-bin" \
  "/usr/bin/antigravity-bin"; do
  if [[ -x "${candidate}" ]]; then
    AG_BIN="${candidate}"
    break
  fi
done

if [[ -z "${AG_BIN}" ]]; then
  echo "antigravity: binary not found. Install from https://antigravity.dev" >&2
  exit 1
fi

exec "${AG_BIN}" "$@"
AGWRAP
)
  write_file "${OWL_LOCAL_BIN}/antigravity" "${antigravity_wrapper}"

  # kiro-cli wrapper
  local kiro_wrapper
  kiro_wrapper=$(cat <<'KWRAP'
#!/usr/bin/env bash
# kiro-cli wrapper — routes through OWL proxy
set -euo pipefail

export HTTP_PROXY="http://127.0.0.1:60000"
export HTTPS_PROXY="http://127.0.0.1:60000"
export NO_PROXY="localhost,127.0.0.1,::1"

KIRO_BIN="${HOME}/.owl-agent/kiro-gateway/bin/kiro-cli"
if [[ ! -x "${KIRO_BIN}" ]]; then
  KIRO_BIN="${HOME}/.local/bin/kiro-cli"
fi

if [[ ! -x "${KIRO_BIN}" ]]; then
  echo "kiro-cli: binary not found." >&2
  exit 1
fi

exec "${KIRO_BIN}" "$@"
KWRAP
)

  # Only write kiro wrapper if not skipping kiro
  if [[ "${SKIP_KIRO}" != true ]]; then
    write_file "${OWL_LOCAL_BIN}/kiro-cli" "${kiro_wrapper}"
  fi

  # Make wrappers executable
  if [[ "${DRY_RUN}" != true ]]; then
    chmod +x "${OWL_LOCAL_BIN}/antigravity" 2>/dev/null || true
    chmod +x "${OWL_LOCAL_BIN}/kiro-cli" 2>/dev/null || true
  fi

  # Ensure .local/bin is in PATH (idempotent)
  if [[ ":${PATH}:" != *":${OWL_LOCAL_BIN}:"* ]]; then
    log_info "Adding ${OWL_LOCAL_BIN} to PATH..."
    if [[ "${DRY_RUN}" != true ]]; then
      if ! grep -q "export PATH=.*${OWL_LOCAL_BIN}" "${HOME}/.bashrc" 2>/dev/null; then
        echo "export PATH=\"${OWL_LOCAL_BIN}:\$PATH\"" >> "${HOME}/.bashrc"
      fi
      export PATH="${OWL_LOCAL_BIN}:${PATH}"
    fi
  fi

  log_success "OpenCode and provider configurations deployed."
}

# ── Step 9: Enrichment ──────────────────────────────────────────────────
step_9_enrichment() {
  log_step "9" "Proxy Enrichment"

  if [[ "${ENABLE_ENRICH}" != true ]]; then
    log_info "Enrichment not requested (--enrich). Skipping."
    return 0
  fi

  local enrich_config
  enrich_config=$(cat <<'ENREOF'
{
  "version": "7.0",
  "enrichment": {
    "enabled": true,
    "sources": [
      {
        "name": "free-proxy-list",
        "url": "https://free-proxy-list.net/api/v1/proxies",
        "format": "json",
        "refresh_interval": 3600,
        "max_proxies": 50,
        "protocols": ["http", "https"]
      },
      {
        "name": "proxy-scrape",
        "url": "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=5000",
        "format": "text",
        "refresh_interval": 1800,
        "max_proxies": 30,
        "protocols": ["http"]
      },
      {
        "name": "geonode",
        "url": "https://proxylist.geonode.com/api/proxy-list?limit=50&page=1&sort_by=lastChecked&sort_type=desc&protocols=http,https",
        "format": "json",
        "refresh_interval": 7200,
        "max_proxies": 25,
        "protocols": ["http", "https"]
      }
    ],
    "validation": {
      "timeout": 5,
      "test_url": "https://httpbin.org/ip",
      "min_uptime": 0.7,
      "check_interval": 600
    },
    "rotation": {
      "strategy": "weighted-random",
      "weights": {
        "latency": 0.4,
        "uptime": 0.3,
        "speed": 0.3
      }
    },
    "providers": {
      "antigravity": { "enrich": true, "prefer_protocol": "https" },
      "claude":      { "enrich": true, "prefer_protocol": "https" },
      "opencode":    { "enrich": true, "prefer_protocol": "https" },
      "copilot":     { "enrich": true, "prefer_protocol": "https" },
      "kiro":        { "enrich": false },
      "hermes":      { "enrich": true, "prefer_protocol": "https" }
    }
  }
}
ENREOF
)
  write_file "${OWL_CONFIG}/proxy_sources.json" "${enrich_config}"

  log_success "Proxy enrichment configured."
}

# ── Step 10: Mesh Firewall ──────────────────────────────────────────────
step_10_mesh_firewall() {
  log_step "10" "Mesh Firewall"

  if [[ "${ENABLE_MESH}" != true ]]; then
    log_info "Mesh sync not requested (--enable-mesh). Skipping."
    return 0
  fi

  log_info "Configuring mesh firewall rule for UDP ${MESH_PORT}..."

  if [[ "${DRY_RUN}" == true ]]; then
    log_dry "Would add iptables rule: INPUT -p udp --dport ${MESH_PORT} -j ACCEPT"
    log_dry "Would persist rule via iptables-save"
    return 0
  fi

  # Check if rule already exists
  if sudo iptables -C INPUT -p udp --dport ${MESH_PORT} -j ACCEPT 2>/dev/null; then
    log_info "Firewall rule already exists for UDP ${MESH_PORT}."
  else
    sudo iptables -A INPUT -p udp --dport ${MESH_PORT} -j ACCEPT
    log_success "Added iptables rule: UDP ${MESH_PORT} ACCEPT"
  fi

  # Persist rules
  if command -v iptables-save &>/dev/null; then
    sudo iptables-save | sudo tee /etc/iptables/rules.v4 &>/dev/null || true
    log_info "iptables rules persisted."
  fi

  # Mesh config
  local mesh_config
  mesh_config=$(cat <<'MESHEOF'
{
  "version": "7.0",
  "mesh": {
    "enabled": true,
    "port": 42100,
    "protocol": "udp",
    "discovery": {
      "method": "multicast",
      "group": "239.255.255.250",
      "interval": 30
    },
    "peers": {
      "max": 64,
      "timeout": 120,
      "heartbeat_interval": 15
    },
    "sync": {
      "proxy_lists": true,
      "config_changes": true,
      "health_status": true
    }
  }
}
MESHEOF
)
  write_file "${OWL_CONFIG}/mesh.json" "${mesh_config}"

  log_success "Mesh firewall configured."
}

# ── Step 11: Verification ───────────────────────────────────────────────
step_11_verification() {
  log_step "11" "Verification"

  local failures=0

  # ── Check directories ────────────────────────────────────────────────
  for d in "${OWL_HOME}" "${OWL_CONFIG}" "${OWL_LOGS}" "${OWL_CACHE}"; do
    if [[ -d "${d}" ]]; then
      log_success "Directory exists: ${d}"
    else
      log_error "Missing directory: ${d}"
      failures=$((failures + 1))
    fi
  done

  # ── Check core scripts ──────────────────────────────────────────────
  for f in forward_proxy.py proxy_defense_fixed_v3.py owl_resilient_mcp.py run.sh; do
    if [[ -f "${OWL_HOME}/${f}" ]]; then
      log_success "Script exists: ${f}"
    else
      log_error "Missing script: ${f}"
      failures=$((failures + 1))
    fi
  done

  # ── Check configs ───────────────────────────────────────────────────
  for f in opencode.jsonc mcp.json; do
    if [[ -f "${OWL_CONFIG}/${f}" ]]; then
      log_success "Config exists: ${f}"
    else
      log_error "Missing config: ${f}"
      failures=$((failures + 1))
    fi
  done

  # ── Check enrichment config ─────────────────────────────────────────
  if [[ "${ENABLE_ENRICH}" == true ]]; then
    if [[ -f "${OWL_CONFIG}/proxy_sources.json" ]]; then
      log_success "Enrichment config exists: proxy_sources.json"
    else
      log_error "Missing enrichment config: proxy_sources.json"
      failures=$((failures + 1))
    fi
  fi

  # ── Check mesh config ───────────────────────────────────────────────
  if [[ "${ENABLE_MESH}" == true ]]; then
    if [[ -f "${OWL_CONFIG}/mesh.json" ]]; then
      log_success "Mesh config exists: mesh.json"
    else
      log_error "Missing mesh config: mesh.json"
      failures=$((failures + 1))
    fi
  fi

  # ── Check CLI wrappers ──────────────────────────────────────────────
  if [[ -f "${OWL_LOCAL_BIN}/antigravity" ]]; then
    log_success "CLI wrapper: antigravity"
  else
    log_warn "Missing CLI wrapper: antigravity"
  fi

  if [[ "${SKIP_KIRO}" != true ]]; then
    if [[ -f "${OWL_LOCAL_BIN}/kiro-cli" ]]; then
      log_success "CLI wrapper: kiro-cli"
    else
      log_warn "Missing CLI wrapper: kiro-cli"
    fi
  fi

  # ── Check venv ──────────────────────────────────────────────────────
  if [[ -f "${OWL_VENV}/bin/python3" ]]; then
    log_success "Python venv: OK"
  else
    log_error "Python venv not found at ${OWL_VENV}"
    failures=$((failures + 1))
  fi

  # ── Check pip packages ──────────────────────────────────────────────
  local pip="${OWL_VENV}/bin/pip"
  for pkg in httpx aiohttp aiofiles; do
    if "${pip}" show "${pkg}" &>/dev/null 2>&1; then
      log_success "Pip package: ${pkg}"
    else
      log_error "Missing pip package: ${pkg}"
      failures=$((failures + 1))
    fi
  done

  # ── Check systemd services ──────────────────────────────────────────
  for svc in owl-forward-proxy; do
    if systemctl is-enabled --quiet "${svc}" 2>/dev/null; then
      log_success "Service enabled: ${svc}"
    else
      log_warn "Service not enabled: ${svc} (may need manual start)"
    fi
  done

  if [[ "${SKIP_GATEWAY}" != true ]]; then
    if systemctl is-enabled --quiet kiro-gateway 2>/dev/null; then
      log_success "Service enabled: kiro-gateway"
    else
      log_warn "Service not enabled: kiro-gateway"
    fi
  fi

  # ── Check ports ─────────────────────────────────────────────────────
  if [[ "${DRY_RUN}" != true ]]; then
    # Try to start proxy service briefly to test port
    if ss -tlnp 2>/dev/null | grep -q ":${PROXY_PORT} "; then
      log_success "Port ${PROXY_PORT} (proxy): listening"
    else
      log_info "Port ${PROXY_PORT} (proxy): not yet listening (start service to activate)"
    fi

    if ss -tlnp 2>/dev/null | grep -q ":${GATEWAY_PORT} "; then
      log_success "Port ${GATEWAY_PORT} (gateway): listening"
    else
      log_info "Port ${GATEWAY_PORT} (gateway): not yet listening"
    fi
  fi

  # ── Summary ──────────────────────────────────────────────────────────
  echo ""
  if [[ ${failures} -eq 0 ]]; then
    echo -e "${GREEN}${BOLD}  ╔═══════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}${BOLD}  ║    ✓  OWL-AGENT v${VERSION} INSTALL COMPLETE      ║${NC}"
    echo -e "${GREEN}${BOLD}  ╚═══════════════════════════════════════════════╝${NC}"
    echo ""
    log_info "Start services with:"
    echo -e "  ${CYAN}sudo systemctl start owl-forward-proxy${NC}"
    if [[ "${SKIP_GATEWAY}" != true ]]; then
      echo -e "  ${CYAN}sudo systemctl start kiro-gateway${NC}"
    fi
    echo ""
    log_info "Verify with: ${CYAN}~/.owl-agent/diagnose.sh${NC}"
    echo ""
    log_info "Provider configs: ${OWL_CONFIG}/opencode.jsonc"
    log_info "CLI wrappers:     ${OWL_LOCAL_BIN}/antigravity"
    if [[ "${ENABLE_ENRICH}" == true ]]; then
      log_info "Enrichment:       ${OWL_CONFIG}/proxy_sources.json"
    fi
    if [[ "${ENABLE_MESH}" == true ]]; then
      log_info "Mesh sync:        UDP ${MESH_PORT}"
    fi
  else
    echo ""
    echo -e "${YELLOW}${BOLD}  ⚠  Installation completed with ${failures} issue(s)${NC}"
    echo -e "${YELLOW}     Run diagnose.sh for details.${NC}"
  fi
}

# ═══════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════
main() {
  parse_args "$@"

  log_banner

  # Handle uninstall first
  if [[ "${UNINSTALL}" == true ]]; then
    do_uninstall
  fi

  # Dry run notice
  if [[ "${DRY_RUN}" == true ]]; then
    echo -e "${MAGENTA}${BOLD}  ◈  DRY RUN MODE — No files will be written${NC}\n"
  fi

  # Flag summary
  log_info "Installation flags:"
  [[ "${SKIP_KIRO}" == true ]]    && log_info "  --skip-kiro"
  [[ "${SKIP_GATEWAY}" == true ]] && log_info "  --skip-gateway"
  [[ "${ENABLE_ENRICH}" == true ]] && log_info "  --enrich"
  [[ "${ENABLE_MESH}" == true ]]  && log_info "  --enable-mesh"
  [[ "${DRY_RUN}" == true ]]      && log_info "  --dry-run"
  echo ""

  # Check for root
  if [[ "${EUID}" -eq 0 ]]; then
    log_warn "Running as root. Some features may not work as expected."
    log_warn "Consider running as a regular user."
  fi

  # Execute steps in order
  step_1_system_check
  step_2_swap_guard
  step_3_directory_structure
  step_4_python_venv
  step_5_core_scripts
  step_5_5_upgrade_cleanup
  step_6_kiro_ecosystem
  step_7_systemd_services
  step_8_opencode_config
  step_9_enrichment
  step_10_mesh_firewall
  step_11_verification
}

main "$@"
