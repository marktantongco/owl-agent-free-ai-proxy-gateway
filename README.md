# OWL-AGENT — DEPRECATED v7.0

> **v7.0 has known critical bugs; do not use. v7.1 incoming.**

The v7.0 release shipped with critical security and correctness defects
(SSRF, cache poisoning, broken embedded installer, dead code paths).
All public deployments have been taken offline.

- GitHub Pages site — **deleted**
- Vercel deployment — **being torn down**
- `v7.0-deprecated` git tag — **points at the broken commit**

For the full list of 24 P0 bugs and their fixes, see
**[KNOWN_ISSUES.md](./KNOWN_ISSUES.md)**.

If you installed v7.0 on a bare-metal host, uninstall with:

```bash
bash install_owl_unified.sh --uninstall
```

…then watch this repo for the v7.1 tag.

## What was wrong (short version)

| Class         | Count | Worst offender |
|---------------|-------|----------------|
| Security      | 4     | SSRF — proxy accepts CONNECT to `127.0.0.1:*`, `169.254.169.254` (cloud metadata), RFC1918 internals |
| Dead code     | 6     | `ProxyCache`, `MeshSync.get_peer_proxies()`, `PROVIDERS` dict, `mesh_alternatives.RedisPubSubMesh` |
| Broken install| 5     | Installer wrote simplified stubs over the real `forward_proxy.py` — every bare-metal install produced a non-functional proxy |
| Data loss     | 3     | `iptables-save` redirect under `sudo` silently failed; `OfflineQueue.peek_all()` returned wrong data; `echo` in `write_file()` stripped trailing newlines |
| Race conditions| 3    | Semaphore recreated in-flight; AutoTuner reads `MAX_CONNECTIONS` global mid-request; multicast group mismatch between installer and runtime |
| Container     | 3     | Build tools (`build-essential`, `git`) shipped in production image; Redis port exposed unnecessarily; health check hit `httpbin.org` instead of local `/health` |

## What v7.1 will look like

- **Half the code, twice the function.** Dead modules deleted.
- **SSRF allowlist** — proxy rejects any CONNECT/HTTP target not on a built-in AI-provider allowlist (configurable).
- **No cache by default.** Caching introduces cache-poisoning risk for a stateless proxy; v7.1 ships cache-free unless `OWL_CACHE_ENABLED=true` is explicitly set.
- **No `--fix` mode in `diagnose.sh`.** Diagnostics report; humans fix.
- **No Redis mesh.** UDP multicast stays for LAN; TCP gossip stays for cloud. Redis path is gone.
- **Single installer path.** No more embedded stubs — installer copies the real `forward_proxy.py` from the repo.
- **Reframed identity.** It is no longer a "proxy"; it is an **AI free-tier aggregator with mesh health sync**. The proxy is an implementation detail; the mesh is the product.

## Timeline

- **Now:** v7.0 deprecated, public deployments down.
- **+24h:** v7.1 tag cut, redeployed to GitHub Pages and Vercel.
- **+72h:** Postmortem published as `docs/POSTMORTEM-v7.0.md`.

## Contact

File issues at <https://github.com/marktantongco/owl-agent-free-ai-proxy-gateway/issues>.
