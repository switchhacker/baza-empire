"""
Baza Empire — Tool Server
--------------------------
FastAPI tool server. Each agent has a set of tools exposed as POST /tools/{agent}/{tool}.
Simon calls these endpoints when dispatching work. Each tool runs real operations on baza.
"""

import os
import subprocess
import json
import time
import logging
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Any
import httpx
from sam_imaging import router as sam_imaging_router
from nova_router import router as nova_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Baza Empire Tool Server", version="1.0.0")

app.include_router(sam_imaging_router)
app.include_router(nova_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Request/Response models ──────────────────────────────────────────────────

class ToolRequest(BaseModel):
    input: dict = {}
    task_id: Optional[str] = None

class ToolResponse(BaseModel):
    success: bool
    output: Any
    tool: str
    task_id: Optional[str] = None
    duration_ms: int
    error: Optional[str] = None

def run_tool(tool_name: str, fn, req: ToolRequest) -> ToolResponse:
    start = time.time()
    try:
        output = fn(req.input)
        return ToolResponse(
            success=True,
            output=output,
            tool=tool_name,
            task_id=req.task_id,
            duration_ms=int((time.time() - start) * 1000)
        )
    except Exception as e:
        logger.error(f"[{tool_name}] Error: {e}")
        return ToolResponse(
            success=False,
            output=None,
            tool=tool_name,
            task_id=req.task_id,
            duration_ms=int((time.time() - start) * 1000),
            error=str(e)
        )

# ═══════════════════════════════════════════════════════════════════════════════
# CLAW BATTO TOOLS — DevOps, Linux, Security
# ═══════════════════════════════════════════════════════════════════════════════

@app.post("/tools/claw/run-command")
async def claw_run_command(req: ToolRequest):
    """Run a safe shell command on baza. Whitelist enforced."""
    ALLOWED = ["systemctl status", "df -h", "free -h", "uptime", "docker ps",
               "journalctl", "ls", "cat /var/log", "ping", "curl", "wget",
               "git status", "git log", "python3", "pip", "nvidia-smi",
               "rocm-smi", "xmrig", "ps aux", "netstat", "ss -", "ip addr"]

    cmd = req.input.get("command", "")
    if not cmd:
        raise HTTPException(400, "No command provided")

    allowed = any(cmd.startswith(a) for a in ALLOWED)
    if not allowed:
        raise HTTPException(403, f"Command not in whitelist: {cmd}")

    def _run(inp):
        result = subprocess.run(
            inp["command"], shell=True, capture_output=True,
            text=True, timeout=30
        )
        return {
            "stdout": result.stdout[-3000:],  # cap at 3k chars
            "stderr": result.stderr[-500:],
            "returncode": result.returncode
        }

    return run_tool("claw/run-command", _run, req)


@app.post("/tools/claw/service-status")
async def claw_service_status(req: ToolRequest):
    """Check status of a systemd service."""
    def _run(inp):
        service = inp.get("service", "")
        if not service:
            raise ValueError("No service name provided")
        result = subprocess.run(
            f"systemctl status {service} --no-pager -l",
            shell=True, capture_output=True, text=True, timeout=10
        )
        active = "active (running)" in result.stdout
        return {"service": service, "active": active, "output": result.stdout[-2000:]}

    return run_tool("claw/service-status", _run, req)


@app.post("/tools/claw/restart-service")
async def claw_restart_service(req: ToolRequest):
    """Restart a systemd service. Only whitelisted services allowed."""
    ALLOWED_SERVICES = [
        "baza-agent-simon-bately", "baza-agent-claw-batto",
        "baza-agent-phil-hass", "baza-agent-sam-axe",
        "baza-dashboard", "baza-tool-server",
        "baza-mining", "baza-nuc-mining",
        "mosquitto", "postgresql", "redis", "nginx", "docker"
    ]

    def _run(inp):
        service = inp.get("service", "")
        if service not in ALLOWED_SERVICES:
            raise ValueError(f"Service not whitelisted: {service}")
        result = subprocess.run(
            f"sudo systemctl restart {service}",
            shell=True, capture_output=True, text=True, timeout=15
        )
        return {"service": service, "restarted": result.returncode == 0, "output": result.stdout}

    return run_tool("claw/restart-service", _run, req)


@app.post("/tools/claw/docker-status")
async def claw_docker_status(req: ToolRequest):
    """List all running Docker containers."""
    def _run(inp):
        result = subprocess.run(
            "docker ps --format '{{.Names}}\t{{.Status}}\t{{.Ports}}'",
            shell=True, capture_output=True, text=True, timeout=10
        )
        containers = []
        for line in result.stdout.strip().splitlines():
            parts = line.split("\t")
            containers.append({
                "name": parts[0] if len(parts) > 0 else "",
                "status": parts[1] if len(parts) > 1 else "",
                "ports": parts[2] if len(parts) > 2 else ""
            })
        return {"containers": containers, "count": len(containers)}

    return run_tool("claw/docker-status", _run, req)


@app.post("/tools/claw/disk-usage")
async def claw_disk_usage(req: ToolRequest):
    """Check disk usage on baza."""
    def _run(inp):
        result = subprocess.run("df -h", shell=True, capture_output=True, text=True)
        return {"output": result.stdout}

    return run_tool("claw/disk-usage", _run, req)


@app.post("/tools/claw/mining-status")
async def claw_mining_status(req: ToolRequest):
    """Check status of all mining services."""
    def _run(inp):
        services = ["baza-mining", "baza-nuc-mining"]
        statuses = {}
        for svc in services:
            r = subprocess.run(
                f"systemctl is-active {svc}",
                shell=True, capture_output=True, text=True
            )
            statuses[svc] = r.stdout.strip()
        return statuses

    return run_tool("claw/mining-status", _run, req)



@app.post("/tools/claw/start-mining")
async def claw_start_mining(req: ToolRequest):
    """Start mining services on baza and NUC."""
    def _run(inp):
        services = inp.get("services", ["baza-mining", "baza-nuc-mining"])
        results = {}
        for svc in services:
            r = subprocess.run(
                f"sudo systemctl start {svc}",
                shell=True, capture_output=True, text=True, timeout=15
            )
            # Check if it actually started
            check = subprocess.run(
                f"systemctl is-active {svc}",
                shell=True, capture_output=True, text=True
            )
            results[svc] = check.stdout.strip()
        return results

    return run_tool("claw/start-mining", _run, req)


@app.post("/tools/claw/stop-mining")
async def claw_stop_mining(req: ToolRequest):
    """Stop mining services on baza and NUC."""
    def _run(inp):
        services = inp.get("services", ["baza-mining", "baza-nuc-mining"])
        results = {}
        for svc in services:
            r = subprocess.run(
                f"sudo systemctl stop {svc}",
                shell=True, capture_output=True, text=True, timeout=15
            )
            check = subprocess.run(
                f"systemctl is-active {svc}",
                shell=True, capture_output=True, text=True
            )
            results[svc] = check.stdout.strip()
        return results

    return run_tool("claw/stop-mining", _run, req)


@app.post("/tools/sam/mining-earnings")
async def sam_mining_earnings(req: ToolRequest):
    """Fetch live mining earnings from supportxmr.com pool API."""
    start = time.time()
    wallet = req.input.get("wallet", os.environ.get("XMR_WALLET_ADDRESS", ""))
    if not wallet:
        return ToolResponse(success=False, output=None, tool="sam/mining-earnings",
                            task_id=req.task_id, duration_ms=0,
                            error="No wallet address. Set XMR_WALLET_ADDRESS in secrets.env")
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            stats_resp = await client.get(
                f"https://supportxmr.com/api/miner/{wallet}/stats"
            )
            stats_resp.raise_for_status()
            data = stats_resp.json()

            price_resp = await client.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": "monero", "vs_currencies": "usd"}
            )
            xmr_price = price_resp.json().get("monero", {}).get("usd", 0)

        paid = data.get("amtPaid", 0) / 1e12
        pending = data.get("amtDue", 0) / 1e12
        hashrate = data.get("hash", 0)

        output = {
            "hashrate_hs": hashrate,
            "paid_xmr": round(paid, 6),
            "pending_xmr": round(pending, 6),
            "pending_usd": round(pending * xmr_price, 4),
            "xmr_price_usd": xmr_price,
        }
        return ToolResponse(success=True, output=output, tool="sam/mining-earnings",
                            task_id=req.task_id,
                            duration_ms=int((time.time() - start) * 1000))
    except Exception as e:
        logger.error(f"[mining-earnings] {e}")
        return ToolResponse(success=False, output=None, tool="sam/mining-earnings",
                            task_id=req.task_id,
                            duration_ms=int((time.time() - start) * 1000),
                            error=str(e))


# ═══════════════════════════════════════════════════════════════════════════════
# PHIL HASS TOOLS — Legal, Finance, Documents
# ═══════════════════════════════════════════════════════════════════════════════

@app.post("/tools/phil/generate-invoice")
async def phil_generate_invoice(req: ToolRequest):
    """Generate a plain-text invoice for AHBCO LLC."""
    def _run(inp):
        client = inp.get("client_name", "Client")
        items = inp.get("items", [])  # [{"description": "...", "amount": 0.00}]
        invoice_num = inp.get("invoice_number", f"INV-{int(time.time())}")
        date = inp.get("date", time.strftime("%Y-%m-%d"))

        total = sum(i.get("amount", 0) for i in items)
        lines = [
            f"INVOICE — All Home Building Co LLC / DBA-AHBCO LLC",
            f"Invoice #: {invoice_num}",
            f"Date: {date}",
            f"Bill To: {client}",
            "─" * 40,
        ]
        for item in items:
            lines.append(f"  {item.get('description', '')} ... ${item.get('amount', 0):.2f}")
        lines += ["─" * 40, f"TOTAL: ${total:.2f}", "", "Payment due within 30 days."]

        invoice_text = "\n".join(lines)

        # Save to disk
        path = f"/tmp/invoice_{invoice_num}.txt"
        with open(path, "w") as f:
            f.write(invoice_text)

        return {"invoice_number": invoice_num, "total": total, "path": path, "text": invoice_text}

    return run_tool("phil/generate-invoice", _run, req)


@app.post("/tools/phil/tax-summary")
async def phil_tax_summary(req: ToolRequest):
    """Generate a basic tax estimate for AHBCO LLC."""
    def _run(inp):
        revenue = inp.get("revenue", 0)
        expenses = inp.get("expenses", 0)
        state = inp.get("state", "NY")

        net = revenue - expenses
        # Rough estimates: federal SE tax ~15.3%, income tax ~22% bracket
        se_tax = net * 0.153
        income_tax = max(0, net * 0.22)
        total_est = se_tax + income_tax
        quarterly = total_est / 4

        return {
            "revenue": revenue,
            "expenses": expenses,
            "net_profit": net,
            "estimated_se_tax": round(se_tax, 2),
            "estimated_income_tax": round(income_tax, 2),
            "total_estimated_tax": round(total_est, 2),
            "quarterly_payment": round(quarterly, 2),
            "state": state,
            "note": "This is an estimate. Consult a CPA for filing."
        }

    return run_tool("phil/tax-summary", _run, req)


@app.post("/tools/phil/contract-template")
async def phil_contract_template(req: ToolRequest):
    """Generate a basic contractor agreement template."""
    def _run(inp):
        contractor = inp.get("contractor_name", "[CONTRACTOR NAME]")
        scope = inp.get("scope", "[SCOPE OF WORK]")
        rate = inp.get("rate", "[RATE]")
        start_date = inp.get("start_date", "[START DATE]")

        template = f"""INDEPENDENT CONTRACTOR AGREEMENT

This Agreement is entered into as of {start_date} between:

All Home Building Co LLC (DBA-AHBCO LLC), a New York Limited Liability Company ("Company")
and {contractor} ("Contractor").

1. SCOPE OF WORK
Contractor agrees to perform the following services: {scope}

2. COMPENSATION
Company agrees to pay Contractor {rate} upon completion of deliverables.

3. INDEPENDENT CONTRACTOR STATUS
Contractor is an independent contractor. Nothing in this Agreement creates an
employer-employee relationship.

4. CONFIDENTIALITY
Contractor agrees to keep all Company information confidential.

5. INTELLECTUAL PROPERTY
All work product created under this Agreement is the sole property of the Company.

6. TERMINATION
Either party may terminate this Agreement with 14 days written notice.

7. GOVERNING LAW
This Agreement shall be governed by the laws of the State of New York.

SIGNATURES:

Company: _______________________ Date: _________
Contractor: _____________________ Date: _________
"""
        path = f"/tmp/contract_{contractor.replace(' ', '_')}.txt"
        with open(path, "w") as f:
            f.write(template)

        return {"contractor": contractor, "path": path, "text": template}

    return run_tool("phil/contract-template", _run, req)


# ═══════════════════════════════════════════════════════════════════════════════
# SAM AXE TOOLS — Analytics, Marketing, Media
# ═══════════════════════════════════════════════════════════════════════════════

@app.post("/tools/sam/scrape-web")
async def sam_scrape_web(req: ToolRequest):
    """Fetch and return text content from a URL."""
    async def _run(inp):
        url = inp.get("url", "")
        if not url:
            raise ValueError("No URL provided")
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, follow_redirects=True,
                                    headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            # Strip HTML tags roughly
            import re
            text = re.sub(r'<[^>]+>', ' ', resp.text)
            text = re.sub(r'\s+', ' ', text).strip()
            return {"url": url, "content": text[:5000], "length": len(text)}

    start = time.time()
    try:
        output = await _run(req.input)
        return ToolResponse(success=True, output=output, tool="sam/scrape-web",
                            task_id=req.task_id, duration_ms=int((time.time()-start)*1000))
    except Exception as e:
        return ToolResponse(success=False, output=None, tool="sam/scrape-web",
                            task_id=req.task_id, duration_ms=int((time.time()-start)*1000),
                            error=str(e))


@app.post("/tools/sam/crypto-prices")
async def sam_crypto_prices(req: ToolRequest):
    """Fetch current prices for empire coins (XMR, RVN, BTC)."""
    async def _run(inp):
        coins = inp.get("coins", ["monero", "ravencoin", "bitcoin"])
        ids = ",".join(coins)
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd&include_24hr_change=true"
            )
            resp.raise_for_status()
            return resp.json()

    start = time.time()
    try:
        output = await _run(req.input)
        return ToolResponse(success=True, output=output, tool="sam/crypto-prices",
                            task_id=req.task_id, duration_ms=int((time.time()-start)*1000))
    except Exception as e:
        return ToolResponse(success=False, output=None, tool="sam/crypto-prices",
                            task_id=req.task_id, duration_ms=int((time.time()-start)*1000),
                            error=str(e))


@app.post("/tools/sam/market-research")
async def sam_market_research(req: ToolRequest):
    """Search DuckDuckGo and return top results for a query."""
    async def _run(inp):
        query = inp.get("query", "")
        if not query:
            raise ValueError("No query provided")
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_html": 1},
                headers={"User-Agent": "Mozilla/5.0"}
            )
            data = resp.json()
            results = []
            if data.get("AbstractText"):
                results.append({"title": data.get("Heading", ""), "summary": data["AbstractText"]})
            for r in data.get("RelatedTopics", [])[:5]:
                if "Text" in r:
                    results.append({"title": r.get("Text", "")[:100], "url": r.get("FirstURL", "")})
            return {"query": query, "results": results}

    start = time.time()
    try:
        output = await _run(req.input)
        return ToolResponse(success=True, output=output, tool="sam/market-research",
                            task_id=req.task_id, duration_ms=int((time.time()-start)*1000))
    except Exception as e:
        return ToolResponse(success=False, output=None, tool="sam/market-research",
                            task_id=req.task_id, duration_ms=int((time.time()-start)*1000),
                            error=str(e))


@app.post("/tools/sam/kpi-report")
async def sam_kpi_report(req: ToolRequest):
    """Generate a KPI summary report from provided metrics."""
    def _run(inp):
        metrics = inp.get("metrics", {})
        title = inp.get("title", "KPI Report")
        date = time.strftime("%Y-%m-%d")

        lines = [f"{title} — {date}", "=" * 40]
        for key, value in metrics.items():
            lines.append(f"  {key}: {value}")

        lines += ["=" * 40, f"Generated by Sam Axe — Baza Empire Analytics"]
        report = "\n".join(lines)

        path = f"/tmp/kpi_report_{int(time.time())}.txt"
        with open(path, "w") as f:
            f.write(report)

        return {"title": title, "path": path, "report": report, "metric_count": len(metrics)}

    return run_tool("sam/kpi-report", _run, req)



# ═══════════════════════════════════════════════════════════════════════════════
# SIMON TOOLS — Coordination, Reporting
# ═══════════════════════════════════════════════════════════════════════════════

@app.post("/tools/simon/send-report")
async def simon_send_report(req: ToolRequest):
    """Send a message to Serge via Simon's Telegram bot."""
    async def _run(inp):
        message = inp.get("message", "")
        chat_id = inp.get("chat_id", os.environ.get("SERGE_CHAT_ID", ""))
        token = os.environ.get("TELEGRAM_SIMON_BATELY", "")

        if not message or not chat_id or not token:
            raise ValueError("Missing message, chat_id, or Simon's token")

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"}
            )
            resp.raise_for_status()
            return {"sent": True, "chat_id": chat_id}

    start = time.time()
    try:
        output = await _run(req.input)
        return ToolResponse(success=True, output=output, tool="simon/send-report",
                            task_id=req.task_id, duration_ms=int((time.time()-start)*1000))
    except Exception as e:
        return ToolResponse(success=False, output=None, tool="simon/send-report",
                            task_id=req.task_id, duration_ms=int((time.time()-start)*1000),
                            error=str(e))


@app.post("/tools/simon/schedule-task")
async def simon_schedule_task(req: ToolRequest):
    """Store a scheduled task in Redis for later execution."""
    import redis as redis_lib

    def _run(inp):
        task = inp.get("task", "")
        run_at = inp.get("run_at", "")  # ISO datetime string
        assigned_to = inp.get("assigned_to", "simon_bately")

        if not task:
            raise ValueError("No task provided")

        r = redis_lib.Redis(host="localhost", port=6379, decode_responses=True)
        task_id = f"scheduled_{int(time.time())}"
        r.hset(f"scheduled:{task_id}", mapping={
            "task": task,
            "run_at": run_at,
            "assigned_to": assigned_to,
            "status": "pending",
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
        })
        r.expire(f"scheduled:{task_id}", 86400 * 7)
        return {"task_id": task_id, "task": task, "run_at": run_at, "assigned_to": assigned_to}

    return run_tool("simon/schedule-task", _run, req)


# ═══════════════════════════════════════════════════════════════════════════════
# HEALTH
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/health")
async def health():
    return {"status": "online", "empire": "Baza", "tools": "ready"}

@app.get("/tools")
async def list_tools():
    """List all available tools."""
    return {
        "claw_batto": [
            "run-command", "service-status", "restart-service",
            "docker-status", "disk-usage", "mining-status"
        ],
        "phil_hass": [
            "generate-invoice", "tax-summary", "contract-template"
        ],
        "sam_axe": [
            "scrape-web", "crypto-prices", "market-research", "kpi-report"
        ],
        "simon_bately": [
            "send-report", "schedule-task"
        ],
        "system": [
            "temps", "gpu-pool", "services", "network", "ollama-status",
            "memory", "uptime", "disk-all", "cpu-top", "failed-services"
        ],
        "agents": [
            "pulse", "activity", "knowledge", "skill-run"
        ],
        "data": [
            "convert", "search-artifacts", "db-stats", "token-count"
        ],
        "construction": [
            "quick-estimate", "material-calc", "rates", "permit-check"
        ],
        "utility": [
            "weather", "crypto", "whois", "headers-check", "ssl-check"
        ]
    }


# ═══════════════════════════════════════════════════════════════════════════════
# System Tools
# ═══════════════════════════════════════════════════════════════════════════════

def _run(cmd, timeout=10):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception as e:
        return str(e)


@app.get("/tools/system/temps")
async def system_temps():
    """All hardware temperatures."""
    temps = {}
    # CPU
    cpu = _run("sensors -j 2>/dev/null | python3 -c \"import sys,json;d=json.load(sys.stdin);print(json.dumps(d.get('k10temp-pci-00c3',{})))\" 2>/dev/null")
    try: temps["cpu"] = json.loads(cpu)
    except: temps["cpu"] = {"raw": cpu}
    # NVIDIA
    nv = _run("nvidia-smi --query-gpu=name,temperature.gpu,memory.used,memory.total,fan.speed,power.draw --format=csv,noheader 2>/dev/null")
    if nv:
        parts = [p.strip() for p in nv.split(",")]
        temps["nvidia"] = {"name": parts[0], "temp_c": int(parts[1]) if len(parts)>1 else None,
                           "mem_used": parts[2] if len(parts)>2 else None, "mem_total": parts[3] if len(parts)>3 else None,
                           "fan": parts[4] if len(parts)>4 else None, "power": parts[5] if len(parts)>5 else None}
    # AMD
    for h in os.listdir("/sys/class/hwmon"):
        try:
            with open(f"/sys/class/hwmon/{h}/name") as f:
                if f.read().strip() == "amdgpu":
                    for tf, label in [("temp1_input","edge"),("temp2_input","junction"),("temp3_input","mem")]:
                        try:
                            with open(f"/sys/class/hwmon/{h}/{tf}") as f2:
                                v = int(f2.read().strip())
                                if v > 0: temps.setdefault("amd",{})[label] = v // 1000
                        except: pass
        except: pass
    # NVMe
    nvme = _run("sensors 2>/dev/null | grep -A1 'nvme' | grep 'Composite' | head -2")
    if nvme: temps["nvme"] = nvme
    return temps


@app.get("/tools/system/gpu-pool")
async def gpu_pool_status():
    """GPU pool status — all 3 backends."""
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from core.gpu_pool import gpu_pool
    return {"slots": gpu_pool.status()}


@app.get("/tools/system/services")
async def system_services():
    """All baza-* systemd service statuses."""
    raw = _run("systemctl list-units 'baza-*' --no-pager --no-legend --plain 2>/dev/null")
    services = []
    for line in raw.split("\n"):
        parts = line.split()
        if len(parts) >= 4:
            services.append({"unit": parts[0], "load": parts[1], "active": parts[2], "sub": parts[3]})
    # Also check ollama instances
    for svc in ["ollama", "ollama-amd", "ollama-cpu"]:
        state = _run(f"systemctl is-active {svc} 2>/dev/null")
        services.append({"unit": svc, "active": state})
    return {"services": services, "count": len(services)}


@app.get("/tools/system/network")
async def system_network():
    """Network interfaces, IPs, Tailscale."""
    interfaces = _run("ip -j addr show 2>/dev/null")
    tailscale = _run("tailscale status --json 2>/dev/null | python3 -c \"import sys,json;d=json.load(sys.stdin);s=d.get('Self',{});print(json.dumps({'ip':s.get('TailscaleIPs',[]),'hostname':s.get('HostName',''),'online':s.get('Online',False)}))\" 2>/dev/null")
    try: ts = json.loads(tailscale)
    except: ts = {"raw": tailscale}
    return {"tailscale": ts, "interfaces_raw": interfaces[:2000]}


@app.get("/tools/system/ollama-status")
async def ollama_status():
    """All Ollama instances — loaded models, health."""
    instances = {}
    for port, name in [(11434,"nvidia"), (11435,"amd"), (11436,"cpu")]:
        try:
            import urllib.request
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ps", timeout=3) as r:
                data = json.loads(r.read())
                models = [{"name":m["name"],"size_vram_mb":m.get("size_vram",0)//1024//1024,
                           "ctx":m.get("context_length",0)} for m in data.get("models",[])]
                instances[name] = {"port": port, "status": "online", "models": models}
        except Exception as e:
            instances[name] = {"port": port, "status": "offline", "error": str(e)[:100]}
    return instances


@app.get("/tools/system/memory")
async def system_memory():
    """RAM + swap breakdown."""
    free = _run("free -b --si 2>/dev/null")
    lines = free.split("\n")
    result = {}
    for line in lines:
        parts = line.split()
        if parts and parts[0] == "Mem:":
            result["ram"] = {"total_gb": int(parts[1])/(1024**3), "used_gb": int(parts[2])/(1024**3),
                             "free_gb": int(parts[3])/(1024**3), "available_gb": int(parts[6])/(1024**3) if len(parts)>6 else 0}
        elif parts and parts[0] == "Swap:":
            result["swap"] = {"total_gb": int(parts[1])/(1024**3), "used_gb": int(parts[2])/(1024**3)}
    return result


@app.get("/tools/system/uptime")
async def system_uptime():
    """System uptime + load averages."""
    uptime = _run("uptime -p")
    load = _run("cat /proc/loadavg")
    parts = load.split() if load else []
    return {"uptime": uptime, "load_1m": float(parts[0]) if parts else 0,
            "load_5m": float(parts[1]) if len(parts)>1 else 0,
            "load_15m": float(parts[2]) if len(parts)>2 else 0}


@app.get("/tools/system/disk-all")
async def system_disk_all():
    """All mount points usage."""
    df = _run("df -h --output=source,size,used,avail,pcent,target 2>/dev/null")
    mounts = []
    for line in df.split("\n")[1:]:
        parts = line.split()
        if len(parts) >= 6 and not parts[0].startswith("tmpfs"):
            mounts.append({"source": parts[0], "size": parts[1], "used": parts[2],
                           "avail": parts[3], "pct": parts[4], "mount": parts[5]})
    return {"mounts": mounts}


@app.get("/tools/system/cpu-top")
async def system_cpu_top():
    """Top CPU consumers."""
    ps = _run("ps aux --sort=-%cpu | head -11")
    procs = []
    for line in ps.split("\n")[1:]:
        parts = line.split(None, 10)
        if len(parts) >= 11:
            procs.append({"user": parts[0], "cpu": float(parts[2]), "mem": float(parts[3]),
                          "pid": int(parts[1]), "command": parts[10][:80]})
    return {"processes": procs}


@app.get("/tools/system/failed-services")
async def failed_services():
    """List failed systemd services."""
    raw = _run("systemctl --failed --no-pager --no-legend --plain")
    failed = [l.strip() for l in raw.split("\n") if l.strip()]
    return {"failed": failed, "count": len(failed)}


# ═══════════════════════════════════════════════════════════════════════════════
# Agent Tools
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/tools/agents/pulse")
async def agents_pulse():
    """All 9 agents' heartbeat status from Redis."""
    import redis
    agents = ["simon_bately","claw_batto","phil_hass","sam_axe","rex_valor",
              "duke_harmon","scout_reeves","nova_sterling","specter_voss"]
    r = redis.Redis(host="localhost", port=6379, decode_responses=True, socket_timeout=3)
    result = {}
    for a in agents:
        hb = r.get(f"baza:heartbeat:{a}")
        if hb:
            try:
                d = json.loads(hb)
                age = int(time.time()) - int(d.get("ts",0))
                result[a] = {"status": "online" if age < 180 else "stale", "age_s": age,
                             "model": d.get("model"), "raw_status": d.get("status")}
            except: result[a] = {"status": "parse_error"}
        else:
            result[a] = {"status": "offline"}
    return result


@app.get("/tools/agents/activity")
async def agents_activity():
    """Recent agent activity from team_activity view."""
    limit = 30
    try:
        import psycopg2
        conn = psycopg2.connect(host="localhost", port=5432, dbname="baza_agents",
                                user="switchhacker", password=os.environ.get("DB_PASSWORD","baza2026"))
        cur = conn.cursor()
        cur.execute("SELECT ts, agent_id, kind, subkind, summary FROM team_activity ORDER BY ts DESC LIMIT %s", (limit,))
        rows = [{"ts": r[0].isoformat(), "agent": r[1], "kind": r[2], "subkind": r[3],
                 "summary": r[4][:200] if r[4] else ""} for r in cur.fetchall()]
        cur.close(); conn.close()
        return {"events": rows, "count": len(rows)}
    except Exception as e:
        return {"error": str(e)}


@app.get("/tools/agents/{agent_id}/memory")
async def agent_memory(agent_id: str):
    """Agent's memory entries from PostgreSQL."""
    try:
        import psycopg2
        conn = psycopg2.connect(host="localhost", port=5432, dbname="baza_agents",
                                user="switchhacker", password=os.environ.get("DB_PASSWORD","baza2026"))
        cur = conn.cursor()
        cur.execute("SELECT key, value, category, updated_at FROM agent_memory WHERE agent_id=%s ORDER BY updated_at DESC", (agent_id,))
        rows = [{"key": r[0], "value": r[1], "category": r[2], "updated": r[3].isoformat() if r[3] else ""} for r in cur.fetchall()]
        cur.close(); conn.close()
        return {"agent": agent_id, "memories": rows, "count": len(rows)}
    except Exception as e:
        return {"error": str(e)}


@app.get("/tools/agents/knowledge")
async def empire_knowledge():
    """Empire knowledge entries."""
    try:
        import psycopg2
        conn = psycopg2.connect(host="localhost", port=5432, dbname="baza_agents",
                                user="switchhacker", password=os.environ.get("DB_PASSWORD","baza2026"))
        cur = conn.cursor()
        cur.execute("SELECT key, value, category, updated_by, updated_at FROM empire_knowledge ORDER BY updated_at DESC LIMIT 50")
        rows = [{"key": r[0], "value": r[1][:300], "category": r[2], "by": r[3], "updated": r[4].isoformat() if r[4] else ""} for r in cur.fetchall()]
        cur.close(); conn.close()
        return {"knowledge": rows, "count": len(rows)}
    except Exception as e:
        return {"error": str(e)}


class SkillRunRequest(BaseModel):
    skill: str
    args: dict = {}

@app.post("/tools/agents/skill-run")
async def skill_run(req: SkillRunRequest):
    """Run any shared Baza skill by name."""
    FRAMEWORK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    skill_path = os.path.join(FRAMEWORK, "skills", "shared", f"{req.skill}.py")
    if not os.path.exists(skill_path):
        raise HTTPException(404, f"Skill '{req.skill}' not found")
    env = os.environ.copy()
    env["SKILL_ARGS"] = json.dumps(req.args)
    env["AGENT_ID"] = "tool_server"
    try:
        proc = subprocess.run([os.path.join(FRAMEWORK,"venv","bin","python"), skill_path],
                              capture_output=True, text=True, timeout=90, env=env)
        return {"skill": req.skill, "success": proc.returncode == 0,
                "output": proc.stdout.strip() if proc.returncode == 0 else proc.stderr.strip()[:500]}
    except subprocess.TimeoutExpired:
        return {"skill": req.skill, "success": False, "output": "Timed out"}
    except Exception as e:
        return {"skill": req.skill, "success": False, "output": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
# Data Tools
# ═══════════════════════════════════════════════════════════════════════════════

class ConvertRequest(BaseModel):
    data: str
    from_format: str = "csv"
    to_format: str = "json"

@app.post("/tools/data/convert")
async def data_convert(req: ConvertRequest):
    """Convert between formats (CSV↔JSON, MD→HTML)."""
    import csv, io
    if req.from_format == "csv" and req.to_format == "json":
        reader = csv.DictReader(io.StringIO(req.data))
        return {"result": list(reader)}
    elif req.from_format == "json" and req.to_format == "csv":
        rows = json.loads(req.data)
        if not rows: return {"result": ""}
        out = io.StringIO()
        w = csv.DictWriter(out, fieldnames=rows[0].keys())
        w.writeheader(); w.writerows(rows)
        return {"result": out.getvalue()}
    elif req.from_format == "md" and req.to_format == "html":
        # Simple markdown → HTML
        html = req.data.replace("\n\n","<br><br>")
        import re
        html = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', html)
        html = re.sub(r'\*(.+?)\*', r'<i>\1</i>', html)
        html = re.sub(r'^# (.+)$', r'<h1>\1</h1>', html, flags=re.MULTILINE)
        html = re.sub(r'^## (.+)$', r'<h2>\1</h2>', html, flags=re.MULTILINE)
        return {"result": html}
    return {"error": f"Unsupported: {req.from_format} → {req.to_format}"}


@app.get("/tools/data/db-stats")
async def db_stats():
    """Dashboard DB stats (clients, projects, invoices)."""
    import sqlite3
    FRAMEWORK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    db = os.path.join(FRAMEWORK, "dashboard", "baza_projects.db")
    try:
        conn = sqlite3.connect(db)
        stats = {}
        for table in ["ahb_clients","ahb_projects","ahb_invoices","ahb_receipts","tasks","ahb_employees"]:
            try: stats[table] = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            except: stats[table] = 0
        # Revenue
        try: stats["total_revenue"] = conn.execute("SELECT coalesce(sum(amount),0) FROM ahb_invoices WHERE status='paid'").fetchone()[0]
        except: stats["total_revenue"] = 0
        conn.close()
        return stats
    except Exception as e:
        return {"error": str(e)}


class TokenCountRequest(BaseModel):
    text: str

@app.post("/tools/data/token-count")
async def token_count(req: TokenCountRequest):
    """Estimate token count."""
    chars = len(req.text)
    words = len(req.text.split())
    tokens = max(1, chars // 4)
    return {"chars": chars, "words": words, "estimated_tokens": tokens,
            "cost_gpt4o": round(tokens * 0.005 / 1000, 4),
            "cost_claude": round(tokens * 0.015 / 1000, 4),
            "cost_ollama": 0.0}


@app.get("/tools/data/search-artifacts")
async def search_artifacts(q: str = ""):
    """Search across all artifact filenames."""
    FRAMEWORK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    art_dir = os.path.join(FRAMEWORK, "dashboard", "artifacts")
    results = []
    if not q: return {"error": "query parameter 'q' required"}
    for root, dirs, files in os.walk(art_dir):
        for f in files:
            if q.lower() in f.lower():
                rel = os.path.relpath(os.path.join(root, f), art_dir)
                results.append({"name": f, "path": rel, "size": os.path.getsize(os.path.join(root,f))})
    return {"query": q, "results": results[:50], "count": len(results)}


# ═══════════════════════════════════════════════════════════════════════════════
# Construction Tools
# ═══════════════════════════════════════════════════════════════════════════════

class EstimateRequest(BaseModel):
    project_type: str = "kitchen"
    sqft: float = 200
    grade: str = "mid"

@app.post("/tools/construction/quick-estimate")
async def quick_estimate(req: EstimateRequest):
    """Quick project estimate by type + sqft."""
    rates = {
        "kitchen": {"low":80,"mid":150,"high":300},
        "bathroom": {"low":100,"mid":200,"high":400},
        "basement": {"low":30,"mid":60,"high":120},
        "deck": {"low":20,"mid":40,"high":80},
        "roofing": {"low":5,"mid":10,"high":20},
        "painting": {"low":3,"mid":5,"high":8},
        "flooring": {"low":5,"mid":10,"high":18},
        "general": {"low":50,"mid":100,"high":200},
    }
    r = rates.get(req.project_type, rates["general"])
    rate = r.get(req.grade, r["mid"])
    total = req.sqft * rate
    return {"project": req.project_type, "sqft": req.sqft, "grade": req.grade,
            "rate_per_sqft": rate, "estimate": total,
            "range_low": req.sqft * r["low"], "range_high": req.sqft * r["high"]}


class MaterialCalcRequest(BaseModel):
    material: str = "drywall"
    sqft: float = 0
    waste_pct: float = 10

@app.post("/tools/construction/material-calc")
async def material_calc(req: MaterialCalcRequest):
    """Calculate material quantities."""
    coverage = {"drywall":32,"paint":350,"tile":1,"flooring":20,"insulation":77,"concrete_bags":0.6}
    cov = coverage.get(req.material, 1)
    total_sqft = req.sqft * (1 + req.waste_pct/100)
    if cov > 0:
        units = int(total_sqft / cov) + 1
    else:
        units = int(total_sqft)
    unit_names = {"drywall":"4x8 sheets","paint":"gallons","tile":"tiles","flooring":"boxes (20sqft)",
                  "insulation":"rolls","concrete_bags":"80lb bags"}
    return {"material": req.material, "sqft": req.sqft, "waste": req.waste_pct,
            "total_sqft": total_sqft, "units_needed": units,
            "unit_type": unit_names.get(req.material, "units")}


@app.get("/tools/construction/rates")
async def construction_rates():
    """Current labor/material rate reference (PA market)."""
    return {
        "labor_rates_per_hour": {
            "general_laborer": "$18-25", "carpenter": "$25-45", "electrician": "$35-65",
            "plumber": "$35-60", "painter": "$20-35", "mason": "$30-50",
            "roofer": "$25-40", "hvac_tech": "$35-65", "tile_setter": "$30-50"
        },
        "material_rates": {
            "drywall_4x8": "$12-18/sheet", "2x4_stud": "$3-6/each",
            "paint_gallon": "$30-60", "hardwood_flooring": "$6-12/sqft",
            "tile_ceramic": "$2-8/sqft", "concrete_per_yard": "$150-200",
            "roofing_shingle_bundle": "$30-50", "insulation_r19": "$0.50-1.00/sqft"
        },
        "market": "Greater Philadelphia / Bucks County PA",
        "updated": "2026 rates"
    }


@app.get("/tools/construction/permit-check")
async def permit_check(project: str = "", value: float = 0):
    """Check if a project typically needs permits in PA."""
    always_permit = ["electrical","plumbing","structural","roofing","hvac","addition","new_construction","deck"]
    maybe_permit = ["kitchen","bathroom","basement","window","door","siding"]
    no_permit = ["painting","flooring","cabinet_refacing","minor_repair","landscaping"]
    p = project.lower().replace(" ","_")
    if any(k in p for k in always_permit):
        needed = "YES — permit required"
    elif any(k in p for k in maybe_permit):
        needed = "LIKELY — check with local building dept"
    elif any(k in p for k in no_permit):
        needed = "NO — typically not required"
    else:
        needed = "UNKNOWN — check with Bucks County building dept"
    return {"project": project, "value": value, "permit_needed": needed,
            "note": "PA requires HIC license for residential work over $500",
            "contact": "Bucks County Building Inspection: (215) 348-6060"}


# ═══════════════════════════════════════════════════════════════════════════════
# Utility Tools
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/tools/utility/weather")
async def utility_weather(location: str = "Bensalem,PA"):
    """Current weather."""
    import urllib.request, urllib.parse, socket
    _orig = socket.getaddrinfo
    socket.getaddrinfo = lambda *a, **k: [r for r in _orig(*a, **k) if r[0] == socket.AF_INET6] or _orig(*a, **k)
    try:
        url = f"https://wttr.in/{urllib.parse.quote(location)}?format=j1"
        req = urllib.request.Request(url, headers={"User-Agent": "BazaEmpire/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        c = data["current_condition"][0]
        return {"location": location, "temp_f": c.get("temp_F"), "temp_c": c.get("temp_C"),
                "feels_like_f": c.get("FeelsLikeF"), "humidity": c.get("humidity"),
                "desc": c["weatherDesc"][0]["value"], "wind_mph": c.get("windspeedMiles")}
    except Exception as e:
        return {"error": str(e)}


@app.get("/tools/utility/crypto")
async def utility_crypto(coins: str = "monero,ravencoin,bitcoin,ethereum"):
    """Crypto prices."""
    import urllib.request, socket
    _orig = socket.getaddrinfo
    socket.getaddrinfo = lambda *a, **k: [r for r in _orig(*a, **k) if r[0] == socket.AF_INET6] or _orig(*a, **k)
    coin_list = [c.strip() for c in coins.split(",")]
    try:
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={','.join(coin_list)}&vs_currencies=usd&include_24hr_change=true"
        req = urllib.request.Request(url, headers={"User-Agent": "BazaEmpire/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"error": str(e)}


@app.get("/tools/utility/ssl-check")
async def utility_ssl_check(domain: str = ""):
    """Check SSL certificate for a domain."""
    if not domain: raise HTTPException(400, "domain parameter required")
    import ssl, socket
    try:
        ctx = ssl.create_default_context()
        with ctx.wrap_socket(socket.socket(), server_hostname=domain) as s:
            s.settimeout(5); s.connect((domain, 443))
            cert = s.getpeercert()
        from datetime import datetime
        exp = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z")
        days = (exp - datetime.now()).days
        return {"domain": domain, "expires": exp.isoformat(), "days_left": days,
                "status": "ok" if days > 30 else "warning" if days > 7 else "critical"}
    except Exception as e:
        return {"domain": domain, "error": str(e)}


@app.get("/tools/utility/headers-check")
async def utility_headers(url: str = ""):
    """Check HTTP security headers."""
    if not url: raise HTTPException(400, "url parameter required")
    import urllib.request
    checks = ["X-Frame-Options","X-Content-Type-Options","Strict-Transport-Security",
              "Content-Security-Policy","X-XSS-Protection","Referrer-Policy"]
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as r:
            found = {h: r.headers.get(h) for h in checks}
            missing = [h for h in checks if not found[h]]
            return {"url": url, "headers": found, "missing": missing,
                    "score": f"{len(checks)-len(missing)}/{len(checks)}"}
    except Exception as e:
        return {"url": url, "error": str(e)}
