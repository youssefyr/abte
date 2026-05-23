# ABTE: AI-Powered Productivity Desktop App

ABTE is a cross-platform desktop productivity tool built with PySide6, integrating advanced AI, machine learning, computer vision, and NLP features. Designed for extensibility and developer-friendliness, ABTE empowers users to optimize workflows, automate tasks, and leverage intelligent insights—all within a modern, plugin-enabled GUI.

---

## Features

- **Modern Desktop UI**: Built with PySide6 and Qt, featuring custom widgets, dynamic runtime themes, and micro-animations.
- **AI & ML Integration**: High-frequency focus drift tracking using custom-trained LightGBM, constraint-based task scheduling (Google OR-Tools), and energy pattern clustering (KMeans).
- **Local Small Language Model (SLM)**: Modular GGUF catalog (`LFM-2.5 1.2B Thinking` and `Phi-3 Mini 4K`) with automated download discovery and execution target optimization (CPU/GPU/Hybrid with hardware planner).
- **Asynchronous AI Workers**: Non-blocking asynchronous QThread-based pipeline execution to prevent PySide6 main thread freeze.
- **Customizable Sidebar Template Engine**: Render dynamic statistics and app states directly in the sidebar using 12 customizable template variables (e.g. active tasks, focus minutes, unread notifications).
- **Computer Vision**: High-performance eye-gaze tracking and facial landmarker pipeline with real-time blur and low-light enhancement.
- **NLP & Task Automation**: Smart NLP-based browser tab relevance checking using fuzzy string comparison (`rapidfuzz`) and inline task shorthand quick-add.
- **Plugin System**: Modular plugins register independent SQLite migrations, schema versioning, and customized data payloads.
- **Robust Testing & Clean Infrastructure**: Thoroughly cleaned test suite leveraging mock systems to verify scheduling, SLM backends, templates, and UI components.
- **Cross-Platform**: Runs natively on Linux, Windows, and macOS.

---

## Project Structure

```
app/
  calibration/  # Vision calibration and data storage
  core/         # Core engines, settings, logging, plugin API
  data/         # Data entities and SQLite repositories
  models/       # ML models, serializers, and runtimes (LightGBM, etc.)
  services/     # Application and system services (including services/slm/)
  tests/        # Comprehensive test suite (pytest)
  ui/           # UI pages, components, widgets, and helpers
main.py         # Application entry point
find_unused_py_files.py, count_py_lines.py, test_fix.py  # Dev utilities
```

---

## Getting Started (Developer Guide)

### 1. Clone the Repository

```sh
git clone <your-repo-url>
cd abte
```

### 2. Create and Activate a Virtual Environment

```sh
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

### 3. Install Dependencies

```sh
pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Run the Application

```sh
python main.py
```

### 5. Run Tests

```sh
pytest
```

---

## Packaging Guide (PyInstaller)

### 1. Install PyInstaller

```sh
pip install pyinstaller
```

### 2. Build the Standalone Executable

```sh
pyinstaller --noconfirm --clean \
  --name abte \
  --windowed \
  --add-data "app:app" \
  --add-data "extension:extension" \
  --hidden-import=PySide6.QtCore \
  --hidden-import=PySide6.QtGui \
  --hidden-import=PySide6.QtWidgets \
  --hidden-import=qtawesome \
  main.py
```

**Notes:**
- Adjust `--add-data` paths as needed for your OS (use `;` on Windows, `:` on Unix).
- PySide6 may require special hooks for Qt plugins (platforms, imageformats, etc.). If you encounter missing plugin errors, add:
  ```
  --add-data "<venv_path>/lib/python3.*/site-packages/PySide6/Qt/plugins:PySide6/Qt/plugins"
  ```
- For custom icons, models, or data files, ensure they are included with `--add-data`.
- See [PyInstaller docs](https://pyinstaller.org/en/stable/) for advanced options.

---

## Extensibility

- **Plugin API**: Add new features by dropping Python modules into the `app/plugins/` directory.
- **Custom Models**: Integrate new ML/CV/NLP models by extending the `app/services/slm/` and `app/models/` modules.
- **UI Customization**: Modify or extend UI components in `app/ui/`.

---

## Contributing

Pull requests and issues are welcome! Please ensure code is tested and follows project conventions.

---

## License

[MIT License](LICENSE) (or specify your license here)

---

**For questions or support, open an issue or contact the maintainers.**
