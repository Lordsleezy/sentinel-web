"""
main.py — Entry point for Sentinel Web Agent

Usage:
  python main.py                 Start server on port 8766
  python main.py --headed        Show browser window (debug mode)
  python main.py --port 9000     Custom port
  python main.py --no-ollama     Disable Ollama (pattern-only mode)
"""
import argparse
import logging
import os
import sys

import uvicorn
from dotenv import load_dotenv

load_dotenv()

# ─── Force UTF-8 on Windows ──────────────────────────────────────────────────
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ─── Logging ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("sentinel_web.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("main")

# Quiet noisy libraries
for lib in ("httpx", "httpcore", "playwright", "apscheduler", "uvicorn.access"):
    logging.getLogger(lib).setLevel(logging.WARNING)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Sentinel Web Agent — Natural Language Web Automation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--headed",    action="store_true", help="Show browser (debug mode)")
    p.add_argument("--port",      type=int, default=int(os.getenv("PORT", "8766")),
                   help="Server port (default 8766)")
    p.add_argument("--no-ollama", action="store_true", help="Disable Ollama (pattern-only)")
    p.add_argument("--host",      default="0.0.0.0",  help="Bind host (default 0.0.0.0)")
    return p.parse_args()


def main():
    args = parse_args()

    if args.headed:
        os.environ["SENTINEL_HEADED"] = "1"
        logger.info("Headed mode: browser will be visible")

    if args.no_ollama:
        os.environ["SENTINEL_NO_OLLAMA"] = "1"
        logger.info("No-Ollama mode: pattern-based extraction only")

    logger.info("=" * 55)
    logger.info("  Sentinel Web Agent")
    logger.info(f"  Listening on http://{args.host}:{args.port}")
    logger.info(f"  API docs:   http://localhost:{args.port}/docs")
    logger.info(f"  Headed:     {args.headed}")
    logger.info(f"  Ollama:     {not args.no_ollama}")
    logger.info("=" * 55)

    # Patch BROWSER_HEADLESS based on --headed flag
    if args.headed:
        import browser as _browser_mod
        # Monkey-patch new_context viewport for headed debugging
        logger.info("Browser will open in headed mode for debugging")

    from api import app
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level="warning",
        access_log=False,
    )


if __name__ == "__main__":
    main()
