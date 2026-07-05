export default function Home() {
  return (
    <>
      {/* Hero */}
      <section className="hero">
        <div className="container">
          <div className="hero-owl animate-in">🦉</div>
          <h1 className="animate-in delay-1">OWL-AGENT</h1>
          <h2 className="animate-in delay-2">Free AI Proxy Gateway — Unified Synergy Installer v7.1.1</h2>
          <p className="hero-tagline animate-in delay-3">
            <strong>One gateway. All AI models. Zero cost.</strong><br />
            Free-tier proxy access for Antigravity, Claude, OpenCode, Copilot, Kiro &amp; Hermes
          </p>
          <div className="badges animate-in delay-3">
            <span className="badge badge-green">v7.1.1</span>
            <span className="badge badge-blue">Linux Ubuntu</span>
            <span className="badge badge-purple">Podman</span>
            <span className="badge badge-red">MCP Compatible</span>
          </div>
          <div className="cta-group animate-in delay-4">
            <a href="https://github.com/marktantongco/owl-agent-free-ai-proxy-gateway" className="btn btn-primary">
              ⭐ Star on GitHub
            </a>
            <a href="#install" className="btn btn-secondary">
              🚀 Quick Install
            </a>
            <a href="https://github.com/marktantongco/owl-agent-free-ai-proxy-gateway/wiki" className="btn btn-outline">
              📖 Documentation
            </a>
          </div>
        </div>
      </section>

      {/* v7.1.1 Patch Release */}
      <section className="features">
        <div className="container">
          <p className="section-label">Patch Release</p>
          <h2 className="section-title">v7.1.1 — Hardening Update</h2>
          <div className="feature-card" style={{ maxWidth: '100%' }}>
            <h3>🛡️ v7.1.1 patch release</h3>
            <p style={{ marginBottom: '1rem' }}>
              Patch release on top of v7.1. Hardens the proxy defense layer, removes a dead-code path, and tightens the container entrypoint.
            </p>
            <ul style={{ margin: '1rem 0', paddingLeft: '1.5rem', color: 'var(--muted)', lineHeight: 1.8 }}>
              <li>
                <strong style={{ color: 'var(--fg)' }}>proxy_defense hardening</strong> — 14 P0 audit fixes applied to{' '}
                <code style={{ background: 'rgba(0,240,255,0.1)', color: 'var(--accent2)', padding: '2px 6px', borderRadius: '4px', fontSize: '0.85rem' }}>proxy_defense_fixed_v3.py</code>{' '}
                with full regression-test coverage.
              </li>
              <li>
                <strong style={{ color: 'var(--fg)' }}>OfflineQueue deletion</strong> — removed the dead{' '}
                <code style={{ background: 'rgba(0,240,255,0.1)', color: 'var(--accent2)', padding: '2px 6px', borderRadius: '4px', fontSize: '0.85rem' }}>OfflineQueue</code>{' '}
                class and its unused call sites; queue logic is now bounded LRU only.
              </li>
              <li>
                <strong style={{ color: 'var(--fg)' }}>entrypoint security guards</strong> —{' '}
                <code style={{ background: 'rgba(0,240,255,0.1)', color: 'var(--accent2)', padding: '2px 6px', borderRadius: '4px', fontSize: '0.85rem' }}>entrypoint.sh</code>{' '}
                now drops to non-root, validates env vars, and fails fast on missing secrets.
              </li>
              <li>
                <strong style={{ color: 'var(--fg)' }}>Redis removal from compose</strong> —{' '}
                <code style={{ background: 'rgba(0,240,255,0.1)', color: 'var(--accent2)', padding: '2px 6px', borderRadius: '4px', fontSize: '0.85rem' }}>podman-compose.yml</code>{' '}
                no longer boots a Redis sidecar; mesh sync uses TCP gossip by default.
              </li>
            </ul>
            <p style={{ marginTop: '1rem', fontSize: '1rem' }}>
              <strong style={{ color: 'var(--fg)' }}>24 P0 bugs fixed · 482 net lines removed · 97% slimmer package</strong>
            </p>
            <p style={{ marginTop: '1.25rem' }}>
              <a
                href="https://github.com/marktantongco/owl-agent-free-ai-proxy-gateway/releases/tag/v7.1.1"
                className="btn btn-secondary"
                style={{ display: 'inline-flex' }}
              >
                📦 View v7.1.1 release notes
              </a>
            </p>
          </div>
        </div>
      </section>

      {/* Providers */}
      <section className="providers">
        <div className="container">
          <p className="section-label">Supported Providers</p>
          <h2 className="section-title">6 AI Providers, 1 Unified Gateway</h2>
          <div className="provider-grid">
            <div className="provider-card">
              <div className="provider-icon">🌀</div>
              <div className="provider-name">Antigravity</div>
              <div className="provider-type">Multi-model proxy</div>
              <div className="provider-free">✓ Free tier available</div>
            </div>
            <div className="provider-card">
              <div className="provider-icon">🧠</div>
              <div className="provider-name">Claude</div>
              <div className="provider-type">Anthropic API</div>
              <div className="provider-free">✓ Free tier available</div>
            </div>
            <div className="provider-card">
              <div className="provider-icon">🔓</div>
              <div className="provider-name">OpenCode</div>
              <div className="provider-type">Open-source models</div>
              <div className="provider-free">✓ Free tier available</div>
            </div>
            <div className="provider-card">
              <div className="provider-icon">💡</div>
              <div className="provider-name">Copilot</div>
              <div className="provider-type">GitHub Copilot</div>
              <div className="provider-free">✓ Free tier available</div>
            </div>
            <div className="provider-card">
              <div className="provider-icon">🔑</div>
              <div className="provider-name">Kiro</div>
              <div className="provider-type">Kiro Gateway</div>
              <div className="provider-free">✓ CLI free</div>
            </div>
            <div className="provider-card">
              <div className="provider-icon">📨</div>
              <div className="provider-name">Hermes</div>
              <div className="provider-type">Community proxy</div>
              <div className="provider-free">✓ Free tier available</div>
            </div>
          </div>
        </div>
      </section>

      {/* Features */}
      <section className="features">
        <div className="container">
          <p className="section-label">Core Innovations</p>
          <h2 className="section-title">3 Wild Ideas That Actually Work</h2>
          <div className="feature-grid">
            <div className="feature-card">
              <h3>🔄 Auto-Tuner Daemon</h3>
              <p>
                Continuously monitors proxy response times and error rates using exponential moving average analysis.
                Dynamically adjusts request routing and concurrency before congestion hits — no static thresholds,
                pure adaptive intelligence.
              </p>
            </div>
            <div className="feature-card">
              <h3>🌐 Mesh Sync</h3>
              <p>
                Every node contributes to a community-driven proxy health network. Discover working endpoints from
                peers via UDP multicast (LAN), TCP gossip (cloud), or Redis pub/sub (production). The mesh gets
                smarter with every participant.
              </p>
            </div>
            <div className="feature-card">
              <h3>⚡ Predictive Circuit Breaker</h3>
              <p>
                Statistical analysis of P50/P95/P99 latency percentiles and error rate velocity predicts failures
                30 seconds before they happen. Proactively reroutes traffic away from degrading endpoints — no
                more silent downtime.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* Install */}
      <section className="install" id="install">
        <div className="container">
          <p className="section-label">Get Started</p>
          <h2 className="section-title">Install in 30 Seconds</h2>
          <div className="install-methods">
            <div className="install-card">
              <h3>Bare Metal <span className="recommended">Recommended</span></h3>
              <div className="code-block">
                <span className="comment"># One-line install</span><br />
                curl -fsSL https://raw.githubusercontent.com/marktantongco/owl-agent-free-ai-proxy-gateway/main/install_owl_unified.sh | bash
              </div>
            </div>
            <div className="install-card">
              <h3>Podman Container</h3>
              <div className="code-block">
                <span className="comment"># Build &amp; run</span><br />
                podman build -t owl-agent:7.0 .<br />
                podman run -d -p 60000:60000 \<br />
                &nbsp;&nbsp;-p 8333:8333 owl-agent:7.0
              </div>
            </div>
            <div className="install-card">
              <h3>Podman Compose</h3>
              <div className="code-block">
                <span className="comment"># All services</span><br />
                podman-compose up -d
              </div>
            </div>
            <div className="install-card">
              <h3>Verify</h3>
              <div className="code-block">
                <span className="comment"># Health check</span><br />
                curl -s http://localhost:8333/health | jq .
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Architecture */}
      <section className="architecture">
        <div className="container">
          <p className="section-label">System Design</p>
          <h2 className="section-title">Architecture Overview</h2>
          <div className="arch-diagram">
            <pre>{`┌─────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   Client     │────▶│  Gateway :8333   │────▶│  Auto-Tuner     │
│  (any SDK)   │     │  (entry point)   │     │  (rate control) │
└─────────────┘     └──────────────────┘     └────────┬────────┘
                                                       │
                            ┌──────────────────────────┤
                            ▼                          ▼
                   ┌─────────────────┐      ┌──────────────────┐
                   │  Circuit Breaker │      │   Mesh Sync      │
                   │  (predictive)    │      │  (proxy sharing) │
                   └────────┬────────┘      └────────┬─────────┘
                            │                         │
                            ▼                         ▼
                   ┌─────────────────────────────────────────┐
                   │       Defense Layer (LRU + Elastic)        │
                   └────────────────────┬────────────────────┘
                                        │
                    ┌───────┬───────┬────┴────┬────────┬────────┐
                    ▼       ▼       ▼         ▼        ▼        ▼
               Antigravity  Claude  OpenCode  Copilot   Kiro   Hermes`}</pre>
          </div>
        </div>
      </section>

      {/* Components */}
      <section className="components">
        <div className="container">
          <p className="section-label">File Manifest</p>
          <h2 className="section-title">Core Components</h2>
          <table className="components-table">
            <thead>
              <tr>
                <th>Component</th>
                <th>File</th>
                <th>Version</th>
                <th>Description</th>
              </tr>
            </thead>
            <tbody>
              <tr><td>Installer</td><td><code>install_owl_unified.sh</code></td><td>v7.1.1</td><td>One-command setup for all components</td></tr>
              <tr><td>Forward Proxy</td><td><code>forward_proxy.py</code></td><td>v3.0</td><td>AutoTuner + MeshSync + Circuit Breaker</td></tr>
              <tr><td>Defense Layer</td><td><code>proxy_defense_fixed_v3.py</code></td><td>v3.3</td><td>Bounded LRU cache + elastic client</td></tr>
              <tr><td>MCP Server</td><td><code>owl_resilient_mcp.py</code></td><td>v1.1</td><td>5-tool MCP server (JSON-RPC)</td></tr>
              <tr><td>Mesh Alt.</td><td><code>mesh_alternatives.py</code></td><td>v1.0</td><td>TCP gossip + Redis pub/sub</td></tr>
              <tr><td>Diagnostics</td><td><code>diagnose.sh</code></td><td>v2.0</td><td>System health & troubleshooting</td></tr>
              <tr><td>Container</td><td><code>Containerfile</code></td><td>v1.0</td><td>Podman/OCI build recipe</td></tr>
              <tr><td>Compose</td><td><code>podman-compose.yml</code></td><td>v1.0</td><td>Multi-service orchestration</td></tr>
              <tr><td>Quadlet</td><td><code>owl-quadlet.container</code></td><td>v1.0</td><td>Systemd integration for Podman</td></tr>
            </tbody>
          </table>
        </div>
      </section>

      {/* Footer */}
      <footer>
        <p>
          Built with 🦉 by <a href="https://github.com/marktantongco">Mark Tantongco</a> ·{' '}
          <a href="https://github.com/marktantongco/owl-agent-free-ai-proxy-gateway">GitHub</a> ·{' '}
          MIT License · Optimized for Linux Ubuntu 8GB RAM
        </p>
        <p style={{ marginTop: 8, fontSize: '0.75rem' }}>
          Free AI Proxy Gateway · Open Source · Podman-native · MCP Compatible
        </p>
      </footer>
    </>
  )
}
