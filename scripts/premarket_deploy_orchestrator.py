#!/usr/bin/env python3
"""
Pre-Market Deployment Orchestrator

Runs before market open to:
1. Test WebSocket connectivity
2. If test passes → deploy paper-trading:v4 (WebSocket enabled)
3. If test fails → keep current version (graceful degradation handles it anyway)

Scheduled to run at 9:00 AM ET on trading days.
"""

import asyncio
import os
import sys
import logging
from datetime import datetime

import pytz

sys.path.insert(0, '/app')

from firehose.stock_price_monitor import StockPriceMonitor

# Configuration
DEPLOY_IMAGE = "us-west1-docker.pkg.dev/fl3-v2-prod/fl3-v2-images/paper-trading:v4"
FALLBACK_IMAGE = "us-west1-docker.pkg.dev/fl3-v2-prod/fl3-v2-images/paper-trading:v1"
JOB_NAME = "paper-trading-prod"
REGION = "us-west1"
PROJECT = "fl3-v2-prod"

# Test parameters
TEST_SYMBOLS = ["SPY", "AAPL", "TSLA"]
TEST_DURATION_SECONDS = 20
MIN_MESSAGES_REQUIRED = 5  # Must receive at least this many messages

ET = pytz.timezone("America/New_York")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


async def test_websocket_connectivity() -> dict:
    """
    Test WebSocket connectivity and return results.

    Returns:
        dict with keys: success, trades, quotes, error
    """
    api_key = os.environ.get("POLYGON_API_KEY")
    if not api_key:
        return {"success": False, "error": "POLYGON_API_KEY not set"}

    logger.info("=" * 60)
    logger.info("WEBSOCKET CONNECTIVITY TEST")
    logger.info("=" * 60)

    monitor = StockPriceMonitor(api_key)
    results = {"success": False, "trades": 0, "quotes": 0, "error": None}

    try:
        logger.info("Connecting to Polygon stocks WebSocket...")
        started = await monitor.start()

        if not started:
            results["error"] = "Failed to connect/authenticate"
            logger.error(f"Connection failed: {results['error']}")
            return results

        logger.info("Connected! Subscribing to test symbols...")
        await monitor.subscribe(TEST_SYMBOLS)
        logger.info(f"Subscribed to: {TEST_SYMBOLS}")

        logger.info(f"Waiting {TEST_DURATION_SECONDS}s for data...")
        await asyncio.sleep(TEST_DURATION_SECONDS)

        metrics = monitor.get_metrics()
        results["trades"] = metrics["trades_received"]
        results["quotes"] = metrics["quotes_received"]
        total_messages = results["trades"] + results["quotes"]

        logger.info(f"Results: {results['trades']} trades, {results['quotes']} quotes")

        # Check if we received enough data
        if total_messages >= MIN_MESSAGES_REQUIRED:
            results["success"] = True
            logger.info(f"TEST PASSED: Received {total_messages} messages (min: {MIN_MESSAGES_REQUIRED})")
        else:
            results["error"] = f"Insufficient data: {total_messages} < {MIN_MESSAGES_REQUIRED}"
            logger.warning(f"TEST FAILED: {results['error']}")

        # Log final prices
        for sym in TEST_SYMBOLS:
            price = monitor.get_last_price(sym)
            if price:
                logger.info(f"  {sym}: ${price:.2f}")

    except Exception as e:
        results["error"] = str(e)
        logger.error(f"Test error: {e}")

    finally:
        await monitor.stop()

    return results


def deploy_image(image: str) -> bool:
    """
    Deploy specified image to paper-trading-prod job.

    Returns:
        True if deployment succeeded
    """
    import subprocess

    logger.info(f"Deploying image: {image}")

    cmd = [
        "gcloud", "run", "jobs", "update", JOB_NAME,
        f"--image={image}",
        f"--region={REGION}",
        f"--project={PROJECT}",
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0:
            logger.info("Deployment successful!")
            return True
        else:
            logger.error(f"Deployment failed: {result.stderr}")
            return False
    except subprocess.TimeoutExpired:
        logger.error("Deployment timed out")
        return False
    except Exception as e:
        logger.error(f"Deployment error: {e}")
        return False


def get_current_image() -> str:
    """Get the current image deployed to the job."""
    import subprocess

    cmd = [
        "gcloud", "run", "jobs", "describe", JOB_NAME,
        f"--region={REGION}",
        f"--project={PROJECT}",
        "--format=value(template.template.containers[0].image)",
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception as e:
        logger.error(f"Failed to get current image: {e}")

    return "unknown"


async def main():
    """Main orchestrator logic."""
    now = datetime.now(ET)
    logger.info("=" * 60)
    logger.info("PRE-MARKET DEPLOYMENT ORCHESTRATOR")
    logger.info(f"Time: {now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    logger.info("=" * 60)

    # Check if it's a trading day (Mon-Fri)
    if now.weekday() >= 5:
        logger.info("Weekend - skipping deployment check")
        return

    # Get current deployment
    current_image = get_current_image()
    logger.info(f"Current image: {current_image}")

    # Run WebSocket test
    test_results = await test_websocket_connectivity()

    logger.info("=" * 60)
    logger.info("DEPLOYMENT DECISION")
    logger.info("=" * 60)

    if test_results["success"]:
        logger.info("WebSocket test PASSED")

        if DEPLOY_IMAGE in current_image:
            logger.info("Already running v4 - no deployment needed")
        else:
            logger.info(f"Deploying WebSocket-enabled version: {DEPLOY_IMAGE}")
            if deploy_image(DEPLOY_IMAGE):
                logger.info("✅ Deployment complete - WebSocket enabled")
            else:
                logger.error("❌ Deployment failed - will use graceful degradation")
    else:
        logger.warning(f"WebSocket test FAILED: {test_results.get('error', 'unknown')}")
        logger.info("Keeping current deployment - graceful degradation will handle this")
        logger.info("Paper trading will automatically fall back to REST polling")

    logger.info("=" * 60)
    logger.info("ORCHESTRATOR COMPLETE")
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
