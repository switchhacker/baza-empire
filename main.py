import asyncio, logging, sys, os
sys.path.insert(0, os.path.dirname(__file__))

from agents.claw_batto.agent import ClawBatto
from agents.simon_bately.agent import SimonBately
from agents.phil_hass.agent import PhilHass
from agents.sam_axe.agent import SamAxe
from agents.rex_valor.agent import RexValor
from agents.duke_harmon.agent import DukeHarmon
from agents.scout_reeves.agent import ScoutReeves
from agents.nova_sterling.agent import NovaSterling
from core.context_db import init_context_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("baza.main")

AGENTS = {
    "simon": SimonBately,
    "claw":  ClawBatto,
    "phil":  PhilHass,
    "sam":   SamAxe,
    "rex":   RexValor,
    "duke":  DukeHarmon,
    "scout": ScoutReeves,
    "nova":  NovaSterling,
}

async def run_agent(AgentClass, name):
    while True:
        try:
            logger.info(f"Starting {name}...")
            await AgentClass().run()
        except Exception as e:
            logger.error(f"{name} crashed: {e}. Restarting in 10s...")
            await asyncio.sleep(10)

async def main():
    init_context_db()
    args = sys.argv[1:]
    selected = {k:v for k,v in AGENTS.items() if k in args} if args else AGENTS
    if args and not selected:
        print(f"Unknown: {args}. Available: {list(AGENTS.keys())}"); sys.exit(1)
    logger.info(f"Launching: {list(selected.keys())}")
    tasks = [asyncio.create_task(run_agent(C, n)) for n,C in selected.items()]
    try:
        await asyncio.gather(*tasks)
    except (KeyboardInterrupt, SystemExit):
        [t.cancel() for t in tasks]

if __name__ == "__main__":
    asyncio.run(main())
