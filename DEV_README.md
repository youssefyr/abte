# ABTE — Developer Documentation

> Last updated: May 2026
> Maintainer notes for anyone onboarding or trying to figure out what this thing does.

---

## What is ABTE?

ABTE is a desktop productivity app built with **PySide6** (Qt for Python). It tracks your focus during work sessions using computer vision (gaze tracking via MediaPipe), predicts when you're about to lose focus using a LightGBM classifier, and helps you plan your day with an AI-powered task decomposition system backed by a local SLM (small language model).

It's not a web app. It runs locally, your data stays on your machine, and the ML models run on your hardware. No cloud calls, no telemetry.

The app also ships with browser extensions (Firefox + Chrome) that communicate with the desktop app over native messaging. The extension can block distracting tabs during focus sessions, using fuzzy NLP matching to figure out if what you're looking at is related to your current task.

---

## Table of Contents

1. [High-Level Architecture](#high-level-architecture)
2. [Project Structure](#project-structure)
3. [Core Services (the important stuff)](#core-services)
    - [FocusTickEngine](#focustickengine)
    - [FocusSessionService](#focussessionservice)
    - [GazeService + Vision Pipeline](#gazeservice--vision-pipeline)
    - [PlannerService (Energy Pattern + OR-Tools)](#plannerservice)
    - [SlmService (Local LLM)](#slmservice)
    - [TaskService](#taskservice)
    - [ExtensionCoreHandler (Browser Bridge)](#extensioncorehandler)
    - [TabFocusGuard (NLP Matching)](#tabfocusguard)
    - [NotificationService](#notificationservice)
    - [ActiveWindowService + WindowTracker](#activewindowservice--windowtracker)
    - [FactService](#factservice)
    - [SidebarTemplateService (Custom Dynamic Sidebar Text)](#sidebartemplateservice)
4. [AI / ML / CV / NLP Features](#ai--ml--cv--nlp-features)
5. [Data Layer](#data-layer)
6. [UI Architecture](#ui-architecture)
7. [Plugin System](#plugin-system)
8. [Browser Extension](#browser-extension)
9. [Configuration & Settings](#configuration--settings)
10. [Building with PyInstaller](#building-with-pyinstaller)
11. [Development Setup](#development-setup)
12. [Known Issues & Gotchas](#known-issues--gotchas)

---

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────┐
│                      main.py                            │
│  (arg parsing, logging setup, QApplication lifecycle)   │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────┐
│                   bootstrap.py                          │
│  (wires all services together, connects signals/slots)  │
└──────────┬─────────────┬────────────┬───────────────────┘
           │             │            │
     ┌─────▼─────┐ ┌────▼────┐ ┌────▼──────┐
     │ MainWindow│ │ Services│ │ Data Layer│
     │  (UI)     │ │         │ │           │
     └───────────┘ └─────────┘ └───────────┘
```

**bootstrap.py** is the central orchestration point. It creates every service, hooks up Qt signals between them, and passes everything into `MainWindow`. If you're trying to understand how things connect, start there.

The reason it exists as a separate file (instead of just doing it in `main.py`) is to avoid circular imports. Services reference each other, and having a single wiring point keeps things sane.

### Signal Flow (the hot path)

During a focus session, here's roughly what happens every 500ms:

1. `FocusTickEngine` fires its QTimer
2. It grabs the active OS window from `ActiveWindowService`
3. It grabs the latest gaze result from `GazeService`
4. It builds a feature vector and runs LightGBM inference
5. It emits `focus_updated` signal with the current drift probability
6. `FocusSessionService` picks that up and updates session metrics
7. If drift is high for long enough, `NotificationService` fires a nudge
8. The dashboard UI updates the focus score display

---

## Project Structure

```
abte/
├── main.py                          # Entry point
├── requirements.txt                 # pip deps
├── DEV_README.md                    # You are here
├── README.md                        # Short public readme
│
├── app/
│   ├── bootstrap.py                 # Service wiring & signal connections
│   │
│   ├── core/
│   │   ├── logging_config.py        # Rotating file + console logging
│   │   ├── settings.py              # SettingsManager (wraps QSettings)
│   │   ├── plugin_api.py            # PluginManager + PluginStorageAPI protocol
│   │   ├── window_tracker.py        # OS-level active window detection
│   │   ├── integration_hooks.py     # Dev bootstrapping (fake data, weekly review)
│   │   └── vision/
│   │       ├── gaze_worker.py       # QThread running the full gaze pipeline
│   │       ├── face_landmarker_wrapper.py  # MediaPipe face landmarker
│   │       ├── feature_extractor.py # Iris position, head pose, blink detection
│   │       ├── frame_enhancer.py    # Pre-processing (contrast, blur detection)
│   │       ├── gaze_mapper.py       # Polynomial regression screen mapping
│   │       └── gaze_zone_classifier.py    # Zone classification (on/off screen, etc)
│   │
│   ├── data/
│   │   ├── entities.py              # Dataclasses: TaskItem, SessionLogItem, etc.
│   │   ├── repository.py            # SqliteRepository (all DB operations)
│   │   ├── settings_store.py        # QSettings wrapper for app preferences
│   │   └── fact_store.py            # Nudge/motivation text storage
│   │
│   ├── services/
│   │   ├── focus_tick_engine.py      # The brain: 500ms tick, feature aggregation, ML inference
│   │   ├── focus_session_service.py  # Session lifecycle (start/pause/resume/stop)
│   │   ├── focus_smoother.py         # Modernized focus drift bucket accumulator & EWMA smoother
│   │   ├── gaze_service.py          # High-level gaze management (start/stop/calibrate)
│   │   ├── active_window_service.py  # Wraps WindowTracker for the rest of the app
│   │   ├── handle_tasks.py          # TaskService: CRUD, quick-add parsing, NL task creation
│   │   ├── notification_service.py  # Publish/suppress/flush notification queue
│   │   ├── fact_service.py          # Random nudge/motivation text
│   │   ├── fake_data_service.py     # Generates realistic test data
│   │   ├── extension_core.py        # Native messaging bridge to browser extensions
│   │   ├── tab_focus_guard.py       # NLP fuzzy matching for tab blocking
│   │   ├── planner_service.py       # Energy-aware scheduling (KMeans + OR-Tools)
│   │   ├── sidebar_template_service.py # Custom sidebar template renderer
│   │   └── slm/
│   │       ├── slm_service.py       # Local LLM orchestration (llama.cpp / ONNX)
│   │       ├── hardware_planner.py  # CPU/GPU sampling, execution plan selection
│   │       ├── model_catalog.py     # GGUF model registry and automated down-discovery
│   │       ├── slm_async.py         # QThread workers & SlmWorkerPool for background LLM calls
│   │       ├── benchmark_store.py   # Benchmark history for inference decisions
│   │       ├── models.py            # Dataclasses for SLM config, plans, stats
│   │       └── parser_utils.py      # JSON extraction from LLM output
│   │
│   ├── ui/
│   │   ├── main_window.py           # MainWindow: sidebar, topbar, page stack, overlays
│   │   ├── theme.py                 # ThemeManager + 3 theme specs (Forest, Mono, Paper)
│   │   ├── metrics.py               # Responsive UiMetrics (font sizes, spacing, radii)
│   │   ├── navigation.py            # SidebarMenu widget
│   │   ├── nav_config.py            # Page ordering and header metadata
│   │   ├── calendar_widget.py       # Week view calendar
│   │   ├── startup_wizard_dialog.py # First-run setup wizard (integrates model selector)
│   │   ├── slm_model_selector.py    # UI widget for selecting & downloading GGUF models
│   │   ├── icon_manager.py          # qtawesome icon wrapper
│   │   ├── ui_helpers.py            # Factory functions (make_card, make_button, etc.)
│   │   ├── plugins_manager.py       # Plugin list UI
│   │   ├── pages/
│   │   │   ├── dashboard.py         # Main dashboard with focus score, session controls
│   │   │   ├── planner_page.py      # AI planner UI
│   │   │   ├── task_editor_page.py  # Task list + editor
│   │   │   ├── coach_page.py        # Weekly review + NL task creation
│   │   │   ├── notifications_page.py
│   │   │   ├── settings_page.py     # All app settings (vision, SLM, dev tools)
│   │   │   └── account_page.py      # Profile, avatar, stats
│   │   ├── widgets/
│   │   │   └── avatar_crop_dialog.py
│   │   └── calibration/
│   │       └── gaze_calibration_wizard.py
│   │
│   └── tests/                       # pytest suite
│       ├── test_abte.py             # Main integration & service tests
│       └── test_sidebar_template.py # Sidebar template engine unit tests
│
└── extension/
    ├── firefox/
    │   ├── manifest.json
    │   ├── background.js
    │   ├── popup.html
    │   └── popup.js
    └── chrome/
        ├── manifest.json
        ├── background.js
        ├── popup.html
        └── popup.js
```

---

## Core Services

### FocusTickEngine

**File:** `app/services/focus_tick_engine.py`

This is the heart of the app. It runs on a 500ms `QTimer` and does the following each tick:

1. Polls the active OS window (title + process name)
2. Reads the latest gaze result (zone, confidence, blink rate, head pose)
3. Builds a feature vector combining window context + gaze data + session duration
4. Runs inference through a **LightGBM** model to predict `p_drift` (probability of attention drift)
5. Applies exponential smoothing to avoid noisy predictions
6. Emits `focus_updated(float)` signal with the smoothed score
7. Persists `FocusTickItem` records to the database every N ticks

**Smart notification suppression:** When the user is in a flow state (low drift for sustained period), the engine tells `NotificationService` to suppress non-error notifications. They queue up and flush when the flow state ends. This prevents "you're doing great!" notifications from ironically breaking your focus.

**Task nudging:** If drift stays high for a configurable threshold, the engine pulls a motivational nudge from `FactService` and publishes it through `NotificationService`. The language is intentionally supportive and non-judgmental. We don't say "you're distracted," we say something like "take a breath, ready to jump back in?"

**Distraction classification (SLM-powered):** When the SLM service is available, the engine can send window titles to `SlmService.categorize_distractions()` which classifies them as "productive" or "distracting" using the local language model. This feeds back into the drift prediction as an additional feature.

### FocusSessionService

**File:** `app/services/focus_session_service.py`

Manages the session lifecycle: start, pause, resume, stop. Uses an in-memory cache for the current session status to avoid hitting SQLite on the hot path (the focus engine polls at 10Hz for signal updates, and we don't want DB reads on every tick).

**Gaze-driven auto-pause:** When `GazeService` reports the user's face has been absent for a configurable duration, the session automatically pauses. When the face comes back, it resumes. This means you can walk away from your desk and the timer handles itself.

Key signals:
- `session_started(str)` — session ID
- `session_paused(str)`
- `session_resumed(str)`
- `session_stopped(str, dict)` — session ID + final metrics

### FocusSmoother

**File:** `app/services/focus_smoother.py`

Handles the accumulation, smoothing, and aggregation of raw attention-drift predictions over time. Rather than relying on instantaneous noisy outputs from the LightGBM classifier, `FocusSmoother` groups ticks into minute-by-minute buckets to maintain clean history and smooths recent ticks using Exponentially Weighted Moving Averages (EWMA).

Key components:
- **`MinuteBucket`**: Dataclass representing a closed 1-minute interval of ticks. Stores the average `p_drift`, tick count, linked `session_id`, and metadata.
- **`LiveFocusSnapshot`**: The data payload emitted on every tick containing raw scores, EWMA smoothed scores (scaled 0-100), active window titles, process names, gaze presence, and tick index.
- **Active Session Tracking**: Integrates with the current session via `start_session(session_id)` and `end_session()`. Calculates clean session averages by weighting closed bucket averages.
- **Exponential Smoothing (`_update_ema`)**: Applies EWMA (configurable alpha, defaults to `0.15`) over the sliding window history. Automatically falls back to raw averages if EWMA is disabled or uninitialized.

### GazeService + Vision Pipeline

**File:** `app/services/gaze_service.py` + `app/core/vision/*`

This is the computer vision subsystem. It's built as a pipeline:

```
Camera Frame → FrameEnhancer → FaceLandmarkerWrapper → FeatureExtractor → GazeMapper → GazeZoneClassifier → GazeResult
```

**Components:**

| Component | File | What it does |
|-----------|------|-------------|
| `FrameEnhancer` | `frame_enhancer.py` | Contrast normalization, blur/low-light detection |
| `FaceLandmarkerWrapper` | `face_landmarker_wrapper.py` | MediaPipe Face Landmarker (468 landmarks) |
| `FeatureExtractor` | `feature_extractor.py` | Extracts iris position, head pose (yaw/pitch), blink rate, yawn detection, eye openness |
| `GazeMapper` | `gaze_mapper.py` | Polynomial regression mapping from iris coordinates to screen coordinates. Per-screen calibration. |
| `GazeZoneClassifier` | `gaze_zone_classifier.py` | Classifies gaze into zones: `ON_SCREEN`, `OFF_SCREEN`, `LOOKING_AWAY`, `ABSENT`, `DEGRADED`, `NOT_CALIBRATED` |
| `GazeWorker` | `gaze_worker.py` | QThread running the full pipeline at ~10 FPS |

**GazeResult dataclass fields:**
- `zone`: enum (ON_SCREEN, OFF_SCREEN, LOOKING_AWAY, ABSENT, DEGRADED, NOT_CALIBRATED)
- `gaze_x_norm`, `gaze_y_norm`: normalized screen coordinates
- `gaze_x_px`, `gaze_y_px`: pixel coordinates
- `confidence`: float
- `face_detected`: bool
- `blink_rate_per_min`, `eye_open_avg`, `is_blinking`
- `yaw_deg`, `pitch_deg`
- `is_low_light`, `is_blurry`

**Calibration:** The `GazeMapper` uses polynomial regression and requires per-screen calibration. The calibration wizard (`gaze_calibration_wizard.py`) collects calibration points and fits the model. Calibration data is stored via `SettingsManager`.

**Thread safety:** The `GazeWorker` runs in a `QThread`. All MediaPipe objects are created inside `run()` to keep them thread-local. Shutdown uses `_stop_async()` with `QTimer.singleShot` to avoid blocking the main thread while the worker cleans up.

**Camera backend selection:**
- Linux: `CAP_V4L2`
- macOS: `CAP_AVFOUNDATION`
- Windows: `CAP_DSHOW`

### PlannerService

**File:** `app/services/planner_service.py`

Uses two ML/optimization approaches:

1. **EnergyPatternModel (KMeans):** Clusters historical focus sessions by time-of-day and focus score to identify the user's natural energy patterns. Outputs peak focus hours.

2. **Constraint-based scheduling (Google OR-Tools):** Given a list of tasks with estimated durations, energy costs, and deadlines, it solves a scheduling problem that:
   - Matches high-energy tasks with peak focus hours
   - Respects calendar blocks
   - Avoids scheduling during historically low-focus periods
   - Produces a suggested daily plan

This runs entirely offline. No API calls.

### SlmService (Local LLM)

**File:** `app/services/slm/slm_service.py`

Orchestrates a local small language model for several AI features. Supports two backends:

| Backend | Runtime | Notes |
|---------|---------|-------|
| `llama_cpp` | llama.cpp CLI (`llama-cli`, `main`, or `llama` binary) | Preferred. Supports CPU, GPU, and hybrid execution. |
| `onnx_runtime` | ONNX Runtime | Fallback. CPU or GPU via execution providers. |

#### 1. GGUF Model Catalog & Auto-Discovery
**File:** `app/services/slm/model_catalog.py`

ABTE features a modular model catalog supporting different hardware tiers:
- **`LFM-2.5 1.2B Thinking` (Lightweight)**: ~850 MB download size, requires ≥1.1 GB RAM. Highly recommended for low-resource or older CPU systems.
- **`Phi-3 Mini 4K` (Standard)**: ~2.2 GB download size, requires ≥2.8 GB RAM. Strong reasoning capabilities, optimized for modern CPUs or GPUs.

**Automated Discovery Caching**: The model catalog implements a robust path scanner (`find_downloaded_model`) that searches for existing downloaded models across multiple directories to avoid redundant downloads. It checks:
1. Application model folder (`~/.gemini/antigravity/models` or similar `app_data_dir/models`).
2. Global system cache locations: Hugging Face hubs (`~/.cache/huggingface/hub`), LM Studio cache (`~/.cache/lm-studio/models` or `~/.lmstudio/models`), standard `Downloads` folder, local share directories, and OS AppData structures.
3. Hermetic unit tests automatically bypass global system checks to ensure clean, isolated testing boundaries.

#### 2. Hardware Planner & Resource Budgets
**File:** `app/services/slm/hardware_planner.py`

Before each inference call, the planner samples active system resources:
- **Strict Resource Budgeting**: A 25-50% CPU utilization budget is checked and respected to ensure the host desktop application remains responsive and benchmark timeouts are avoided.
- **Dynamic Thread Tuning & Layer Offloading**: Based on real-time CPU/GPU memory headroom and historical benchmarks stored in `BenchmarkStore`, the planner selects the target execution plan (CPU, GPU, or Hybrid) and dynamically offloads an optimal number of layers (reducing GPU offload under VRAM pressure to prevent OOM errors).

#### 3. Asynchronous Execution Pipeline
**File:** `app/services/slm/slm_async.py`

To prevent slow subprocess/CLI-based LLM execution (which takes 5-30+ seconds) from freezing the PySide6 Qt GUI main thread, all inference operations are wrapped in background workers:
- **`DecomposeTaskWorker`**: Asynchronously breaks goals into structured subtask lists.
- **`GenerateWeeklyReviewWorker`**: Asynchronously compiles weekly reports from database logs.
- **`CategorizeDistractionsWorker`**: Asynchronously classifies active OS window titles.
- **`SlmWorkerPool`**: Central pool coordinator that enforces single-concurrency execution of background workers. In-flight requests are automatically cancelled (`cancel()`) when a new request arrives, discarding stale results and saving CPU cycles.

#### 4. Model Selection & Integration UI
**File:** `app/ui/slm_model_selector.py` & `app/ui/startup_wizard_dialog.py`

The modern **`SlmModelSelector`** UI widget provides an interactive interface displaying available models, their resource footprints, compatibility scores (computed dynamically based on system specifications), download progress, and local path status. This is integrated into:
- The **Settings Page** under the SLM Configuration panel.
- The **Startup Setup Wizard**, enabling users to choose a suitable model tier, download it, or auto-detect an existing local cache before using NLP and AI features.

**AI features powered by the SLM:**

1. **Task decomposition** (`decompose_task`): Takes a high-level task ("Finish OS assignment 2") and breaks it into 3-8 concrete subtasks with estimated durations, energy costs, and tags. If the user's current focus score is low, it adapts by suggesting smaller 5-minute chunks.

2. **Natural language task creation** (`extract_tasks_from_text`): Paste free-form text and it extracts actionable tasks from it.

3. **Weekly coach review** (`generate_weekly_review`): Generates a supportive weekly summary based on session stats, completed tasks, and focus patterns. Uses non-diagnostic language. Stored as `coach_reports` in the database.

4. **Distraction classification** (`categorize_distractions`): Classifies window titles as "productive" or "distracting".

5. **Predictive scheduling** (`predictive_schedule`): Given a task backlog and user stats, suggests which 3 tasks to tackle next based on energy patterns.

6. **Goal decomposition** (`decompose_goal`): Breaks a high-level goal into `DecomposedTask` items with auto-persist.

**Fallback behavior:** Every SLM feature has a deterministic fallback. If the model isn't loaded, the file doesn't exist, or inference fails, it falls back to heuristic-based decomposition (`parser_utils.py`). The app never crashes because the LLM is unavailable.

**Prompt design:** All prompts explicitly state: "You are not a therapist, clinician, or medical advisor." This is a safety guardrail. The system prompt focuses on productivity support only.

**Benchmarking:** `benchmark_runtime()` tests all three targets (cpu/gpu/hybrid), records latency, and stores results in `BenchmarkStore`. The planner uses these benchmarks to make better decisions next time.

### TaskService

**File:** `app/services/handle_tasks.py`

Full CRUD for tasks with some smart features:

- **Quick-add parsing:** Supports inline syntax like `"Finish report #work !!! 90min tomorrow at 2pm"`. Extracts tags, priority, estimated duration, and due date from natural text.
- **Auto-decomposition:** When a task looks like a high-level goal (3+ words, contains keywords like "finish", "build", "prepare"), it automatically triggers SLM decomposition if available.
- **NL task creation:** Routes through `SlmService.extract_tasks_from_text()` to create tasks from pasted text.
- **Subtask management:** Parent-child task relationships with `parent_task_id`.

### ExtensionCoreHandler

**File:** `app/services/extension_core.py`

Manages the native messaging bridge between the desktop app and browser extensions. Communication happens through shared JSON files (not stdin/stdout pipes), which is more reliable for long-running sessions.

**How it works:**
1. The desktop app writes state to a shared JSON file (current task, keywords, blocking rules)
2. The browser extension reads that file periodically
3. The extension sends events back (tab changes, blocked tab reports) via native messaging
4. Bridge scripts are dynamically generated and verified by content hash + version stamp to prevent stale installations

**Native messaging manifest registration:**
- Firefox: `~/.mozilla/native-messaging-hosts/`
- Chrome/Chromium: `~/.config/google-chrome/NativeMessagingHosts/` (or equivalent)

### TabFocusGuard

**File:** `app/services/tab_focus_guard.py`

NLP-based tab relevance checking. Uses `rapidfuzz` (fuzzy string matching) to compare the current browser tab's title/URL against the active task's title and keywords.

**Algorithm:**
1. Extract tokens from the task title and keywords
2. Extract tokens from the tab title and URL
3. Compute fuzzy match ratio using `rapidfuzz.fuzz.token_sort_ratio`
4. If the relevance score is below a threshold for a sustained duration, trigger a blocking command through the extension bridge
5. The extension then overlays a "get back to work" message on the tab

This is intentionally soft-blocking. The user can always dismiss it. We're not trying to be a parental control app; it's just a nudge.

### NotificationService

**File:** `app/services/notification_service.py`

Publish-subscribe notification system with suppression support.

- **Suppression:** During flow states, non-error notifications are queued. When suppression ends, they flush.
- **Callbacks:** UI components register callbacks to show toast notifications.
- **Persistence:** All notifications are saved to SQLite with read/unread tracking.
- **Levels:** `info`, `warning`, `error`

### ActiveWindowService + WindowTracker

**File:** `app/services/active_window_service.py` + `app/core/window_tracker.py`

Cross-platform active window detection. Returns the window title, process name, and whether it's a browser.

**Platform support:**

| Platform | Method | Fallback |
|----------|--------|----------|
| Linux (X11) | `xprop` | `xdotool` |
| Linux (Wayland/KDE) | `qdbus` KWin | — |
| Linux (Wayland/wlroots) | `lswt -j` | — |
| Linux (Wayland/GNOME) | Not supported (no API) | Returns "unknown" |
| Windows | `ctypes` (user32/kernel32) | — |
| macOS | Stub (returns placeholder) | — |

**Browser detection:** Maintains a set of ~20 known browser process names with alias normalization (e.g., `google-chrome-stable` → `chrome`, `zen-browser` → `zen`, `org.mozilla.firefox` → `firefox`).

**Caching:** Results are cached for 400ms to avoid spamming subprocess calls on the hot path.

### FactService

**File:** `app/services/fact_service.py`

Simple service that pulls random motivational nudges and facts from a local store (`FactStore`). Used by `FocusTickEngine` when the user's drift is high.

Categories: `nudge` (short refocus prompts) and `motivation` (encouraging messages). Always has a fallback default so it never returns empty.

### SidebarTemplateService

**File:** `app/services/sidebar_template_service.py`

Enables users to customize the desktop application's sidebar text dynamically by resolving active data-driven tokens at runtime. It decouples UI presentation from backend service implementation.

The template service compiles and substitutes 12 dynamic placeholders:
1. `{{plugin_number}}`: Counts active third-party plugins (excluding `core.demo`).
2. `{{username}}`: Preferred user display name (fallback: `abte user`).
3. `{{task_count}}`: Total count of tasks currently stored in the repository.
4. `{{todo_count}}`: Pending tasks (`todo` or `in_progress`).
5. `{{done_count}}`: Completed tasks.
6. `{{focus_session_count}}`: Completed focus sessions.
7. `{{total_focus_minutes}}`: Aggregated productive minutes across all sessions.
8. `{{current_date}}`: Today's system date (`YYYY-MM-DD`).
9. `{{current_time}}`: Current system time (`HH:MM`).
10. `{{unread_notifications}}`: Counts unread internal notifications.
11. `{{gaze_status}}`: Live eye-gaze tracking status (`active`/`inactive`).
12. `{{theme_name}}`: Name of the active visual stylesheet theme.

It provides a `get_placeholders_metadata()` method to list descriptions of all supported variables, making it self-documenting for settings pages.

---

## AI / ML / CV / NLP Features Summary

Here's every AI-adjacent feature in one table:

| Feature | Tech Stack | Runs Where | Data Requirements | Fallback |
|---------|-----------|-----------|-------------------|----------|
| **Focus drift prediction** | LightGBM classifier | In-process | Window titles, gaze data, session duration | Fixed threshold |
| **Gaze tracking** | MediaPipe Face Landmarker + OpenCV | QThread (10 FPS) | Camera feed, calibration points | Disabled (sessions still work without it) |
| **Gaze-to-screen mapping** | Polynomial regression | In-process | Per-screen calibration data | Zone classifier falls back to head pose only |
| **Energy pattern modeling** | KMeans clustering (scikit-learn) | In-process | Historical session logs | Uniform energy assumption |
| **Task scheduling** | Google OR-Tools (CP-SAT solver) | In-process | Task list + energy patterns + calendar | Simple priority sort |
| **Task decomposition** | Local SLM (llama.cpp / ONNX) | Subprocess | Task title + description | Heuristic splitting by duration |
| **NL task extraction** | Local SLM | Subprocess | Free-form text | Regex-based extraction |
| **Weekly coach review** | Local SLM | Subprocess | Session stats, task completions | Empty (feature disabled) |
| **Distraction classification** | Local SLM | Subprocess | Window titles | Empty dict (no classification) |
| **Predictive scheduling** | Local SLM | Subprocess | Task backlog + energy stats | Empty list |
| **Tab relevance scoring** | RapidFuzz (token_sort_ratio) | In-process | Task keywords + tab title/URL | No blocking |
| **Blink rate / yawn detection** | MediaPipe landmarks + heuristics | QThread | Camera feed | Not available |

**Privacy note:** Everything runs locally. No data leaves the machine. The SLM runs as a local subprocess. Camera frames are never saved to disk, they're processed in memory and discarded.

---

## Data Layer

### SqliteRepository

**File:** `app/data/repository.py`

Single SQLite database with WAL mode enabled for concurrent reads. All tables:

| Table | Purpose |
|-------|---------|
| `app_meta` | Key-value store for schema version |
| `tasks` | Task items with full metadata |
| `task_history` | Audit log of task state changes |
| `sessions` | Focus session logs |
| `focus_ticks` | Granular focus measurements (every 10s) |
| `notifications` | In-app notification history |
| `calendar_events` | Calendar entries |
| `coach_reports` | Weekly AI-generated reviews |
| `plugins` | Plugin registry |
| `plugin_meta` | Plugin schema versions |
| `profiles` | User profile (display name, avatar) |

**Schema versioning:** `CURRENT_SCHEMA_VERSION = 5`. Migrations run automatically on startup. The migration system handles both core schema changes and per-plugin migrations.

**Entities** (`app/data/entities.py`):
- `TaskItem` — 20+ fields including `energy_cost`, `focus_score_hint`, `recurrence_rule`, `parent_task_id`
- `SessionLogItem` — start/end times, outcome, focus score average, distraction count, absent seconds
- `FocusTickItem` — per-tick drift probability with session linkage
- `NotificationItem`, `CalendarEventItem`, `PluginItem`, `UserProfileItem`

All entities are `@dataclass(slots=True)` for memory efficiency.

### Signals

The repository emits Qt signals on data changes:
- `tasks_changed`, `sessions_changed`, `notifications_changed`, `calendar_changed`, `plugins_changed`, `profile_changed`

UI components connect to these to refresh automatically.

---

## UI Architecture

### MainWindow

**File:** `app/ui/main_window.py`

The main window uses a grid layout with three columns: sidebar, content area, and an optional detail overlay panel.

```
┌──────────┬─────────────────────────────┬──────────┐
│          │         Topbar              │          │
│ Sidebar  ├─────────────────────────────┤  Detail  │
│  (nav)   │                             │ Overlay  │
│          │      Page Stack             │ (hidden  │
│          │   (QStackedWidget)          │  default)│
│          │                             │          │
└──────────┴─────────────────────────────┴──────────┘
```

**Pages** (in stack order):
1. Dashboard — focus score ring, session controls, upcoming tasks, recent sessions
2. Calendar — flexible week view widget
3. Planner — AI-powered daily plan with energy patterns
4. Tasks — full task list with inline editing, filters, search, and decomposition
5. Coach — weekly review display + NL task creation chat interface
6. Account — profile management, avatar, stats overview
7. Notifications — notification list with read/dismiss
8. Plugins — plugin list with enable/disable
9. Settings — everything: vision, SLM, themes, dev tools, extension setup

Each page is wrapped in a `QScrollArea` for scrollability. Pages that support it implement `apply_metrics(UiMetrics)` for responsive resizing and `filter_content(text)` for global search.

### Theme System

**File:** `app/ui/theme.py`

Three built-in themes, switchable at runtime via the topbar palette button or settings:

| Theme | Style | Colors |
|-------|-------|--------|
| `forest_focus` | Dark, calm green | `#0E1512` bg, `#3ECF8E` primary |
| `mono_focus` | Dark, minimal blue | `#101010` bg, `#00C2FF` primary |
| `paper_daylight` | Light, paper-like | `#F7F8FA` bg, `#2364AA` primary |

Each theme is a `ThemeSpec` dataclass with ~20 color tokens. The `ThemeManager` generates a massive QSS stylesheet from these tokens combined with `UiMetrics` for dimensions. It also sets the Qt `QPalette` and applies the `Fusion` style.

**Fonts:** Space Grotesk (headings), DM Sans (body), JetBrains Mono (numeric/code). Falls back to Inter if not available.

### Responsive Metrics

**File:** `app/ui/metrics.py`

`UiMetrics` is a dataclass that computes all dimensions based on window width:
- Font sizes (`body_pt`, `title_pt`, `section_pt`, `meta_pt`)
- Spacing (`card_padding`, `card_gap`, `page_margin`)
- Control sizes (`control_height`, `compact_control_height`, `toolbar_height`)
- Border radius, border width, sidebar width, detail panel width, nav row height

When the window resizes, `build_metrics(width)` recalculates and the theme re-applies. This keeps the UI looking good from 1100px to ultrawide monitors.

### Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Ctrl+K` | Focus search bar |
| `Ctrl+N` | Open quick-add task |
| `Escape` | Close detail overlay |

### Toast Notifications

When `NotificationService` publishes a notification, a themed toast appears at the bottom-right of the window and auto-dismisses after 5 seconds. Error-level notifications get a red border, warnings get yellow.

---

## Plugin System

**File:** `app/core/plugin_api.py`

Still in early stages. Currently ships with a single demo plugin (`core.demo`).

**Architecture:**
- `PluginManager` holds a list of `PluginRuntime` objects
- Each plugin can register a database migration via `register_migration(plugin_id, migrate_fn)`
- Plugins can store per-task data via `set_task_plugin_value()` / `get_task_plugin_payload()`
- Plugin migrations are versioned independently in the `plugin_meta` table

**PluginStorageAPI protocol:**
```python
class PluginStorageAPI(Protocol):
    def register_migration(self, plugin_id: str, migrate_fn: MigrationFn) -> None: ...
    def set_task_plugin_value(self, task_id: str, plugin_id: str, key: str, value: Any) -> None: ...
    def get_task_plugin_payload(self, task_id: str, plugin_id: str) -> dict[str, Any]: ...
    def ensure_plugin_table(self, plugin_id: str, create_sql: str) -> None: ...
```

---

## Browser Extension

### Structure

Separate builds for Firefox (Manifest V2) and Chrome (Manifest V3). Both share `popup.html` and `popup.js`.

**Files:**
- `extension/firefox/` — `manifest.json`, `background.js`, `popup.html`, `popup.js`
- `extension/chrome/` — same structure, different manifest version

### How It Connects

The extension uses the WebExtensions Native Messaging API to communicate with a bridge script that the desktop app generates. The bridge script reads/writes a shared JSON state file.

**Flow:**
1. Desktop app writes current task context to `~/.abte/extension_state.json`
2. Extension background script reads this file via native messaging
3. Extension knows what task the user is working on and what keywords are relevant
4. `TabFocusGuard` decides if a tab is off-task
5. If off-task for too long, the extension shows a blocking overlay

### Installing the Extension

The settings page has a button to register the native messaging manifests. The bridge scripts are version-stamped and hash-verified to prevent stale installations from older app versions.

---

## Configuration & Settings

**File:** `app/core/settings.py` + `app/data/settings_store.py`

Uses Qt's `QSettings` for persistence (INI file on Linux, Registry on Windows).

**Key settings groups:**

| Group | Keys | Purpose |
|-------|------|---------|
| `Vision/` | `enable_gaze`, `face_landmarker_model_path`, `camera_index` | Gaze tracking config |
| `SLM/` | `model_path`, `backend`, `max_tokens`, `coach_enabled`, `decomposition_enabled`, `prefer_gpu`, `gpu_layers_override`, `cpu_threads` | Local LLM config |
| `Profile/` | `display_name`, `avatar_path`, `current_goals` | User profile |
| `Settings/` | `theme` | UI theme |
| `Development/` | `dev_reset_database`, `dev_fake_data`, `dev_show_startup_wizard` | Dev/debug flags |
| `MainWindow/` | `geometry`, `last_page` | Window state |
| `Startup/` | `first_run_completed` | First-run flag |

---

## Building with PyInstaller

### Prerequisites

```bash
pip install pyinstaller
```

Make sure all runtime dependencies are installed in your environment. Run the app at least once to confirm it works before packaging.

### Linux Build

```bash
pyinstaller --noconfirm --clean \
  --name abte \
  --windowed \
  --add-data "app:app" \
  --add-data "extension:extension" \
  --hidden-import=PySide6.QtCore \
  --hidden-import=PySide6.QtGui \
  --hidden-import=PySide6.QtWidgets \
  --hidden-import=PySide6.QtSvg \
  --hidden-import=PySide6.QtSvgWidgets \
  --hidden-import=qtawesome \
  --hidden-import=mediapipe \
  --hidden-import=cv2 \
  --hidden-import=lightgbm \
  --hidden-import=sklearn \
  --hidden-import=sklearn.cluster \
  --hidden-import=rapidfuzz \
  --hidden-import=rapidfuzz.fuzz \
  --hidden-import=ortools \
  --hidden-import=ortools.sat \
  --hidden-import=ortools.sat.python \
  --hidden-import=ortools.sat.python.cp_model \
  --collect-data mediapipe \
  --collect-data qtawesome \
  --collect-submodules PySide6 \
  main.py
```

**Important Linux notes:**
- The `--add-data` separator is `:` on Linux/macOS.
- If you get missing Qt platform plugin errors, add:
  ```
  --add-data "$(python -c 'import PySide6; print(PySide6.__path__[0])')/Qt/plugins:PySide6/Qt/plugins"
  ```
- MediaPipe bundles `.tflite` model files, `--collect-data mediapipe` grabs those.
- If you have a face landmarker model at a custom path, make sure to bundle it too:
  ```
  --add-data "/path/to/face_landmarker.task:models"
  ```
- The output will be in `dist/abte/`. Run `./dist/abte/abte` to test.

### Windows Build

```powershell
pyinstaller --noconfirm --clean ^
  --name abte ^
  --windowed ^
  --add-data "app;app" ^
  --add-data "extension;extension" ^
  --hidden-import=PySide6.QtCore ^
  --hidden-import=PySide6.QtGui ^
  --hidden-import=PySide6.QtWidgets ^
  --hidden-import=PySide6.QtSvg ^
  --hidden-import=PySide6.QtSvgWidgets ^
  --hidden-import=qtawesome ^
  --hidden-import=mediapipe ^
  --hidden-import=cv2 ^
  --hidden-import=lightgbm ^
  --hidden-import=sklearn ^
  --hidden-import=sklearn.cluster ^
  --hidden-import=rapidfuzz ^
  --hidden-import=rapidfuzz.fuzz ^
  --hidden-import=ortools ^
  --hidden-import=ortools.sat ^
  --hidden-import=ortools.sat.python ^
  --hidden-import=ortools.sat.python.cp_model ^
  --collect-data mediapipe ^
  --collect-data qtawesome ^
  --collect-submodules PySide6 ^
  main.py
```

**Important Windows notes:**
- The `--add-data` separator is `;` on Windows (not `:`).
- Use `^` for line continuation in cmd, or backtick `` ` `` in PowerShell.
- Windows Defender sometimes flags freshly-built PyInstaller executables. You may need to whitelist the output directory.
- The active window tracker uses `ctypes` to call `user32.dll` and `kernel32.dll` directly, so no extra DLLs are needed.
- Camera access requires permission on Windows 10/11. If the gaze service fails silently, check camera privacy settings.

### One-File Build (optional)

If you want a single executable instead of a folder:

```bash
# add this flag:
--onefile
```

This is slower to start (it extracts to a temp dir on launch) but easier to distribute. Not recommended for development.

### Post-Build Verification

After building, verify:
1. App launches without errors: `./dist/abte/abte` (Linux) or `dist\abte\abte.exe` (Windows)
2. Theme loads correctly (not unstyled Qt)
3. Gaze tracking starts (if camera is available and model file is bundled)
4. Settings persist between runs
5. Browser extension bridge can register

### Common PyInstaller Issues

| Issue | Fix |
|-------|-----|
| `ModuleNotFoundError: No module named 'PySide6.QtSvg'` | Add `--hidden-import=PySide6.QtSvg` |
| Missing Qt platform plugin | Bundle `PySide6/Qt/plugins` with `--add-data` |
| MediaPipe model files not found | Use `--collect-data mediapipe` |
| qtawesome icons don't render | Use `--collect-data qtawesome` |
| LightGBM `.so`/`.dll` not found | Use `--collect-binaries lightgbm` |
| OR-Tools import error | Add all three `--hidden-import=ortools.*` entries |
| App crashes on exit (SIGSEGV) | Known issue, see below |
| `cv2` import fails | Use `--collect-binaries cv2` if `--hidden-import` alone doesn't work |

---

## Development Setup

### 1. Clone & venv

```bash
git clone <your-repo-url>
cd abte
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
```

### 2. Install dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Run

```bash
python main.py
```

Optional flags:
- `--debug` — enable debug logging
- `--reset-db` — wipe and recreate the database on startup

### 4. Run tests

```bash
pytest
```

### 5. Dev tools in the app

The Settings page has a "Development" section where you can:
- Reset the database
- Seed fake data (configurable counts for tasks, sessions, calendar events)
- Force-show the startup wizard
- Toggle the SLM model path and backend

### 6. Fake data

`FakeDataService` (`app/services/fake_data_service.py`) generates realistic test data:
- Tasks with varied statuses, priorities, tags, and dates
- Focus sessions with realistic score distributions
- Focus ticks linked to sessions
- Calendar events
- Coach reports

Trigger it from Settings > Development > "Generate Fake Data" or programmatically:
```python
from app.services.fake_data_service import FakeDataService
fake = FakeDataService(repository)
fake.generate_all(tasks_count=50, sessions_count=20, days_back=30)
```

---

## Known Issues & Gotchas

### SIGSEGV on exit (Linux)

The app sometimes crashes with exit code 139 (SIGSEGV) when closing. This is related to how MediaPipe and PySide6 clean up threads on shutdown. The gaze worker thread might still be accessing MediaPipe objects when Qt tears down the event loop.

**Current mitigation:** `GazeService._stop_async()` uses `QTimer.singleShot` to defer the thread join, giving the worker time to release resources before the main thread exits. It's not perfect.

### Wayland + GNOME

Active window detection doesn't work on GNOME under Wayland. There's no public API for it (by design, GNOME considers it a privacy issue). The app falls back to returning "unknown" for window titles, which means the focus drift prediction loses one of its input features. X11 and KDE Wayland work fine.

### SLM inference latency

Local LLM calls through llama.cpp can take 5-30+ seconds depending on model size and hardware. The UI doesn't block (it runs as a subprocess), but the user has to wait. The hardware planner tries to pick the fastest execution target, but there's only so much you can do with a 7B model on a CPU.

### Browser extension registration

The native messaging manifests have to be placed in specific directories per browser. The app generates these, but if the user has a non-standard browser installation path, registration might fail silently. Check the logs if the extension can't connect.

### Thread safety of GazeWorker

All MediaPipe objects are created inside `GazeWorker.run()` specifically because MediaPipe isn't thread-safe. If you ever need to access the landmarker from the main thread, don't. Always communicate through signals.

### Database hot path

`FocusSessionService` caches the current session status in memory to avoid hitting SQLite at 10Hz. If you modify session state directly in the database (e.g., via a plugin or external tool), the cache will be stale until the next session start/stop.

---

## Dependencies

Core runtime dependencies:

| Package | Version | Purpose |
|---------|---------|---------|
| PySide6 | 6.x | Qt GUI framework |
| opencv-python | 4.x | Camera capture and image processing |
| mediapipe | 0.10.x | Face landmark detection |
| lightgbm | 4.x | Focus drift prediction model |
| scikit-learn | 1.x | KMeans clustering for energy patterns |
| ortools | 9.x | Constraint-based task scheduling |
| rapidfuzz | 3.x | Fuzzy string matching for tab relevance |
| qtawesome | 1.x | Material Design icons for Qt |
| numpy | 1.x/2.x | Numerical operations |

Optional (for SLM features):
- `llama-cli` or `llama` binary on PATH (llama.cpp)
- `onnxruntime` (pip install, for ONNX backend)

---

*That's the full picture. If something's not documented here, check the source. Most files have decent docstrings and the signal wiring in `bootstrap.py` tells you how everything connects.*
