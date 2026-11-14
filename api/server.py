"""
HTTP API for the Kelvin6K Voice Assistant.

Endpoints:
    /api/health           -> Server & Voice Agent health status
    /api/ask              -> Assistant query (Voice / Text)
    /api/assistant/query  -> Alias for /api/ask
    /api/assistant/stream -> SSE streaming assistant response
    /api/stt              -> faster-whisper speech-to-text
    /api/tts*             -> ElevenLabs / pyttsx3 speech synthesis
    /api/history          -> Production & query event history
    /api/operators        -> Operator management
    /api/shifts*          -> Shift & attendance tracking
    /api/production*      -> Wall production records
    /api/issues*          -> Shop-floor issue logging & resolution
    /api/handovers*       -> Shift handover orchestration
"""

import asyncio
import datetime
import io
import json
import os
import re
import sqlite3
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from agent import config, db, listener, metrics, shift_store
from agent.assistant import Assistant
from agent.logging_setup import configure_logging, get_logger
from agent.rag.retriever import index_summary

configure_logging()
log = get_logger("API")

app = FastAPI(title="Kelvin6K Voice Assistant API", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # local dashboard; tighten for production deployment
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-TTS-Engine"],
)

# Shared singleton voice assistant instance
assistant = Assistant()


@app.on_event("startup")
def _startup() -> None:
    db.init_db()

    # Preload whisper in background so first query has zero model load latency
    threading.Thread(target=listener.preload, name="whisper-preload", daemon=True).start()

    def _warm_answer_engine() -> None:
        try:
            assistant.ask("what is the machine status")
        except Exception:
            pass

    threading.Thread(target=_warm_answer_engine, name="answer-warmup", daemon=True).start()
    log.info("Kelvin6K Voice Assistant API ready; speech-to-text loading in background")


@app.on_event("shutdown")
def _shutdown() -> None:
    pass


# --- health -------------------------------------------------------------

@app.get("/api/health")
def health() -> Dict[str, Any]:
    from agent.voice import provider_name

    rag = index_summary()
    return {
        "ok": True,
        "camera_source": "none",
        "camera_status": "disabled",
        "llm": "gemini" if config.GEMINI_API_KEY else "offline-router",
        "llm_model": config.GEMINI_MODEL if config.GEMINI_API_KEY else None,
        "stt": f"faster-whisper ({config.WHISPER_MODEL_SIZE}) - {listener.get_status()}",
        "tts": provider_name(),
        "rag_chunks": rag["chunks"],
        "rag_mode": "semantic" if rag["semantic"] else "lexical",
        "calibrated": False,
        "read_only": True,
    }


@app.get("/api/history")
def history() -> Dict[str, Any]:
    """Production over the last 10 minutes / 1 hour / 8 hours / 24 hours."""
    return metrics.production_history()


@app.get("/api/metrology/announcement")
def metrology_announcement() -> Dict[str, Any]:
    """Returns the latest layer-width measurement announcement for automatic spoken updates."""
    return {
        "announcement": None,
        "enabled": False,
    }


@app.get("/api/status")
def status() -> Dict[str, Any]:
    """Returns full system, production, vision, and robot telemetry for the React dashboard."""
    now = time.time()
    active_job = db.get_active_job()
    snap = db.get_latest_machine_state() or {}

    prod = metrics.production_metrics(active_job)
    total_mm = active_job["total_length_mm"] if active_job else None
    completed_mm = (
        active_job["completed_length_mm"]
        if active_job
        else snap.get("completed_length_mm")
    )
    remaining_mm = (
        max(float(total_mm) - float(completed_mm), 0.0)
        if total_mm is not None and completed_mm is not None
        else None
    )
    pct = (
        round((float(completed_mm) / float(total_mm)) * 100.0, 1)
        if total_mm and completed_mm is not None and float(total_mm) > 0
        else None
    )

    reason_no_job = "No active print job is being tracked."
    prod_full = {
        "job_name": metrics.field(active_job["job_name"], metrics.REAL) if active_job else metrics.unavailable(reason_no_job),
        "total_mm": metrics.field(total_mm, metrics.REAL, "mm") if total_mm is not None else metrics.unavailable(reason_no_job, "mm"),
        "completed_mm": metrics.field(completed_mm, metrics.REAL, "mm") if completed_mm is not None else metrics.unavailable(reason_no_job, "mm"),
        "remaining_mm": metrics.field(remaining_mm, metrics.REAL, "mm") if remaining_mm is not None else metrics.unavailable(reason_no_job, "mm"),
        "percentage": metrics.field(pct, metrics.REAL, "%") if pct is not None else metrics.unavailable(reason_no_job, "%"),
        "current_layer": metrics.field(snap.get("current_layer") or (active_job.get("current_layer") if active_job else None), metrics.REAL),
        "total_layers": metrics.field(active_job.get("total_layers") if active_job else None, metrics.REAL),
        "elapsed": prod.get("elapsed", metrics.unavailable(reason_no_job)),
        "elapsed_seconds": prod.get("elapsed_seconds", metrics.unavailable(reason_no_job, "s")),
        "print_rate_mm_per_min": prod.get("print_rate_mm_per_min", metrics.unavailable(reason_no_job, "mm/min")),
        "eta": prod.get("eta", metrics.unavailable(reason_no_job)),
        "eta_timestamp": prod.get("eta_timestamp", metrics.unavailable(reason_no_job)),
        "deadline": prod.get("deadline", metrics.unavailable(reason_no_job)),
        "deadline_status": prod.get("deadline_status", metrics.unavailable(reason_no_job)),
        "time_to_deadline": prod.get("time_to_deadline", metrics.unavailable(reason_no_job)),
    }

    geom = metrics.geometry_metrics(active_job, snap)
    printing_val = snap.get("printing")
    printing_status = snap.get("status") or ("PRINTING" if printing_val else "IDLE")

    vision_status = {
        "printing": metrics.field(bool(printing_val), metrics.REAL) if printing_val is not None else metrics.unavailable("Vision loop inactive."),
        "print_status": metrics.field(printing_status, metrics.REAL),
        "layer_number": metrics.field(snap.get("current_layer"), metrics.REAL),
        "layer_width_mm": geom.get("layer_width_mm", metrics.unavailable("No layer width measured.", "mm")),
        "layer_width_px": geom.get("layer_width_px", metrics.unavailable("No pixel measurement.", "px")),
        "confidence": metrics.field(snap.get("confidence"), metrics.REAL) if snap.get("confidence") is not None else metrics.unavailable("No confidence reading."),
        "motion_score": metrics.field(snap.get("motion_score"), metrics.REAL) if snap.get("motion_score") is not None else metrics.unavailable("No motion reading."),
        "note": None,
    }

    machine_telemetry = {
        "pump_running": metrics.unavailable("No machine telemetry link connected."),
        "pump_speed_rpm": metrics.unavailable("No machine telemetry link connected.", "rpm"),
        "accelerator_running": metrics.unavailable("No machine telemetry link connected."),
        "accelerator_speed_rpm": metrics.unavailable("No machine telemetry link connected.", "rpm"),
        "print_speed_mm_s": metrics.unavailable("No machine telemetry link connected.", "mm/s"),
        "pressure_bar": metrics.unavailable("No machine telemetry link connected.", "bar"),
    }

    mat = metrics.material_metrics(active_job, prod_full.get("elapsed_seconds", {}).get("value"))
    material_status = {
        "material_flow_percent": metrics.unavailable("No material flow sensor.", "%"),
        "nozzle_temp_c": metrics.unavailable("No nozzle temp sensor.", "deg C"),
        "bed_temp_c": metrics.unavailable("No bed temp sensor.", "deg C"),
        "machine_state": metrics.field("standby", metrics.REAL),
        "material_type": mat.get("material_type", metrics.unavailable("No material recorded.")),
        "material_used_kg": mat.get("material_used_kg", metrics.unavailable("No sensor.", "kg")),
        "material_remaining_kg": mat.get("material_remaining_kg", metrics.unavailable("No sensor.", "kg")),
        "material_ratio_percent": mat.get("material_ratio_percent", metrics.unavailable("No sensor.", "%")),
        "consumption_rate_kg_per_hour": mat.get("consumption_rate_kg_per_hour", metrics.unavailable("No sensor.", "kg/h")),
    }

    robot_info = {
        "robot_online": metrics.field(True, metrics.REAL),
        "robot_status": metrics.field("READY", metrics.REAL),
        "machine_state": metrics.field("IDLE", metrics.REAL),
        "current_operation": metrics.field("MONITORING", metrics.REAL),
        "current_shift": metrics.current_shift(),
        "current_operator": metrics.current_operator(active_job),
        "communication": {
            "api": metrics.field("CONNECTED", metrics.REAL),
            "camera": metrics.field("READY", metrics.REAL),
            "machine_link": metrics.field("STANDBY", metrics.REAL),
        },
    }

    problems = {
        "warnings": [],
        "critical": [],
        "machine": [],
        "camera": [],
        "counts": {"warnings": 0, "critical": 0, "machine": 0, "camera": 0},
    }

    return {
        "timestamp": now,
        "camera": {
            "status": "ready",
            "source": config.CAMERA_SOURCE,
            "fps": float(config.CAMERA_FPS),
            "resolution": [config.CAMERA_WIDTH, config.CAMERA_HEIGHT],
            "error": None,
            "provenance": metrics.REAL,
        },
        "camera2": None,
        "people_count": None,
        "production": prod_full,
        "vision": vision_status,
        "geometry": geom,
        "material": material_status,
        "machine_telemetry": machine_telemetry,
        "robot": robot_info,
        "problems": problems,
        "alerts": [],
        "calibrated": bool(config.PIXELS_PER_MM > 0),
    }


# --- assistant (shared by text AND voice) -------------------------------

class AskRequest(BaseModel):
    question: str
    speak: bool = False  # server-side TTS; the UI plays audio in the browser
    session_id: str = "default_terminal"
    line_id: str = "line_1"
    operator_id: Optional[str] = None
    stream: bool = False


class AskResponse(BaseModel):
    question: str
    answer: str
    tool: Optional[str] = None
    intent: Optional[str] = None
    facts: Optional[Dict[str, Any]] = None
    knowledge_sources: Optional[list] = None
    data_available: bool = True
    llm: str
    timestamp: float
    attendance_updated: Optional[bool] = False
    attachments: Optional[List[Dict[str, Any]]] = None
    fallback_used: Optional[bool] = False
    rag_score: Optional[float] = None
    primary_image_id: Optional[str] = None
    annotated_image_id: Optional[str] = None
    reference_image_id: Optional[str] = None
    reference_component: Optional[Dict[str, Any]] = None


def _stream_ask_response(request: AskRequest):
    question = (request.question or "").strip()
    full_chunks = []
    _t0 = time.time()
    try:
        for chunk in assistant.ask_stream(
            question,
            session_id=request.session_id,
            line_id=request.line_id,
            operator_id=request.operator_id,
        ):
            full_chunks.append(chunk)
            payload = json.dumps({"chunk": chunk, "done": False})
            yield f"data: {payload}\n\n"
    except Exception as exc:
        log.warning("Stream error: %s", exc)
        yield f"data: {json.dumps({'error': str(exc), 'done': True})}\n\n"
        return

    full_answer = "".join(full_chunks)
    log.info("[TIMING] Stream completed in %.3fs for query: %s", time.time() - _t0, question)

    # Server-side TTS run in a background thread so the HTTP response is never blocked
    if request.speak and full_answer:
        def _bg_speak_stream(text: str):
            try:
                from agent.voice import speak as server_speak
                server_speak(text)
            except Exception as exc:
                log.warning("Server-side TTS stream failed in background: %s", exc)

        threading.Thread(target=_bg_speak_stream, args=(full_answer,), daemon=True).start()

    final_payload = json.dumps({
        "chunk": "",
        "done": True,
        "answer": full_answer,
        "tool": Assistant.route(question),
        "llm": "gemini" if assistant.use_llm else "offline-router",
        "timestamp": time.time(),
    })
    yield f"data: {final_payload}\n\n"


@app.post("/api/assistant/query", response_model=AskResponse)
@app.post("/api/ask", response_model=AskResponse)
async def ask(request: Request):
    """
    THE shared query service. Both the text box and the voice pipeline call
    this, so a typed and a spoken question follow exactly the same path.
    Optimized for instantaneous time-to-first-token and non-blocking background tasks.
    Supports both JSON and multipart/form-data (with file uploads).
    """
    content_type = request.headers.get("content-type", "").lower()
    files_list: List[UploadFile] = []
    
    if "multipart/form-data" in content_type:
        form = await request.form()
        question = str(form.get("question") or "").strip()
        speak_val = form.get("speak")
        speak = str(speak_val).lower() in ("true", "1") if speak_val is not None else False
        session_id = str(form.get("session_id") or "default_terminal")
        line_id = str(form.get("line_id") or "line_1")
        op_id_val = form.get("operator_id")
        operator_id = str(op_id_val) if op_id_val else None
        stream = False
        raw_files = form.getlist("files")
        for item in raw_files:
            if hasattr(item, "filename") and item.filename:
                files_list.append(item)
    else:
        try:
            body = await request.json()
        except Exception:
            body = {}
        question = str(body.get("question") or "").strip()
        speak = bool(body.get("speak", False))
        session_id = str(body.get("session_id") or "default_terminal")
        line_id = str(body.get("line_id") or "line_1")
        operator_id = body.get("operator_id")
        stream = bool(body.get("stream", False))

    if not question and not files_list:
        raise HTTPException(status_code=400, detail="question must not be empty")

    if not question and files_list:
        question = "Analyze the attached file(s) for industrial defects or status."

    req_obj = AskRequest(
        question=question,
        speak=speak,
        session_id=session_id,
        line_id=line_id,
        operator_id=operator_id,
        stream=stream,
    )

    if stream:
        return StreamingResponse(
            _stream_ask_response(req_obj),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # If file attachments are present, route to troubleshooter
    if files_list:
        from agent import troubleshooter
        processed_attachments = []
        for uf in files_list:
            content = await uf.read()
            if not content:
                continue
            try:
                att = troubleshooter.validate_and_process_attachment(
                    content,
                    filename=uf.filename,
                    content_type=uf.content_type,
                )
                processed_attachments.append(att)
            except Exception as att_err:
                log.warning("Attachment validation error for %s: %s", uf.filename, att_err)

        if processed_attachments:
            log.info("[QUERY-ATTACHMENTS] Running multimodal troubleshooting with %d file(s)", len(processed_attachments))
            _t_start = time.time()
            res = troubleshooter.troubleshoot(
                question=question,
                attachments=processed_attachments,
                session_id=session_id,
                line_id=line_id,
                operator_id=operator_id,
            )
            log.info("[TIMING] Troubleshoot completed in %.3fs", time.time() - _t_start)

            if speak:
                def _bg_speak_ts(text_to_speak: str):
                    try:
                        from agent.voice import speak as server_speak
                        server_speak(text_to_speak)
                    except Exception as exc:
                        log.warning("Server-side TTS failed in background: %s", exc)

                threading.Thread(target=_bg_speak_ts, args=(res.answer,), daemon=True).start()

            att_infos = [
                {
                    "filename": a.filename,
                    "content_type": a.content_type,
                    "size_bytes": a.size_bytes,
                    "kind": a.kind,
                    "observations": a.observations,
                    "width": a.width,
                    "height": a.height,
                }
                for a in processed_attachments
            ]

            return AskResponse(
                question=question,
                answer=res.answer,
                tool="multimodal_troubleshooter",
                intent="DIAGNOSTIC",
                facts=None,
                knowledge_sources=res.citations or None,
                data_available=True,
                llm="gemini-multimodal" if res.gemini_called else "rag-knowledge",
                timestamp=time.time(),
                attendance_updated=False,
                attachments=att_infos,
                fallback_used=res.fallback_used,
                rag_score=res.rag_score,
                primary_image_id=res.primary_image_id,
                annotated_image_id=res.annotated_image_id,
                reference_image_id=res.reference_image_id,
                reference_component=res.reference_component if isinstance(res.reference_component, dict) else None,
            )

    from agent import query_router

    log.info("[QUERY] Question: %s", question)
    _t_start = time.time()

    if assistant.use_llm:
        answer = assistant.ask(
            question,
            session_id=session_id,
            line_id=line_id,
            operator_id=operator_id,
        )
        tool_name = "gemini_agent"
        intent_val = "CONVERSATIONAL"
        facts_data = None
        knowledge_sources = None
        data_available = True
    else:
        # Fast path for simple greetings and casual conversational turns when offline
        pending = assistant._actions.get(session_id, line_id)
        casual_reply = Assistant._is_casual_or_greeting(question) if not pending else None

        if casual_reply:
            answer = casual_reply
            tool_name = "casual_greeting"
            intent_val = "WAKE_WORD"
            facts_data = None
            knowledge_sources = None
            data_available = True
        else:
            # Route deterministically when offline without LLM
            routed = query_router.route(
                question,
                session_id=session_id,
                use_agentic=False,
                context={
                    "session_id": session_id,
                    "line_id": line_id,
                    "operator_id": operator_id,
                },
            )
            answer = assistant.ask(
                question,
                session_id=session_id,
                line_id=line_id,
                operator_id=operator_id,
                routed=routed,
            )
            tool_name = Assistant.route(question)
            intent_val = routed.intent.value
            facts_data = routed.facts or None
            knowledge_sources = (
                [r["source"] for r in routed.knowledge["results"][:3]]
                if routed.knowledge and routed.knowledge.get("results")
                else None
            )
            data_available = routed.available

    log.info("[TIMING] Total ask response time: %.3fs", time.time() - _t_start)
    log.info("[ASSISTANT] Answer: %s", answer)

    # Check if this query created or updated shifts / attendance
    attendance_modified = False
    lower_ans = answer.lower()
    lower_q = question.lower()
    if any(kw in lower_ans for kw in ("shift started", "started shift", "shift ended", "ended shift", "clocked in", "clocked out")):
        attendance_modified = True
    elif any(kw in lower_q for kw in ("start shift", "end shift", "start my shift", "end my shift", "clock in", "clock out", "check in", "check out")):
        attendance_modified = True
    elif intent_val in ("SHIFT_START", "SHIFT_END"):
        attendance_modified = True

    # Server-side TTS executed asynchronously in a background thread to prevent HTTP response lag
    if speak:
        def _bg_speak(text_to_speak: str):
            try:
                from agent.voice import speak as server_speak
                server_speak(text_to_speak)
            except Exception as exc:
                log.warning("Server-side TTS failed in background: %s", exc)

        threading.Thread(target=_bg_speak, args=(answer,), daemon=True).start()

    return AskResponse(
        question=question,
        answer=answer,
        tool=tool_name,
        intent=intent_val,
        facts=facts_data,
        knowledge_sources=knowledge_sources,
        data_available=data_available,
        llm="gemini" if assistant.use_llm else "offline-router",
        timestamp=time.time(),
        attendance_updated=attendance_modified,
    )


@app.post("/api/assistant/stream")
def assistant_stream(request: AskRequest):
    """Dedicated endpoint to stream conversational chunks with minimal time-to-first-token."""
    question = (request.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question must not be empty")
    return StreamingResponse(
        _stream_ask_response(request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/assistant/stream")
def assistant_stream_get(
    question: str,
    session_id: str = "default_terminal",
    line_id: str = "line_1",
    operator_id: Optional[str] = None,
    speak: bool = False,
):
    """GET endpoint for Server-Sent Events (SSE) streaming directly from browser EventSource."""
    req = AskRequest(
        question=question,
        session_id=session_id,
        line_id=line_id,
        operator_id=operator_id,
        speak=speak,
        stream=True,
    )
    return assistant_stream(req)


# --- speech to text (reuses the existing faster-whisper pipeline) -------

def _probe_audio(path: str):
    """
    Measures an audio file by DECODING it: (duration_seconds, peak, codec).

    Why not the container header: the WebM that a browser's MediaRecorder
    produces is written as a live stream, so its header carries no Duration
    element at all. `container.duration` is therefore None for every browser
    recording, however long it actually is -- and treating that as 0 rejected
    perfectly good audio as "too short". Counting decoded samples is the only
    reliable measure, and it yields the peak amplitude for free, which
    separates "no audio" from "silent audio".
    """
    import av
    import numpy as np

    total_samples = 0
    sample_rate = 0
    peak = 0.0
    codec = "unknown"

    with av.open(path) as container:
        streams = [st for st in container.streams if st.type == "audio"]
        if not streams:
            raise ValueError("no audio stream in container")
        stream = streams[0]
        codec = stream.codec_context.name or "unknown"
        sample_rate = stream.codec_context.sample_rate or getattr(stream, "rate", 0) or 0

        for frame in container.decode(audio=0):
            total_samples += frame.samples
            if not sample_rate:
                sample_rate = frame.sample_rate or 0
            array = frame.to_ndarray()
            if array.size:
                magnitude = float(np.abs(array).max())
                # Integer formats are full-scale; normalise so `peak` is 0..1
                # regardless of whether the decoder gave int16 or float32.
                if array.dtype.kind in "iu":
                    magnitude /= float(np.iinfo(array.dtype).max)
                peak = max(peak, magnitude)

    duration = (total_samples / float(sample_rate)) if sample_rate else 0.0
    return duration, peak, codec


@app.post("/api/stt")
async def speech_to_text(audio: UploadFile = File(...)) -> Dict[str, Any]:
    """
    Transcribes uploaded audio with the project's existing faster-whisper model.

    Accepts whatever the browser's MediaRecorder produces (webm/opus, ogg, mp4)
    plus wav/mp3: the audio is written to a temp file and decoded by PyAV inside
    faster-whisper, so voice input works in every browser.
    """
    payload = await audio.read()
    # Full request trace: every one of these lines has, at some point, been the
    # one that identified a failure, so they all stay.
    log.info("[STT] --- request received ---")
    log.info("[STT] filename     : %s", audio.filename or "(none)")
    log.info("[STT] content type : %s", audio.content_type or "(none)")
    log.info("[STT] upload size  : %d bytes", len(payload))
    if not payload:
        log.error("[STT ERROR] Microphone capture failed -- 0 bytes received")
        raise HTTPException(status_code=400, detail="MICROPHONE CAPTURE FAILED: 0 audio bytes received")

    suffix = os.path.splitext(audio.filename or "")[1] or ".webm"
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
            handle.write(payload)
            temp_path = handle.name
        log.info("[STT] temp file    : %s", temp_path)
        log.info(
            "[STT] file on disk : exists=%s, %d bytes",
            os.path.exists(temp_path),
            os.path.getsize(temp_path) if os.path.exists(temp_path) else -1,
        )

        from agent.listener import transcribe
        # Decode-level validation: a container header with no audio frames
        # (the classic "recording stopped instantly" case) has ~0 duration and
        # must be reported as such rather than silently returning nothing.
        try:
            probe_duration, peak, codec = _probe_audio(temp_path)
            log.info(
                "[STT] Audio format: %s, duration: %.2fs, peak amplitude: %.4f",
                codec, probe_duration, peak,
            )

            if probe_duration < 0.3:
                log.error("[STT ERROR] Only %.2fs of audio decoded -- no usable speech", probe_duration)
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Only {probe_duration:.2f} seconds of audio arrived. "
                        "Click START LISTENING, wait for LISTENING, then speak your question."
                    ),
                )
            # Real duration but a flat waveform means the microphone delivered
            # digital silence -- a different problem with a different fix, so it
            # must not be reported as "too short".
            if peak <= 0.0005:
                log.error("[STT ERROR] %.2fs of audio but peak amplitude %.5f -- silence", probe_duration, peak)
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"The recording is {probe_duration:.1f} seconds of silence. "
                        "The microphone is open but Windows is sending no sound. "
                        "Run the microphone diagnostic in the assistant panel."
                    ),
                )
        except HTTPException:
            raise
        except Exception as exc:
            log.warning("[STT] Could not probe audio (%s) -- letting Whisper try anyway", exc)

        log.info("[STT] Transcribing with faster-whisper (%s)...", config.WHISPER_MODEL_SIZE)
        started = time.time()
        text, info = transcribe(temp_path, return_info=True)
        stt_seconds = time.time() - started
        log.info(
            "[TIMING] STT              : %.2fs  (language=%s)",
            stt_seconds, getattr(info, "language", "?"),
        )
        log.info("[STT] Transcript   : %r", text)
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("[STT ERROR] Transcription raised %s", type(exc).__name__)
        # Most common cause by far: a container header with no audio frames,
        # i.e. the recording was stopped before any speech was captured.
        message = str(exc)
        if "Invalid data" in message or "moov atom" in message or "EBML" in message:
            log.error("[STT ERROR] Invalid/unsupported audio format -- likely an empty recording")
            raise HTTPException(
                status_code=422,
                detail=(
                    "The recording contained no usable audio (only a file header). "
                    "Click START LISTENING, speak for 2-5 seconds, then stop."
                ),
            )
        log.error("[STT ERROR] %s", message)
        raise HTTPException(status_code=400, detail=f"could not transcribe audio: {message}")
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass

    # Whisper hallucinates stock phrases when handed silence or noise ("You",
    # "Thank you.", "Thanks for watching!"). Those must never reach the query
    # router, or a silent recording yields a confident answer to a question the
    # operator never asked.
    _NOISE_TRANSCRIPTS = {
        "you", "thank you", "thanks for watching", "thanks for watching!",
        "bye", "bye.", "silence", ".", "...", "um", "uh",
    }
    normalised = text.lower().strip(" .!?,")
    valid_short = {"no", "na", "ha", "ok", "hi", "ya"}

    if not text or normalised in _NOISE_TRANSCRIPTS or (len(normalised) < 3 and normalised not in valid_short):
        log.error("[STT ERROR] Empty or noise-only transcript: %r", text)
        raise HTTPException(
            status_code=422,
            detail="I couldn't hear a clear question. Please try again.",
        )

    log.info("[STT] Transcript: %s", text)

    return {
        "text": text,
        "transcript": text,
        "engine": f"faster-whisper ({config.WHISPER_MODEL_SIZE})",
        "language": getattr(info, "language", None),
        "confidence": None,
        "bytes_received": len(payload),
    }


# --- text to speech -----------------------------------------------------

class SpeakRequest(BaseModel):
    text: str


@app.post("/api/tts")
def text_to_speech(request: SpeakRequest) -> Dict[str, Any]:
    """
    Speaks through the SERVER's speaker (agent/voice.py).

    Only useful when browser and backend are the same machine. The dashboard
    prefers /api/tts/audio, which returns the audio so the BROWSER plays it on
    the operator's own speakers.
    """
    text = (request.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text must not be empty")

    from agent.voice import TTSError, provider_name, speak as server_speak

    log.info("[TTS] Sending answer to speak(): %s", text)
    _t_tts = time.time()
    try:
        ok = server_speak(text, raise_on_error=True)
        log.info("[TIMING] TTS (spoken)     : %.2fs", time.time() - _t_tts)
    except TTSError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {"spoken": bool(ok), "text": text, "engine": provider_name()}


@app.post("/api/tts/audio")
def text_to_speech_audio(request: SpeakRequest):
    """
    Returns synthesised speech AUDIO for the browser to play.

    This is the architecture the dashboard uses:
        Python TTS -> audio bytes -> HTTP -> <audio> -> operator's speaker

    It does not depend on the server having a usable output device, and avoids
    the browser's speechSynthesis, which fails silently in several situations.
    """
    text = (request.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text must not be empty")

    from agent.voice import TTSError, synthesize

    try:
        path, mime = synthesize(text)
    except TTSError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        log.warning("TTS synthesis failed: %s", exc)
        raise HTTPException(status_code=503, detail=f"TTS failed: {exc}")

    try:
        with open(path, "rb") as handle:
            audio_bytes = handle.read()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

    return Response(
        content=audio_bytes,
        media_type=mime,
        headers={
            "Cache-Control": "no-store",
            "X-TTS-Engine": "elevenlabs" if config.ELEVENLABS_API_KEY else "pyttsx3",
        },
    )


@app.get("/api/tts/diagnostics")
def tts_diagnostics() -> Dict[str, Any]:
    """TTS health: provider, installed voices, volume. No secrets."""
    from agent.voice import diagnostics

    return diagnostics()


# --- Phase 2C: Shift & Production Data API -----------------------------

class CreateOperatorRequest(BaseModel):
    operator_id: Optional[str] = None
    name: str
    employee_code: Optional[str] = None
    Employee_id: Optional[str] = None
    role: str = "operator"
    active: int = 1


class UpdateEmployeeIdRequest(BaseModel):
    operator_name_or_id: str
    new_employee_id: str


class StartShiftRequest(BaseModel):
    operator_id: str
    shift_type: str  # "Morning", "Afternoon", "Night"
    shift_date: Optional[str] = None
    line_id: str = "line_1"
    target_walls: int = 0
    summary_notes: Optional[str] = None


class EndShiftRequest(BaseModel):
    status: str = "closed"  # "closed", "flagged", "abandoned"
    summary_notes: Optional[str] = None


class RecordProductionRequest(BaseModel):
    shift_id: int
    wall_number: int
    job_id: Optional[int] = None
    print_file: Optional[str] = None
    wall_height: float = 0.0
    segment_layers: int = 0
    layers_completed: int = 0
    status: str = "in_progress"  # in_progress, completed, aborted, defect
    is_continuation_of: Optional[int] = None
    notes: Optional[str] = None


class LogIssueRequest(BaseModel):
    shift_id: int
    issue_type: str
    description: str
    wall_number: Optional[int] = None
    cause: Optional[str] = None
    action_taken: Optional[str] = None
    result: Optional[str] = None
    severity: str = "warning"
    downtime_minutes: float = 0.0


class InitiateHandoverRequest(BaseModel):
    from_shift_id: int
    line_id: str = "line_1"
    completed_work: Optional[str] = None
    pending_work: Optional[str] = None
    open_issues: Optional[str] = None
    notes: Optional[str] = None


class CompleteHandoverRequest(BaseModel):
    to_shift_id: int
    notes: Optional[str] = None
    close_from_shift: bool = True


@app.get("/api/operators")
def get_operators(active_only: bool = True) -> Dict[str, Any]:
    """Lists operators, optionally filtering by active."""
    return {"operators": shift_store.list_operators(active_only=active_only)}


@app.post("/api/operators")
def create_operator(req: CreateOperatorRequest) -> Dict[str, Any]:
    """Creates a new operator."""
    eid = req.Employee_id.strip() if req.Employee_id and req.Employee_id.strip() else None
    code = req.employee_code.strip() if req.employee_code and req.employee_code.strip() else None

    # Format validations
    if eid and not re.fullmatch(r"K6K\d+", eid.upper()):
        raise HTTPException(status_code=400, detail="Employee ID must be in the format of K6K followed by numbers (e.g. K6K001).")
    if code and not re.fullmatch(r"\d+", code):
        raise HTTPException(status_code=400, detail="Employee code must contain only numbers.")

    try:
        res = shift_store.create_or_update_employee(
            name=req.name,
            role=req.role,
            status="Active" if req.active else "Inactive",
            Employee_id=eid.upper() if eid else None,
            employee_code=code,
        )
        if not res.get("success"):
            raise HTTPException(status_code=400, detail=res.get("error", "Failed to create employee."))
        op_rec = res.get("operator") or {}
        return {"success": True, "operator_id": op_rec.get("operator_id"), "operator": op_rec}
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail=f"Operator already exists or employee code conflict: {exc}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/operators/update-id")
def update_operator_id(req: UpdateEmployeeIdRequest) -> Dict[str, Any]:
    """Updates an employee's Employee_id to a new ID, and clears their employee_code."""
    eid = req.new_employee_id.strip()
    if not re.fullmatch(r"K6K\d+", eid.upper()):
        raise HTTPException(status_code=400, detail="Employee ID must be in the format of K6K followed by numbers (e.g. K6K001).")
    res = shift_store.update_employee_id(req.operator_name_or_id, eid.upper())
    if not res.get("success"):
        raise HTTPException(status_code=400, detail=res.get("error", "Failed to update Employee ID."))
    return res


@app.delete("/api/operators/{operator_id}")
def delete_operator(operator_id: str) -> Dict[str, Any]:
    """Deletes an operator and cascades deletion across shift records."""
    success = shift_store.delete_operator(operator_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Operator '{operator_id}' not found.")
    return {"success": True, "message": f"Operator '{operator_id}' deleted successfully."}


@app.get("/api/shifts/active")
def get_active_shift(line_id: str = "line_1") -> Dict[str, Any]:
    """Gets the currently active shift on the specified line."""
    active = shift_store.get_active_shift_for_line(line_id=line_id)
    if not active:
        return {"active": False, "shift": None}
    op = shift_store.get_operator(active["operator_id"])
    return {
        "active": True,
        "shift": active,
        "operator": op,
    }


@app.post("/api/shifts/start")
def start_shift(req: StartShiftRequest) -> Dict[str, Any]:
    """Starts a new shift on a line. Blocks if line already has an active shift."""
    existing = shift_store.get_active_shift_for_line(req.line_id)
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Line '{req.line_id}' already has an active shift (ID {existing['shift_id']}). Close it first.",
        )
    op = shift_store.get_operator(req.operator_id)
    if not op or not op.get("active"):
        raise HTTPException(status_code=400, detail=f"Operator '{req.operator_id}' not found or is inactive.")
    shift_date = req.shift_date or datetime.date.today().isoformat()
    try:
        shift_id = shift_store.start_shift(
            operator_id=req.operator_id,
            shift_date=shift_date,
            shift_type=req.shift_type,
            line_id=req.line_id,
            target_walls=req.target_walls,
            summary_notes=req.summary_notes,
        )
        return {
            "success": True,
            "shift_id": shift_id,
            "line_id": req.line_id,
            "operator_id": req.operator_id,
            "shift_type": req.shift_type,
            "shift_date": shift_date,
        }
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail=f"Unique active shift constraint violated: {exc}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/shifts/{shift_id}/end")
def end_shift(shift_id: int, req: EndShiftRequest) -> Dict[str, Any]:
    """Ends a shift and updates its status."""
    shift = shift_store.get_shift(shift_id)
    if not shift:
        raise HTTPException(status_code=404, detail=f"Shift {shift_id} not found.")
    try:
        ok = shift_store.end_shift(
            shift_id=shift_id,
            status=req.status,
            summary_notes=req.summary_notes,
        )
        return {"success": ok, "shift_id": shift_id, "status": req.status}
    except (sqlite3.IntegrityError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/shifts")
def list_shifts(
    shift_date: Optional[str] = None,
    shift_type: Optional[str] = None,
    operator_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
) -> Dict[str, Any]:
    """Lists shifts matching criteria."""
    shifts = shift_store.list_shifts(
        shift_date=shift_date,
        shift_type=shift_type,
        operator_id=operator_id,
        status=status,
        limit=limit,
    )
    return {"shifts": shifts, "count": len(shifts)}


@app.get("/api/reports/attendance")
def get_attendance_report(
    date: Optional[str] = None,
    operator_id: Optional[str] = None,
    limit: int = 200,
) -> Dict[str, Any]:
    """
    Fetches comprehensive attendance report joined from database shifts & operators.
    When a date is set, EVERY employee in the system is listed with their Present/Absent status:
      - 'Present' if the employee started or completed a shift on that date.
      - 'Absent' if the employee has no shift on that date.
    When no date is set, returns historical shift records marked Present.
    """
    now_ts = time.time()

    def _fmt_clock_time(val: Any, fallback_ts: Optional[float] = None) -> str:
        if val:
            val_s = str(val).strip()
            if " " in val_s:
                val_s = val_s.split(" ")[1]
            try:
                parts = val_s.split(":")
                h, m = int(parts[0]), int(parts[1])
                s = int(parts[2]) if len(parts) > 2 else 0
                return datetime.time(h, m, s).strftime("%I:%M %p")
            except Exception:
                return val_s
        if fallback_ts:
            try:
                return datetime.datetime.fromtimestamp(fallback_ts).strftime("%I:%M %p")
            except Exception:
                pass
        return "-"

    with db.connect() as conn:
        all_ops = [
            dict(r)
            for r in conn.execute(
                "SELECT operator_id, name, Employee_id, employee_code FROM operators ORDER BY operator_id ASC"
            ).fetchall()
        ]

        if date:
            target_date = date.strip()
            # Query all shifts on this date
            shift_rows = [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT s.*, COALESCE(o.name, s.operator_name) AS resolved_name
                    FROM shifts s
                    LEFT JOIN operators o ON CAST(s.operator_id AS TEXT) = CAST(o.operator_id AS TEXT)
                                           OR s.operator_id = o.Employee_id
                                           OR s.operator_id = o.employee_code
                    WHERE s.shift_date = ?
                    ORDER BY s.shift_id DESC
                    """,
                    (target_date,),
                ).fetchall()
            ]

            # Index shifts by operator
            shifts_by_op: Dict[str, List[Dict[str, Any]]] = {}
            for s in shift_rows:
                op_key = str(s["operator_id"])
                shifts_by_op.setdefault(op_key, []).append(s)
                if s.get("employee_code"):
                    shifts_by_op.setdefault(str(s["employee_code"]), []).append(s)

            records = []
            for o in all_ops:
                op_id_str = str(o["operator_id"])
                op_eid = str(o["Employee_id"]) if o.get("Employee_id") else None
                op_code = str(o["employee_code"]) if o.get("employee_code") else None

                matched_shifts = (
                    shifts_by_op.get(op_id_str)
                    or (shifts_by_op.get(op_eid) if op_eid else None)
                    or (shifts_by_op.get(op_code) if op_code else None)
                    or []
                )

                if matched_shifts:
                    for s in matched_shifts:
                        st_epoch = s.get("start_time")
                        et_epoch = s.get("end_time")
                        is_active = (s.get("status") or "").lower() in ("open", "active") or et_epoch is None
                        total_hrs = s.get("total_hours")
                        if total_hrs is None:
                            if st_epoch:
                                total_hrs = round(max(0.0, (et_epoch or now_ts) - st_epoch) / 3600.0, 2)
                            else:
                                total_hrs = 0.0
                        ot_hrs = s.get("overtime_hours")
                        if ot_hrs is None:
                            ot_hrs = max(0.0, round(float(total_hrs) - 9.0, 2)) if total_hrs else 0.0

                        records.append({
                            "shift_id": s["shift_id"],
                            "operator_id": o["operator_id"],
                            "id": o.get("Employee_id") or "-",
                            "code": o.get("employee_code") or "-",
                            "name": o["name"],
                            "start_time": _fmt_clock_time(s.get("actual_start_time"), st_epoch),
                            "end_time": "-" if is_active else _fmt_clock_time(s.get("actual_end_time"), et_epoch),
                            "overtime": f"{float(ot_hrs):.2f} hrs" if float(ot_hrs) > 0 else "0.00 hrs",
                            "overtime_num": float(ot_hrs),
                            "date": target_date,
                            "total_hours": f"{float(total_hrs):.2f} hrs",
                            "total_hours_num": float(total_hrs),
                            "presence": "Present",
                            "status": "ACTIVE" if is_active else "COMPLETED",
                            "attendance_status": s.get("attendance_status") or ("On Time" if not is_active else "Active"),
                        })
                else:
                    # Employee has no shift on this date -> Absent
                    records.append({
                        "shift_id": f"absent_{o['operator_id']}_{target_date}",
                        "operator_id": o["operator_id"],
                        "id": o.get("Employee_id") or "-",
                        "code": o.get("employee_code") or "-",
                        "name": o["name"],
                        "start_time": "-",
                        "end_time": "-",
                        "overtime": "0.00 hrs",
                        "overtime_num": 0.0,
                        "date": target_date,
                        "total_hours": "0.00 hrs",
                        "total_hours_num": 0.0,
                        "presence": "Absent",
                        "status": "ABSENT",
                        "attendance_status": "Absent",
                    })

            # Optional filter by operator_id if requested
            if operator_id:
                clean_target = str(operator_id).strip().lower()
                records = [
                    r for r in records
                    if clean_target in (
                        str(r["operator_id"]).lower(),
                        str(r["id"]).lower(),
                        str(r["code"]).lower(),
                        r["name"].lower(),
                    )
                ]

            return {
                "report": records,
                "count": len(records),
                "total_work_hours": round(sum(r["total_hours_num"] for r in records), 2),
                "total_overtime_hours": round(sum(r["overtime_num"] for r in records), 2),
            }

        # No date filter set: return full history of shifts with presence = 'Present'
        query = """
            SELECT 
                s.shift_id,
                s.operator_id,
                COALESCE(o.Employee_id, s.operator_id) AS Employee_id,
                o.employee_code,
                COALESCE(o.name, s.operator_name, 'Operator ' || s.operator_id) AS name,
                s.actual_start_time,
                s.actual_end_time,
                s.start_time,
                s.end_time,
                s.shift_date,
                s.total_hours,
                s.overtime_hours,
                s.status,
                s.attendance_status
            FROM shifts s
            LEFT JOIN operators o ON CAST(s.operator_id AS TEXT) = CAST(o.operator_id AS TEXT)
                                   OR s.operator_id = o.Employee_id
                                   OR s.operator_id = o.employee_code
        """
        clauses, params = [], []
        if operator_id:
            clauses.append("(s.operator_id = ? OR o.Employee_id = ? OR o.employee_code = ?)")
            params.extend([str(operator_id).strip()] * 3)

        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY s.shift_id DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()

    records = []
    for r in rows:
        d = dict(r)
        st_epoch = d.get("start_time")
        et_epoch = d.get("end_time")
        is_active = (d.get("status") or "").lower() in ("open", "active") or et_epoch is None

        total_hrs = d.get("total_hours")
        if total_hrs is None:
            if st_epoch:
                total_hrs = round(max(0.0, (et_epoch or now_ts) - st_epoch) / 3600.0, 2)
            else:
                total_hrs = 0.0

        ot_hrs = d.get("overtime_hours")
        if ot_hrs is None:
            ot_hrs = max(0.0, round(float(total_hrs) - 9.0, 2)) if total_hrs else 0.0

        records.append({
            "shift_id": d["shift_id"],
            "operator_id": d["operator_id"],
            "id": d["Employee_id"] or "-",
            "code": d["employee_code"] or "-",
            "name": d["name"],
            "start_time": _fmt_clock_time(d.get("actual_start_time"), st_epoch),
            "end_time": "-" if is_active else _fmt_clock_time(d.get("actual_end_time"), et_epoch),
            "overtime": f"{float(ot_hrs):.2f} hrs" if float(ot_hrs) > 0 else "0.00 hrs",
            "overtime_num": float(ot_hrs),
            "date": d["shift_date"] or "-",
            "total_hours": f"{float(total_hrs):.2f} hrs",
            "total_hours_num": float(total_hrs),
            "presence": "Present",
            "status": "ACTIVE" if is_active else "COMPLETED",
            "attendance_status": d.get("attendance_status") or ("On Time" if not is_active else "Active"),
        })

    return {
        "report": records,
        "count": len(records),
        "total_work_hours": round(sum(r["total_hours_num"] for r in records), 2),
        "total_overtime_hours": round(sum(r["overtime_num"] for r in records), 2),
    }


@app.post("/api/production")
def record_production(req: RecordProductionRequest) -> Dict[str, Any]:
    """Records production progress for a wall within a shift."""
    shift = shift_store.get_shift(req.shift_id)
    if not shift:
        raise HTTPException(status_code=404, detail=f"Shift {req.shift_id} not found.")
    try:
        prod_id = shift_store.record_production(
            shift_id=req.shift_id,
            wall_number=req.wall_number,
            job_id=req.job_id,
            print_file=req.print_file,
            wall_height=req.wall_height,
            segment_layers=req.segment_layers,
            layers_completed=req.layers_completed,
            status=req.status,
            is_continuation_of=req.is_continuation_of,
            notes=req.notes,
        )
        return {"success": True, "production_id": prod_id}
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail=f"Unique constraint (shift_id, wall_number) violated: {exc}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/production/shift/{shift_id}")
def get_shift_production(shift_id: int) -> Dict[str, Any]:
    """Lists all production records for a shift."""
    records = shift_store.get_shift_production(shift_id)
    return {"shift_id": shift_id, "production_records": records, "count": len(records)}


@app.post("/api/issues")
def log_issue(req: LogIssueRequest) -> Dict[str, Any]:
    """Logs an issue against a shift."""
    shift = shift_store.get_shift(req.shift_id)
    if not shift:
        raise HTTPException(status_code=404, detail=f"Shift {req.shift_id} not found.")
    try:
        issue_id = shift_store.log_issue(
            shift_id=req.shift_id,
            issue_type=req.issue_type,
            description=req.description,
            wall_number=req.wall_number,
            cause=req.cause,
            action_taken=req.action_taken,
            result=req.result,
            severity=req.severity,
            downtime_minutes=req.downtime_minutes,
        )
        return {"success": True, "issue_id": issue_id}
    except (sqlite3.IntegrityError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/issues/shift/{shift_id}")
def get_shift_issues(shift_id: int) -> Dict[str, Any]:
    """Lists all issues reported for a shift."""
    issues = shift_store.list_shift_issues(shift_id=shift_id)
    return {"shift_id": shift_id, "issues": issues, "count": len(issues)}


@app.post("/api/handovers/initiate")
def initiate_handover(req: InitiateHandoverRequest) -> Dict[str, Any]:
    """Initiates an outgoing shift handover."""
    from_shift = shift_store.get_shift(req.from_shift_id)
    if not from_shift:
        raise HTTPException(status_code=404, detail=f"From-shift {req.from_shift_id} not found.")
    try:
        handover_id = shift_store.create_handover(
            from_shift_id=req.from_shift_id,
            line_id=req.line_id,
            completed_work=req.completed_work,
            pending_work=req.pending_work,
            open_issues=req.open_issues,
            notes=req.notes,
        )
        return {"success": True, "handover_id": handover_id}
    except (sqlite3.IntegrityError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/handovers/{handover_id}/complete")
def complete_handover(handover_id: int, req: CompleteHandoverRequest) -> Dict[str, Any]:
    """Completes an initiated handover by linking to the incoming shift."""
    h = shift_store.get_handover(handover_id)
    if not h:
        raise HTTPException(status_code=404, detail=f"Handover {handover_id} not found.")
    to_shift = shift_store.get_shift(req.to_shift_id)
    if not to_shift:
        raise HTTPException(status_code=404, detail=f"To-shift {req.to_shift_id} not found.")
    try:
        ok = shift_store.complete_handover(
            handover_id=handover_id,
            to_shift_id=req.to_shift_id,
            notes=req.notes,
            close_from_shift=req.close_from_shift,
        )
        return {"success": ok, "handover_id": handover_id, "to_shift_id": req.to_shift_id}
    except (sqlite3.IntegrityError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# =======================================================================
# Incident Images API
# =======================================================================

@app.get("/api/incidents/images/{image_id}")
def get_incident_image_file(image_id: str, download: bool = False):
    """
    Serves stored incident images (primary original uploads, YOLO defect
    annotations, and technical reference component drawings).
    """
    from agent import incident_store
    data_tuple = incident_store.get_image_data(image_id)
    if not data_tuple:
        raise HTTPException(status_code=404, detail="Incident image not found")
    img_bytes, mime, original_filename = data_tuple
    headers = {"Cache-Control": "public, max-age=86400"}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{original_filename}"'
    return Response(content=img_bytes, media_type=mime, headers=headers)


# =======================================================================
# Attendance API (v1 endpoints mirroring frontend ui/src/api.ts)
# =======================================================================

def _format_duration(seconds: float) -> str:
    secs = int(max(seconds, 0))
    hours = secs // 3600
    minutes = (secs % 3600) // 60
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _row_to_attendance_dict(row: sqlite3.Row, now: float) -> Dict[str, Any]:
    d = dict(row)
    start_ts = d["shift_start"]
    end_ts = d["shift_end"]
    status = d["status"]

    if status == "ACTIVE":
        worked_sec = max(now - start_ts, 0.0)
    else:
        worked_sec = max((end_ts or now) - start_ts, 0.0)

    # Overtime threshold: standard 8 hours (28800 seconds)
    standard_shift_sec = 8.0 * 3600.0
    overtime_sec = max(worked_sec - standard_shift_sec, 0.0)

    d["worked_seconds"] = int(worked_sec)
    d["worked_hours"] = f"{worked_sec / 3600.0:.2f}"
    d["worked_display"] = _format_duration(worked_sec)
    d["overtime_seconds"] = int(overtime_sec)
    d["overtime_display"] = _format_duration(overtime_sec) if overtime_sec > 0 else "0m"
    d["overtime_spoken"] = (
        f"{int(overtime_sec // 3600)} hours and {int((overtime_sec % 3600) // 60)} minutes"
        if overtime_sec > 0
        else "no overtime"
    )
    d["server_now"] = now
    return d


class CheckInRequest(BaseModel):
    employee_id: Optional[str] = None
    employee_name: Optional[str] = None


class CheckOutRequest(BaseModel):
    employee_id: Optional[str] = None
    employee_name: Optional[str] = None


@app.get("/api/v1/attendance")
def get_attendance(date: Optional[str] = None) -> List[Dict[str, Any]]:
    """Returns today's (or requested date's) employee attendance roster."""
    target_date = date or datetime.date.today().isoformat()
    now = time.time()
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM attendance_records
            WHERE attendance_date = ?
            ORDER BY shift_start DESC
            """,
            (target_date,),
        ).fetchall()
    return [_row_to_attendance_dict(r, now) for r in rows]


@app.post("/api/v1/attendance/check-in")
def check_in_attendance(req: CheckInRequest) -> Dict[str, Any]:
    """Checks an employee in for a shift."""
    identifier = (req.employee_id or req.employee_name or "").strip()
    if not identifier:
        raise HTTPException(status_code=400, detail="employee_id or employee_name required")

    now = time.time()
    today_date = datetime.date.today().isoformat()

    # Look up operator in operators table
    with db.connect() as conn:
        op = conn.execute(
            """
            SELECT * FROM operators
            WHERE operator_id = ? OR eid = ? OR employee_code = ? OR name = ? COLLATE NOCASE
            LIMIT 1
            """,
            (identifier, identifier, identifier, identifier),
        ).fetchone()

        if op:
            op_dict = dict(op)
            operator_id = str(op_dict.get("operator_id") or identifier)
            eid = str(op_dict.get("eid") or op_dict.get("employee_code") or operator_id)
            employee_name = str(op_dict.get("name") or identifier)
        else:
            operator_id = identifier
            eid = identifier
            employee_name = req.employee_name or identifier

        # Check if already active today
        active = conn.execute(
            """
            SELECT * FROM attendance_records
            WHERE (operator_id = ? OR eid = ? OR employee_name = ? COLLATE NOCASE)
              AND attendance_date = ? AND status = 'ACTIVE'
            LIMIT 1
            """,
            (operator_id, eid, employee_name, today_date),
        ).fetchone()

        if active:
            return _row_to_attendance_dict(active, now)

        cur = conn.execute(
            """
            INSERT INTO attendance_records (
                operator_id, eid, employee_name, attendance_date,
                shift_start, shift_end, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, NULL, 'ACTIVE', ?, ?)
            """,
            (operator_id, eid, employee_name, today_date, now, now, now),
        )
        rec_id = cur.lastrowid
        row = conn.execute("SELECT * FROM attendance_records WHERE id = ?", (rec_id,)).fetchone()
        return _row_to_attendance_dict(row, now)


@app.post("/api/v1/attendance/check-out")
def check_out_attendance(req: CheckOutRequest) -> Dict[str, Any]:
    """Checks an employee out of their active shift."""
    identifier = (req.employee_id or req.employee_name or "").strip()
    if not identifier:
        raise HTTPException(status_code=400, detail="employee_id or employee_name required")

    now = time.time()
    today_date = datetime.date.today().isoformat()

    with db.connect() as conn:
        # Find active attendance record
        row = conn.execute(
            """
            SELECT * FROM attendance_records
            WHERE (operator_id = ? OR eid = ? OR employee_name = ? COLLATE NOCASE)
              AND status = 'ACTIVE'
            ORDER BY shift_start DESC
            LIMIT 1
            """,
            (identifier, identifier, identifier),
        ).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail=f"No active shift found for '{identifier}'")

        rec_id = row["id"]
        conn.execute(
            """
            UPDATE attendance_records
            SET status = 'COMPLETED', shift_end = ?, updated_at = ?
            WHERE id = ?
            """,
            (now, now, rec_id),
        )
        updated = conn.execute("SELECT * FROM attendance_records WHERE id = ?", (rec_id,)).fetchone()
        return _row_to_attendance_dict(updated, now)


# =======================================================================
# Wall Production Tracking API (v1 endpoints mirroring frontend ui/src/api.ts)
# =======================================================================

class WallProductionPostRequest(BaseModel):
    operator_id: str
    quantity: int
    job_id: Optional[str] = None
    shift_id: Optional[int] = None
    notes: Optional[str] = None
    request_id: Optional[str] = None
    recorded_by: Optional[str] = None


class WallProductionCorrectRequest(BaseModel):
    record_id: str
    corrected_quantity: int
    reason: Optional[str] = None
    corrected_by: Optional[str] = None


@app.get("/api/v1/production/walls")
def get_wall_production(
    operator_id: Optional[str] = None,
    scope: str = "today",
    job_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    shift_id: Optional[int] = None,
    include_records: bool = True,
) -> Dict[str, Any]:
    """
    Returns aggregated wall production metrics and record list
    scoped by 'today', 'this_week', 'all_time', or custom date ranges.
    """
    today_date = datetime.date.today()
    where_clauses = ["is_corrected = 0"]
    params: List[Any] = []

    if scope == "today":
        where_clauses.append("production_date = ?")
        params.append(today_date.isoformat())
    elif scope == "this_week":
        # Monday of current week
        week_start = today_date - datetime.timedelta(days=today_date.weekday())
        where_clauses.append("production_date >= ?")
        params.append(week_start.isoformat())
    elif scope == "date_range":
        if date_from:
            where_clauses.append("production_date >= ?")
            params.append(date_from)
        if date_to:
            where_clauses.append("production_date <= ?")
            params.append(date_to)

    if operator_id:
        where_clauses.append("(w.operator_id = ? OR o.name = ? COLLATE NOCASE OR o.employee_code = ?)")
        params.extend([operator_id, operator_id, operator_id])

    if job_id:
        where_clauses.append("w.job_id = ?")
        params.append(job_id)

    if shift_id is not None:
        where_clauses.append("w.shift_id = ?")
        params.append(shift_id)

    where_sql = " AND ".join(where_clauses)

    query_records = f"""
        SELECT
            w.record_id,
            w.operator_id,
            COALESCE(o.name, w.operator_id) AS operator_name,
            o.employee_code,
            w.quantity,
            w.production_date,
            w.production_time,
            w.job_id,
            w.shift_id,
            w.notes,
            w.is_corrected,
            w.created_at,
            w.created_at AS updated_at
        FROM wall_production_records w
        LEFT JOIN operators o ON w.operator_id = o.operator_id OR w.operator_id = o.employee_code
        WHERE {where_sql}
        ORDER BY w.created_at DESC
    """

    with db.connect() as conn:
        rows = [dict(r) for r in conn.execute(query_records, params).fetchall()]

    total_qty = sum(r["quantity"] for r in rows)
    record_count = len(rows)

    # Group by operator
    op_map: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        op_key = str(r["operator_id"])
        if op_key not in op_map:
            op_map[op_key] = {
                "operator_id": r["operator_id"],
                "operator_name": r["operator_name"],
                "employee_code": r["employee_code"],
                "total_quantity": 0,
                "record_count": 0,
            }
        op_map[op_key]["total_quantity"] += r["quantity"]
        op_map[op_key]["record_count"] += 1

    by_op = sorted(list(op_map.values()), key=lambda x: x["total_quantity"], reverse=True)

    result = {
        "success": True,
        "scope": scope,
        "total_quantity": total_qty,
        "record_count": record_count,
        "by_operator": by_op,
    }
    if include_records:
        result["records"] = rows
    return result


@app.post("/api/v1/production/walls")
def post_wall_production(req: WallProductionPostRequest) -> Dict[str, Any]:
    """Records wall production for an operator."""
    if req.quantity <= 0:
        raise HTTPException(status_code=400, detail="Quantity must be greater than zero.")

    op_id = req.operator_id.strip()
    with db.connect() as conn:
        op = conn.execute(
            """
            SELECT * FROM operators
            WHERE operator_id = ? OR eid = ? OR employee_code = ? OR name = ? COLLATE NOCASE
            LIMIT 1
            """,
            (op_id, op_id, op_id, op_id),
        ).fetchone()

        if op:
            canonical_op_id = str(op["operator_id"])
            op_name = str(op["name"])
            emp_code = str(op["employee_code"] or "")
        else:
            canonical_op_id = op_id
            op_name = op_id
            emp_code = ""

        now = time.time()
        today_date = datetime.date.today().isoformat()
        rec_id = f"WALL-{int(now * 1000) % 1000000:06d}"

        conn.execute(
            """
            INSERT INTO wall_production_records (
                record_id, operator_id, quantity, production_date,
                production_time, job_id, shift_id, notes, input_source,
                created_at, request_id, is_corrected
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'chat', ?, ?, 0)
            """,
            (
                rec_id,
                canonical_op_id,
                req.quantity,
                today_date,
                now,
                req.job_id,
                req.shift_id,
                req.notes,
                now,
                req.request_id,
            ),
        )

        record_obj = {
            "record_id": rec_id,
            "operator_id": canonical_op_id,
            "operator_name": op_name,
            "employee_code": emp_code,
            "quantity": req.quantity,
            "job_id": req.job_id,
            "shift_id": req.shift_id,
            "notes": req.notes,
            "is_corrected": 0,
            "created_at": now,
            "updated_at": now,
        }

    return {"success": True, "record": record_obj}


@app.post("/api/v1/production/walls/correct")
def correct_wall_production(req: WallProductionCorrectRequest) -> Dict[str, Any]:
    """Corrects a previous wall production entry."""
    if req.corrected_quantity <= 0:
        raise HTTPException(status_code=400, detail="Corrected quantity must be greater than zero.")

    now = time.time()
    with db.connect() as conn:
        orig = conn.execute(
            "SELECT * FROM wall_production_records WHERE record_id = ?",
            (req.record_id,),
        ).fetchone()

        if not orig:
            raise HTTPException(status_code=404, detail=f"Record '{req.record_id}' not found.")

        orig_dict = dict(orig)
        orig_qty = orig_dict["quantity"]

        # Mark original as corrected
        conn.execute(
            """
            UPDATE wall_production_records
            SET is_corrected = 1,
                correction_notes = ?,
                corrected_by = ?,
                corrected_at = ?
            WHERE record_id = ?
            """,
            (req.reason, req.corrected_by, now, req.record_id),
        )

        # Create replacement record
        new_rec_id = f"WALL-{int(now * 1000) % 1000000:06d}"
        conn.execute(
            """
            INSERT INTO wall_production_records (
                record_id, operator_id, quantity, production_date,
                production_time, job_id, shift_id, notes, input_source,
                created_at, is_corrected
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'correction', ?, 0)
            """,
            (
                new_rec_id,
                orig_dict["operator_id"],
                req.corrected_quantity,
                orig_dict["production_date"],
                now,
                orig_dict["job_id"],
                orig_dict["shift_id"],
                f"Correction for {req.record_id}: {req.reason or ''}".strip(),
                now,
            ),
        )

        op = conn.execute(
            "SELECT * FROM operators WHERE operator_id = ?",
            (orig_dict["operator_id"],),
        ).fetchone()
        op_name = op["name"] if op else orig_dict["operator_id"]
        emp_code = op["employee_code"] if op else ""

        replacement_obj = {
            "record_id": new_rec_id,
            "operator_id": orig_dict["operator_id"],
            "operator_name": op_name,
            "employee_code": emp_code,
            "quantity": req.corrected_quantity,
            "job_id": orig_dict["job_id"],
            "shift_id": orig_dict["shift_id"],
            "notes": f"Correction for {req.record_id}",
            "is_corrected": 0,
            "created_at": now,
            "updated_at": now,
        }

    return {
        "success": True,
        "status": "corrected",
        "original_record_id": req.record_id,
        "original_quantity": orig_qty,
        "replacement_record": replacement_obj,
    }
