#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
#  OWL-AGENT v7.1 — Diagnostics v3.0
#  5-Section Health Check: Service · Connectivity · Environment · Resources · Auto-Tune
#
#  v7.1 changes:
#   - DELETED --fix mode. Diagnostics report; humans fix.
#     The v7.0 --fix mode ran commands as root with `2>/dev/null` swallowing
#     all errors — false confidence, no debugging. v7.1 prints the exact
#     command to run instead.
#   - DELETED httpbin.org dependency. Connectivity check uses the proxy's
#     own /health endpoint.
#   - DELETED `rg` dependency. Falls back to `grep` when ripgrep isn't
#     installed.
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

readonly VERSION="3.0"
readonly OWL_HOME="${OWL_HOME:-$HOME/.owl-agent}"
readonly PROXY_PORT=60000
readonly GATEWAY_PORT=8333
readonly MESH_PORT=42100

# ── Colors ─────────────────────────────────────────────────────────────────
readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[0;33m'
readonly CYAN='\033[0;36m'
readonly BOLD='\033[1m'
readonly DIM='\033[2m'
readonly NC='\033[0m'

# ── Flags ──────────────────────────────────────────────────────────────────
VERBOSE=false
FAILURES=0

# ── Logging ────────────────────────────────────────────────────────────────
pass() { echo -e "  ${GREEN}✓${NC} $*"; }
fail() { echo -e "  ${RED}✗${NC} $*"; FAILURES=$((FAILURES + 1)); }
warn() { echo -e "  ${YELLOW}⚠${NC} $*"; }
info() { echo -e "  ${CYAN}›${NC} $*"; }
header() { echo -e "\n${BOLD}${CYAN}══ $1 ══${NC}\n"; }

# ── Usage ──────────────────────────────────────────────────────────────────
usage() {
  cat <<EOF
OWL-AGENT Diagnostics v${VERSION}

USAGE:
  $(basename "$0") [OPTIONS]

OPTIONS:
  --verbose    Show detailed output (logs, full JSON responses)
  -h, --help   Show this help message

NOTE:
  v7.1 no longer has a --fix mode. When an issue is detected, the
  suggested command is printed for you to run manually.
EOF
  exit 0
}

# ── Parse Arguments ────────────────────────────────────────────────────────
parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --verbose) VERBOSE=true; shift ;;
      -h|--help) usage ;;
      --fix)     echo "--fix mode has been removed in v7.1. Diagnostics report; humans fix." >&2; exit 2 ;;
      *) echo "Unknown option: $1"; usage ;;
    esac
  done
}

# ── Suggest fix helper (replaces v7.0 try_fix) ────────────────────────────
suggest_fix() {
  local desc="$1"
  local cmd="$2"
  echo -e "  ${YELLOW}→ Suggested fix:${NC} ${desc}"
  echo -e "    ${DIM}\$ ${cmd}${NC}"
}

# ── Search helper: use rg if available, otherwise grep ────────────────────
search() {
  local pattern="$1"
  local file="$2"
  if command -v rg &>/dev/null; then
    rg -c "${pattern}" "${file}" 2>/dev/null || echo "0"
  else
    grep -cE "${pattern}" "${file}" 2>/dev/null || echo "0"
  fi
}

search_lines() {
  local pattern="$1"
  local file="$2"
  if command -v rg &>/dev/null; then
    rg "${pattern}" "${file}" 2>/dev/null | tail -5
  else
    grep -E "${pattern}" "${file}" 2>/dev/null | tail -5
  fi
}

# ═══════════════════════════════════════════════════════════════════════════
#  SECTION 1: Service Status
# ═══════════════════════════════════════════════════════════════════════════
section_service_status() {
  header "1. Service Status"

  if systemctl is-active --quiet owl-forward-proxy 2>/dev/null; then
    pass "owl-forward-proxy service: active"
  else
    fail "owl-forward-proxy service: NOT running"
    suggest_fix "start the proxy service" "sudo systemctl start owl-forward-proxy"
  fi

  if systemctl is-enabled --quiet owl-forward-proxy 2>/dev/null; then
    pass "owl-forward-proxy: enabled on boot"
  else
    warn "owl-forward-proxy: not enabled on boot"
    suggest_fix "enable on boot" "sudo systemctl enable owl-forward-proxy"
  fi

  if systemctl is-active --quiet kiro-gateway 2>/dev/null; then
    pass "kiro-gateway service: active"
  else
    warn "kiro-gateway service: NOT running (may be --skip-gateway)"
    suggest_fix "start the gateway (if installed)" "sudo systemctl start kiro-gateway"
  fi

  if ss -tlnp 2>/dev/null | grep -q ":${PROXY_PORT} "; then
    pass "Port ${PROXY_PORT} (proxy): listening"
  else
    fail "Port ${PROXY_PORT} (proxy): NOT listening"
  fi

  if ss -tlnp 2>/dev/null | grep -q ":${GATEWAY_PORT} "; then
    pass "Port ${GATEWAY_PORT} (gateway): listening"
  else
    warn "Port ${GATEWAY_PORT} (gateway): NOT listening"
  fi

  if pgrep -f "forward_proxy.py" &>/dev/null; then
    pass "forward_proxy.py process: running (PID $(pgrep -f "forward_proxy.py" | head -1))"
  else
    fail "forward_proxy.py process: NOT running"
  fi

  if pgrep -f "kiro-gateway" &>/dev/null; then
    pass "kiro-gateway process: running"
  else
    warn "kiro-gateway process: NOT running"
  fi

  if [[ "${VERBOSE}" == true ]]; then
    echo -e "\n  ${DIM}── Recent owl-forward-proxy logs ──${NC}"
    sudo journalctl -u owl-forward-proxy --no-pager -n 10 2>/dev/null || echo "  (no logs available)"
    echo -e "\n  ${DIM}── Recent kiro-gateway logs ──${NC}"
    sudo journalctl -u kiro-gateway --no-pager -n 10 2>/dev/null || echo "  (no logs available)"
  fi
}

# ═══════════════════════════════════════════════════════════════════════════
#  SECTION 2: Connectivity (no external httpbin.org dependency)
# ═══════════════════════════════════════════════════════════════════════════
section_connectivity() {
  header "2. Connectivity"

  # ── Proxy health endpoint (local, no external dependency) ────────────
  local health_resp
  health_resp=$(curl -s --connect-timeout 3 "http://127.0.0.1:${PROXY_PORT}/health" 2>/dev/null || echo "")
  if echo "${health_resp}" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['status']=='ok'" 2>/dev/null; then
    pass "Proxy /health: OK"
    if [[ "${VERBOSE}" == true ]]; then
      echo -e "  ${DIM}$(echo "${health_resp}" | python3 -m json.tool 2>/dev/null)${NC}"
    fi
  else
    fail "Proxy /health: not responding or malformed (got: '${health_resp:0:80}')"
    suggest_fix "check proxy is running" "sudo systemctl status owl-forward-proxy"
  fi

  # ── Proxy forwarding test via allowed domain ─────────────────────────
  # Use an allowlisted domain so the SSRF guard doesn't reject the probe.
  local proxy_url="http://127.0.0.1:${PROXY_PORT}"
  local probe_code
  probe_code=$(curl -x "${proxy_url}" -s -o /dev/null -w "%{http_code}" \
               --connect-timeout 5 -m 10 \
               https://api.anthropic.com/ 2>/dev/null || echo "000")
  if [[ "${probe_code}" =~ ^[2-4][0-9]{2}$ ]]; then
    pass "Proxy forwarding: working (probe returned ${probe_code})"
  else
    fail "Proxy forwarding: not working (probe returned ${probe_code})"
  fi

  # ── Gateway /v1/models ───────────────────────────────────────────────
  local models_resp
  models_resp=$(curl -s --connect-timeout 5 "http://127.0.0.1:${GATEWAY_PORT}/v1/models" 2>/dev/null || echo "")
  if [[ -n "${models_resp}" ]] && echo "${models_resp}" | python3 -c "import sys,json; json.load(sys.stdin)" 2>/dev/null; then
    pass "Gateway /v1/models: responding with valid JSON"
    if [[ "${VERBOSE}" == true ]]; then
      echo -e "  ${DIM}$(echo "${models_resp}" | python3 -m json.tool 2>/dev/null | head -20)${NC}"
    fi
  else
    warn "Gateway /v1/models: not available (gateway may not be running)"
  fi

  # ── Direct internet ──────────────────────────────────────────────────
  if curl -s -o /dev/null -w "%{http_code}" --connect-timeout 5 -m 8 https://www.google.com 2>/dev/null | grep -qE "2[0-9]{2}|3[0-9]{2}"; then
    pass "Direct internet: reachable"
  else
    warn "Direct internet: may be blocked (expected if proxy-required network)"
  fi

  # ── DNS ──────────────────────────────────────────────────────────────
  if host api.anthropic.com &>/dev/null; then
    pass "DNS resolution: working (api.anthropic.com)"
  else
    fail "DNS resolution: FAILED for api.anthropic.com"
    suggest_fix "check /etc/resolv.conf" "cat /etc/resolv.conf"
  fi
}

# ═══════════════════════════════════════════════════════════════════════════
#  SECTION 3: Environment
# ═══════════════════════════════════════════════════════════════════════════
section_environment() {
  header "3. Environment Variables"

  local proxy_vars=(HTTP_PROXY HTTPS_PROXY UPSTREAM_PROXY NO_PROXY)
  for var in "${proxy_vars[@]}"; do
    local val="${!var:-<unset>}"
    if [[ "${val}" != "<unset>" ]]; then
      pass "${var}=${val}"
    else
      if [[ "${var}" == "NO_PROXY" ]]; then
        info "${var}=<unset> (optional)"
      else
        warn "${var}=<unset>"
      fi
    fi
  done

  local api_vars=(ANTIGRAVITY_API_KEY ANTHROPIC_API_KEY OPENCODE_API_KEY GITHUB_COPILOT_TOKEN KIRO_API_KEY HERMES_API_KEY)
  echo -e "\n  ${DIM}── API Keys ──${NC}"
  for var in "${api_vars[@]}"; do
    local val="${!var:-<unset>}"
    if [[ "${val}" != "<unset>" ]]; then
      local masked
      if [[ ${#val} -ge 8 ]]; then
        masked="${val:0:4}...${val: -4}"
      else
        masked="${val:0:2}***"
      fi
      pass "${var}=${masked} (set)"
    else
      warn "${var}=<unset>"
    fi
  done

  local owl_vars=(OWL_HOME OWL_PROXY_HOST OWL_PROXY_PORT OWL_PROXY_TOKEN OWL_MAX_CONNECTIONS OWL_ENABLE_MESH OWL_MESH_PORT OWL_ALLOW_EXTRA)
  echo -e "\n  ${DIM}── OWL Configuration ──${NC}"
  for var in "${owl_vars[@]}"; do
    local val="${!var:-<default>}"
    if [[ "${var}" == "OWL_PROXY_TOKEN" && -n "${!var:-}" ]]; then
      val="<set, length=${#val}>"
    fi
    info "${var}=${val}"
  done

  if [[ ":${PATH}:" == *":${HOME}/.local/bin:"* ]]; then
    pass "~/.local/bin in PATH"
  else
    warn "~/.local/bin NOT in PATH (CLI wrappers won't be found)"
    suggest_fix "add to PATH (one-time)" "echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.bashrc && source ~/.bashrc"
  fi
}

# ═══════════════════════════════════════════════════════════════════════════
#  SECTION 4: Resources
# ═══════════════════════════════════════════════════════════════════════════
section_resources() {
  header "4. Resources"

  local mem_total mem_available mem_used mem_percent
  mem_total=$(awk '/MemTotal/ {print $2}' /proc/meminfo)
  mem_available=$(awk '/MemAvailable/ {print $2}' /proc/meminfo)
  mem_used=$((mem_total - mem_available))
  mem_percent=$((mem_used * 100 / mem_total))

  local mem_total_gb=$((mem_total / 1024 / 1024))
  local mem_avail_gb=$((mem_available / 1024 / 1024))

  info "RAM: ${mem_total_gb}GB total, ${mem_avail_gb}GB available (${mem_percent}% used)"

  if [[ ${mem_total} -lt $((4 * 1024 * 1024)) ]]; then
    warn "RAM is below 4GB. Services may be memory-constrained."
  else
    pass "RAM is 4GB or above"
  fi

  if [[ ${mem_percent} -gt 90 ]]; then
    fail "Memory usage above 90%! Services may crash."
    suggest_fix "free memory or restart services" "sudo systemctl restart owl-forward-proxy"
  elif [[ ${mem_percent} -gt 75 ]]; then
    warn "Memory usage above 75%. Monitor closely."
  else
    pass "Memory usage is healthy"
  fi

  local swap_total swap_free
  swap_total=$(awk '/SwapTotal/ {print $2}' /proc/meminfo)
  swap_free=$(awk '/SwapFree/ {print $2}' /proc/meminfo)
  local swap_total_mb=$((swap_total / 1024))

  if [[ ${swap_total} -eq 0 ]]; then
    warn "No swap configured. Consider adding swap for stability."
    suggest_fix "add 2GB swap" "sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile"
  else
    info "Swap: ${swap_total_mb}MB total, $((swap_free / 1024))MB free"
    pass "Swap is configured"
  fi

  local owl_disk_info
  owl_disk_info=$(df -h "${OWL_HOME}" 2>/dev/null | tail -1 || echo "")
  if [[ -n "${owl_disk_info}" ]]; then
    local disk_use disk_avail
    disk_use=$(echo "${owl_disk_info}" | awk '{print $5}')
    disk_avail=$(echo "${owl_disk_info}" | awk '{print $4}')
    info "Disk (${OWL_HOME}): ${disk_use} used, ${disk_avail} available"

    local disk_pct
    disk_pct=$(echo "${disk_use}" | tr -d '%')
    if [[ ${disk_pct} -gt 90 ]]; then
      fail "Disk usage above 90%!"
    elif [[ ${disk_pct} -gt 80 ]]; then
      warn "Disk usage above 80%."
    else
      pass "Disk usage is healthy"
    fi
  fi

  if [[ "${VERBOSE}" == true ]]; then
    echo -e "\n  ${DIM}── /proc/meminfo (selected) ──${NC}"
    for key in MemTotal MemFree MemAvailable Buffers Cached SwapTotal SwapFree Shmem; do
      local val
      val=$(awk "/^${key}:/ {print \$2, \$3}" /proc/meminfo)
      info "${key}: ${val}"
    done
  fi

  echo -e "\n  ${DIM}── Service Memory Usage ──${NC}"
  for svc in owl-forward-proxy kiro-gateway; do
    local pid
    pid=$(systemctl show "${svc}" --property=MainPID --value 2>/dev/null || echo "0")
    if [[ "${pid}" != "0" ]] && [[ -d "/proc/${pid}" ]]; then
      local rss
      rss=$(awk '/VmRSS/ {print $2}' "/proc/${pid}/status" 2>/dev/null || echo "0")
      info "${svc}: PID=${pid}, RSS=$((rss / 1024))MB"
    else
      info "${svc}: not running"
    fi
  done
}

# ═══════════════════════════════════════════════════════════════════════════
#  SECTION 5: Auto-Tune Status
# ═══════════════════════════════════════════════════════════════════════════
section_auto_tune() {
  header "5. Auto-Tune Status"

  local log_dir="${OWL_HOME}/logs"
  local tune_log="${log_dir}/auto-tuner.log"
  local proxy_log="${log_dir}/forward_proxy.log"

  if [[ -f "${tune_log}" ]]; then
    pass "Auto-tuner log found: ${tune_log}"

    local recent
    recent=$(tail -20 "${tune_log}" 2>/dev/null || echo "")
    if [[ -n "${recent}" ]]; then
      info "Recent auto-tuner log lines:"
      echo "${recent}" | while IFS= read -r line; do
        echo -e "    ${DIM}${line}${NC}"
      done
    fi

    local adjust_count
    adjust_count=$(search "adjustment|tuned|throttle|rate_limit|circuit" "${tune_log}")
    info "Total adjustment-related lines: ${adjust_count}"

    local error_count
    error_count=$(search "ERROR|CRITICAL|panic" "${tune_log}")
    if [[ ${error_count} -gt 0 ]]; then
      warn "Auto-tuner has ${error_count} error entries"
      if [[ "${VERBOSE}" == true ]]; then
        search_lines "ERROR|CRITICAL|panic" "${tune_log}" | while IFS= read -r line; do
          echo -e "    ${RED}${line}${NC}"
        done
      fi
    else
      pass "No critical errors in auto-tuner log"
    fi
  else
    warn "Auto-tuner log not found at ${tune_log}"
    info "Auto-tuning is handled internally by forward_proxy.py (AutoTuner class)"
  fi

  if [[ -f "${proxy_log}" ]]; then
    local rl_count
    rl_count=$(search "rate.limit|blocked|circuit.open|throttl" "${proxy_log}")
    if [[ ${rl_count} -gt 0 ]]; then
      warn "Rate-limit/block events: ${rl_count} (check if expected)"
      if [[ "${VERBOSE}" == true ]]; then
        search_lines "rate.limit|blocked|circuit.open" "${proxy_log}" | while IFS= read -r line; do
          echo -e "    ${YELLOW}${line}${NC}"
        done
      fi
    else
      pass "No rate-limit or block events in proxy log"
    fi
  else
    info "Proxy log not found at ${proxy_log} (may use journald instead)"
    local journal_rl
    journal_rl=$(sudo journalctl -u owl-forward-proxy --no-pager -n 100 2>/dev/null \
                  | grep -cE "rate.limit|blocked|circuit.open" 2>/dev/null || echo "0")
    if [[ ${journal_rl} -gt 0 ]]; then
      warn "Rate-limit events in journal: ${journal_rl}"
    else
      pass "No rate-limit events found in recent journal"
    fi
  fi

  local defense_script="${OWL_HOME}/proxy_defense_fixed_v3.py"
  if [[ -f "${defense_script}" ]]; then
    local current_rpm current_burst current_block_ttl
    current_rpm=$(grep -oE 'RATE_LIMIT_RPM[^=]*=[^0-9]*([0-9]+)' "${defense_script}" 2>/dev/null | grep -oE '[0-9]+' | tail -1 || echo "?")
    current_burst=$(grep -oE 'RATE_BURST[^=]*=[^0-9]*([0-9]+)' "${defense_script}" 2>/dev/null | grep -oE '[0-9]+' | tail -1 || echo "?")
    current_block_ttl=$(grep -oE 'BLOCK_TTL[^=]*=[^0-9]*([0-9]+)' "${defense_script}" 2>/dev/null | grep -oE '[0-9]+' | tail -1 || echo "?")
    info "Current defense params: RPM=${current_rpm}, Burst=${current_burst}, BlockTTL=${current_block_ttl}s"
  else
    warn "proxy_defense_fixed_v3.py not found"
  fi
}

# ═══════════════════════════════════════════════════════════════════════════
#  SUMMARY
# ═══════════════════════════════════════════════════════════════════════════
print_summary() {
  echo ""
  echo -e "${BOLD}${CYAN}═══════════════════════════════════════════════════════${NC}"
  echo -e "${BOLD}  DIAGNOSTICS COMPLETE${NC}"

  if [[ ${FAILURES} -eq 0 ]]; then
    echo -e "  ${GREEN}All checks passed. No issues found.${NC}"
  else
    echo -e "  ${RED}${FAILURES} issue(s) detected.${NC}"
    echo -e "  ${YELLOW}Review the suggested fix commands above.${NC}"
  fi

  echo -e "${BOLD}${CYAN}═══════════════════════════════════════════════════════${NC}"
  echo ""
}

# ═══════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════
main() {
  parse_args "$@"

  echo -e "${BOLD}${CYAN}"
  echo "  ╔═══════════════════════════════════════════════╗"
  echo "  ║     OWL-AGENT Diagnostics v${VERSION}              ║"
  echo "  ║     Service · Connect · Env · Resource · Tune ║"
  echo "  ╚═══════════════════════════════════════════════╝"
  echo -e "${NC}"

  if [[ "${VERBOSE}" == true ]]; then
    info "Verbose mode: ON"
  fi

  section_service_status
  section_connectivity
  section_environment
  section_resources
  section_auto_tune

  print_summary
}

main "$@"
