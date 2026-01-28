"""FastAPI application main module."""
import json
import logging
from typing import Any, List, Union
from fastapi import FastAPI, Request, BackgroundTasks, HTTPException, status
from fastapi.responses import JSONResponse
from app.config import settings
from app.models import SpeedingEvent
from app.security import verify_webhook_signature
from app.telegram_bot import process_alert, init_bot, close_bot

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


app = FastAPI(
    title="Safety Alert Bot",
    description="Webhook receiver for Motive speeding events with Telegram notifications",
    version="0.1.0"
)


@app.on_event("startup")
async def startup_event():
    """Initialize services on application startup."""
    await init_bot()


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup services on application shutdown."""
    await close_bot()


@app.get("/")
async def root():
    """Health check endpoint."""
    return {"status": "ok", "service": "safety-alert-bot"}


@app.get("/health")
async def health():
    """Health check endpoint for monitoring."""
    return {"status": "healthy"}


@app.post("/webhook/motive")
async def motive_webhook(
    request: Request,
    background_tasks: BackgroundTasks
):
    """
    Receive and process Motive webhook events.
    
    Security: Verifies HMAC-SHA1 signature before processing.
    Performance: Returns 200 OK immediately, processes Telegram in background.
    Filtering: Only processes 'speeding_event_created' events.
    """
    try:
        # Step 1: Read the request body (can only be read once)
        body_bytes = await request.body()
        
        # Step 2: Verify webhook signature (SECURITY FIRST)
        signature = request.headers.get("X-KT-Webhook-Signature", "")
        verify_webhook_signature(body_bytes, signature, settings.webhook_secret)
        
        # Step 3: Parse the request body
        try:
            payload: Union[List[Any], Any] = json.loads(body_bytes)
        except json.JSONDecodeError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid JSON payload"
            )

        # Normalize to a list of event dicts
        if isinstance(payload, list):
            events_raw: List[Any] = payload
            logger.info(f"Webhook batch received with {len(events_raw)} events")
        else:
            events_raw = [payload]
            logger.info(
                "Webhook event received: action=%s, id=%s",
                payload.get("action") if isinstance(payload, dict) else None,
                payload.get("id") if isinstance(payload, dict) else None,
            )

        accepted_events: List[int] = []

        # Process each event in the batch
        for raw in events_raw:
            if not isinstance(raw, dict):
                logger.error(f"Skipping non-object event payload: {raw!r}")
                continue

            action = raw.get("action")
            if action != "speeding_event_created":
                logger.info(f"Event ignored: action '{action}' not processed")
                continue

            # Validate structure
            try:
                event = SpeedingEvent.model_validate(raw)
            except Exception as e:
                logger.error(f"Invalid payload structure for event: {e}")
                continue

            # Schedule Telegram notification in background (ASYNC/NON-BLOCKING)
            background_tasks.add_task(process_alert, raw)
            accepted_events.append(event.id)

        # Step 4: Return 200 OK immediately (within 2 seconds constraint)
        if not accepted_events:
            # No events were accepted, but we still return 200 to Motive
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={
                    "status": "ignored",
                    "reason": "No qualifying 'speeding_event_created' events in payload",
                },
            )

        logger.info(f"Events queued for processing: {accepted_events}")
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "status": "accepted",
                "event_ids": accepted_events,
                "message": f"{len(accepted_events)} event(s) queued for processing",
            },
        )
        
    except HTTPException:
        # Re-raise HTTP exceptions (like 403 from signature verification)
        raise
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload"
        )
    except Exception as e:
        # Log unexpected errors
        logger.error(f"Unexpected error processing webhook: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error"
        )
