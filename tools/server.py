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
from tools.sam_imaging import router as sam_imaging_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Baza Empire Tool Server", version="1.0.0")

app.include_router(sam_imaging_router)

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
        ]
    }
