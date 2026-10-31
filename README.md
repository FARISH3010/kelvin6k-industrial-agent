# Kelvin6K Unified Industrial AI Agent

An industrial-grade, multimodal AI agent system for 3D concrete printing monitoring, voice-activated operator assistance, shift & attendance lifecycle management, metrology defect tracking, and domain-specific Retrieval-Augmented Generation (RAG).

---

## Table of Contents
1. [System Overview & Architecture](#system-overview--architecture)
2. [Key Capabilities](#key-capabilities)
3. [Prerequisites & Dependencies](#prerequisites--dependencies)
4. [Environment Setup](#environment-setup)
5. [Running the Application](#running-the-application)
6. [Project Structure (Directory & File Map)](#project-structure-directory--file-map)
7. [API Endpoints Overview](#api-endpoints-overview)
8. [Testing & Quality Assurance](#testing--quality-assurance)

---

## System Overview & Architecture

Kelvin6K integrates three primary subsystems:
1. **Voice & Assistant Engine**: Real-time microphone listening via `faster-whisper`, natural language query routing, voice synthesis (`pyttsx3` / `elevenlabs`), and reasoning powered by Google Gemini (with Anthropic Claude fallback).
2. **Vision & Metrology Pipeline**: Real-time layer width measurement, extrusion monitoring, OpenCV ArUco marker tracking, and anomaly detection.
3. **Industrial Database & Knowledge RAG**: Persistent SQLite storage tracking operators, shifts, print jobs, and incidents, coupled with a hybrid BM25 + embedding document retrieval engine for technical manuals and troubleshooting procedures.

```
                  +----------------------------------------------+
                  |         Industrial Web Dashboard (React)      |
                  +-----------------------+----------------------+
                                          | HTTP / REST / SSE
                                          v
+---------------------------------------------------------------------------------+
|                               FastAPI Backend (api/)                            |
|    - Shift / Attendance Routes     - Incident & Image Server                    |
|    - Production Metrology Stream   - Live Camera Feeds                          |
+------------------------------------+--------------------------------------------+
                                     |
    +--------------------------------+--------------------------------+
    |                                |                                |
    v                                v                                v
+-----------------------+  +-----------------------+  +-------------------------+
|     Agent Core        |  |     Vision Pipeline   |  |   Knowledge Base & RAG  |
|  - assistant.py       |  |  - metrology.py       |  |  - knowledge_store.py   |
|  - query_router.py    |  |  - layer_detector.py  |  |  - rag_index/           |
|  - qa.py / nlu.py     |  |  - camera_service.py  |  |  - docx / pdf manuals   |
+-----------+-----------+  +-----------+-----------+  +------------+------------+
            |                          |                           |
            +--------------------------+---------------------------+
                                       |
                                       v
                     +-----------------------------------+
                     |       SQLite Database (db.py)     |
                     |   kelvin6k.db (WAL mode enabled)  |
                     +-----------------------------------+
```

---

## Key Capabilities

- **Operator & Shift Management**: Automatic calculation of regular/overtime hours, late arrival tracking, and active shift state.
- **Incident Store & Visual Evidence**: Tracks shop floor issues, resolution actions, and linked inspection images.
- **RAG Technical Support**: Instant retrieval of machine specifications, error codes, and maintenance playbooks.
- **Metrology & Layer Quality**: Vision measurements verifying bead geometry against tolerance thresholds.
- **Audio Voice Assistant**: Push-to-talk or continuous microphone listening for hands-free operator assistance.

---

## Prerequisites & Dependencies

### System Requirements
- **Python**: Version 3.10 to 3.12
- **Node.js**: Version 18+ and npm (for the React UI)
- **C/C++ Build Tools / Visual Studio Build Tools** (recommended on Windows for audio/OpenCV packages)
- **Microphone & Camera** (USB webcam or industrial IP RTSP camera)

### Python Libraries (`requirements.txt`)
| Package | Version | Purpose |
| :--- | :--- | :--- |
| `python-dotenv` | `>=1.0.0` | Loads environment configurations from `.env` |
| `google-genai` | `>=2.0.0, <3.0.0` | Primary multimodal conversational brain and reasoning |
| `anthropic` | `>=1.0.0, <2.0.0` | Secondary LLM brain option (Claude) |
| `rank-bm25` | `>=0.2.2` | Lexical search ranking for knowledge base queries |
| `numpy` | `>=2.0.0, <3.0.0` | Array operations, math, and vision frame buffers |
| `python-docx` | `>=1.1.0` | Document parser for operator manuals in `.docx` format |
| `pypdf` | `>=4.0.0` | Document parser for PDF knowledge bases |
| `faster-whisper` | `>=1.0.0` | Fast, local speech-to-text transcription |
| `sounddevice` | `>=0.4.6` | Audio recording from microphone |
| `soundfile` | `>=0.12.0` | Audio file read/write operations |
| `pyttsx3` | `>=2.90` | Offline, zero-latency text-to-speech engine |
| `elevenlabs` | `>=0.2.27` | High-fidelity cloud voice generation (optional) |
| `opencv-python` | `>=4.10, <5.0` | Computer vision, ArUco markers, video frames |
| `ultralytics` | `>=8.0.0` | YOLO-based object & person detection |
| `pygrabber` | `>=0.2` | DirectShow camera device discovery on Windows |
| `fastapi` | `>=0.110.0, <1.0.0` | Asynchronous REST and streaming API framework |
| `uvicorn[standard]`| `>=0.27.0, <1.0.0` | ASGI production web server |
| `python-multipart` | `>=0.0.9` | Multipart form/data upload support in FastAPI |
| `pytest` | `>=8.0.0` | Unit, integration, and architecture testing framework |

---

## Environment Setup

### 1. Clone the Repository
```bash
git clone <your-repo-url>
cd integrated_agent
```

### 2. Configure Python Virtual Environment
```bash
# Windows
python -m venv venv
venv\Scripts\activate

# Linux / macOS
python3 -m venv venv
source venv/bin/activate
```

### 3. Install Python Dependencies
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Configure Environment Variables
Copy `.env.example` to `.env` and fill in your API credentials:
```bash
copy .env.example .env
```
Key variables to verify:
- `GEMINI_API_KEY`: Required for multimodal AI reasoning.
- `ANTHROPIC_API_KEY`: Optional fallback brain.
- `ELEVENLABS_API_KEY`: Optional; pyttsx3 is used by default if left blank.
- `DATABASE_PATH`: Defaults to `kelvin6k.db`.

---

## Running the Application

### Option A: Using the Convenience Startup Script (Windows)
```cmd
start.cmd
```

### Option B: Running Manually

1. **Start the FastAPI Backend Server**:
```bash
python run_api.py
# Server runs on http://127.0.0.1:8000
# Swagger API documentation is available at http://127.0.0.1:8000/docs
```

2. **Start the Frontend Dashboard (React + Vite)**:
```bash
cd ui
npm install
npm run dev
# Dashboard runs on http://localhost:5173
```

3. **Start the Standalone Voice CLI**:
```bash
python assistant_cli.py
```

---

## Project Structure (Directory & File Map)

```
integrated_agent/
│
├── agent/                         # Core Autonomous Agent Logic
│   ├── agent_loop.py              # Main monitoring and control cycle
│   ├── anthropic_qa.py            # Claude LLM question-answering adapter
│   ├── assistant.py               # Central orchestrator combining NLU, RAG, and DB
│   ├── camera.py                  # Low-level camera capture abstraction
│   ├── config.py                  # Global application settings & env loader
│   ├── dashboard.py               # Terminal / CLI dashboard reporting
│   ├── db.py                      # SQLite database schema, connections, and migrations
│   ├── identity.py                # Agent persona, operational constraints, system prompts
│   ├── incident_store.py          # Incident ticket lifecycle and image references
│   ├── knowledge_store.py         # RAG loader, vector/BM25 indexing, doc retrieval
│   ├── layer_detector.py          # Vision: 3D concrete layer edge detection
│   ├── listener.py                # Microphone capture and VAD (Voice Activity Detection)
│   ├── logging_setup.py           # Standardized logger formatters
│   ├── metrics.py                 # Print job execution metrics
│   ├── metrology.py               # Geometric measurement and tolerance checking
│   ├── metrology_voice.py         # Voice announcements for print defects/tolerances
│   ├── nlu.py                     # Intent recognition and slot extraction
│   ├── people_detector.py         # YOLO safety zone and personnel detector
│   ├── print_monitor.py           # Real-time state machine for 3D printing
│   ├── qa.py                      # Gemini LLM question-answering engine
│   ├── quality.py                 # Layer quality score calculations
│   ├── query_router.py            # Directs operator queries to SQL, RAG, or Metrology
│   ├── rag/                       # RAG document indexing and retrieval modules
│   ├── rules.py                   # Hard safety and boundary rules
│   ├── segmentation.py            # Image segmentation routines
│   ├── sensors.py                 # Telemetry sensor interfaces
│   ├── services/                  # Business service wrappers
│   ├── shift_store.py             # Shift lifecycle, attendance, and overtime tracking
│   ├── spoken.py                  # Phonetic cleaning and speech formatters
│   ├── state.py                   # In-memory shared agent state
│   ├── troubleshooter.py          # Automated machine diagnostic decision tree
│   ├── vision.py                  # Computer vision utilities & frame processors
│   ├── vision_loop.py             # Dedicated vision worker thread
│   ├── voice.py                   # Text-to-speech player (pyttsx3 & ElevenLabs)
│   ├── voice_assistant.py         # Voice-activated loop (STT -> LLM -> TTS)
│   ├── voice_contract.py          # Strict data contracts for voice responses
│   └── wall_detector.py           # Wall segment completion verification
│
├── api/                           # FastAPI Web Backend
│   ├── camera_service.py          # MJPEG video streaming for metrology camera
│   ├── server.py                  # FastAPI REST endpoints and startup hooks
│   └── site_camera_service.py     # Live site security/overhead camera feed
│
├── ui/                            # React + TypeScript + Vite Dashboard
│   ├── src/
│   │   ├── api.ts                 # Axios / Fetch client connecting to backend
│   │   ├── App.tsx                # Main UI layout and tabs
│   │   ├── components/            # Production, shift, metrology & incident UI cards
│   │   ├── hooks/                 # React state and polling hooks
│   │   └── types.ts               # TypeScript data definitions
│   ├── package.json               # Frontend dependencies
│   └── vite.config.ts             # Vite build configuration
│
├── data/                          # Data Files
│   ├── incident_images/           # Visual defect snapshots captured by operators/cameras
│   └── reference_parts/           # CAD / nominal reference images for comparison
│
├── docs/                          # Technical Manuals & Source Documents
│   ├── kelvin6k_manual.md         # Operational equipment instructions
│   ├── kelvin6k_troubleshooting.md# Symptom and recovery procedures
│   └── knowledge/                 # Extracted knowledge chunks for RAG
│
├── kelvin6k_rag_operator_knowledge_package/ # Domain RAG reference files
├── kelvin6k_rag_updated_knowledge_package/  # Updated domain RAG files
├── rag_index/                     # Persisted pre-indexed knowledge search trees (`index.json`)
├── tests/                         # Automated Unit and Integration Test Suite
│   ├── test_agentic_brain.py
│   ├── test_assistant.py
│   ├── test_conversational_flow.py
│   ├── test_issue_resolution.py
│   └── test_shift_integration.py
│
├── .env.example                   # Template for environment configuration
├── .gitignore                     # Git ignore rules (protects secrets and venv)
├── assistant_cli.py               # Interactive CLI terminal for the agent
├── calibration_test.py            # Camera calibration calibration utility
├── camera_test.py                 # Webcam capture verification test
├── check_gemini_key.py            # Script to validate Google Gemini API connectivity
├── find_cameras.py                # Utility listing connected DirectShow/USB cameras
├── ingest_docs.py                 # Ingestion pipeline rebuilding `rag_index/`
├── kelvin6k.db                    # Core SQLite database with active system state
├── main.py                        # Entry point for the full unified background agent
├── make_marker.py                 # Generates ArUco markers for camera calibration
├── requirements.txt               # Pinned Python package dependencies
├── run_api.py                     # Launcher script for FastAPI
├── start.cmd                      # Double-click startup script for Windows
├── tts_test.py                    # Audio synthesizer test
└── voice_test.py                  # Microphone and speech recognition test
```

---

## API Endpoints Overview

The FastAPI backend exposes the following primary groups of endpoints:

- **System Status**: `GET /api/status` - Health check, active jobs, and camera connectivity.
- **Attendance & Shifts**:
  - `GET /api/v1/attendance` - Today's operator shift status, hours, and overtime.
  - `POST /api/v1/attendance/check-in` - Record operator clock-in.
  - `POST /api/v1/attendance/clock-out` - Record operator clock-out.
- **Production & Metrology**:
  - `GET /api/v1/production/walls` - Wall printing counts and completion status.
  - `GET /api/metrology/announcement` - Latest quality tolerance alerts.
- **Incidents & Troubleshooting**:
  - `GET /api/incidents` - List logged production issues.
  - `GET /api/incidents/images/{image_id}` - Fetch linked visual evidence.
  - `POST /api/incidents` - File a new incident ticket.
- **Video Feeds**:
  - `GET /api/camera/stream` - Live MJPEG video stream from the printhead camera.
  - `GET /api/site-camera/stream` - Live MJPEG site camera feed.
- **Interactive Assistant**:
  - `POST /api/assistant/chat` - Send text/voice queries to the Kelvin6K agent.

---

## Testing & Quality Assurance

Run the test suite using `pytest`:

```bash
# Run all tests
pytest tests/ -v

# Run specific functional test
pytest tests/test_assistant.py -v
```

---

## Contributing & Pushing to GitHub

Before committing:
1. Ensure your `.env` file is **not** staged:
   ```bash
   git status
   ```
2. Verify `.gitignore` excludes `venv/`, `__pycache__/`, and `.env`.
3. Push changes:
   ```bash
   git add .
   git commit -m "Initialize Kelvin6K repository"
   git push origin main
   ```
