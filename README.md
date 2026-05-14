# ABTE: AI-Powered Productivity Desktop App

ABTE is a cross-platform desktop productivity tool built with PySide6, integrating advanced AI, machine learning, computer vision, and NLP features. Designed for extensibility and developer-friendliness, ABTE empowers users to optimize workflows, automate tasks, and leverage intelligent insights—all within a modern, plugin-enabled GUI.

---

## Features

- **Modern Desktop UI**: Built with PySide6 and Qt, featuring custom widgets, themes, and animations.
- **AI & ML Integration**: Includes models for state prediction, scheduling, focus tracking, and more.
- **Computer Vision**: Face landmark detection, feature extraction, and calibration modules.
- **NLP & Task Automation**: Smart scheduling, task management, and natural language processing.
- **Plugin System**: Easily extend core functionality with custom plugins and integrations.
- **Robust Testing**: Comprehensive test suite using pytest.
- **Cross-Platform**: Runs on Linux, Windows, and macOS.

---

## Project Structure

```
app/
  ai/           # AI, ML, and NLP models and services
  calibration/  # Vision calibration and data storage
  core/         # Core engines, settings, logging, plugin API
  data/         # Data entities and repositories
  models/       # ML models and runtime logic
  services/     # Application and system services
  tests/        # Test suite (pytest)
  ui/           # UI components, widgets, and helpers
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
  --add-data "app/ui:app/ui" \
  --add-data "app/models:app/models" \
  --add-data "app/data:app/data" \
  --add-data "app/calibration:app/calibration" \
  --add-data "app/ai:app/ai" \
  --add-data "app/services:app/services" \
  --add-data "app/core:app/core" \
  --add-data "app/tests:app/tests" \
  --add-data "app/plugins:app/plugins" \
  --add-data "app/vision:app/vision" \
  --add-data "app:app" \
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
- **Custom Models**: Integrate new ML/CV/NLP models by extending the `app/ai/` and `app/models/` modules.
- **UI Customization**: Modify or extend UI components in `app/ui/`.

---

## Contributing

Pull requests and issues are welcome! Please ensure code is tested and follows project conventions.

---

## License

[MIT License](LICENSE) (or specify your license here)

---

**For questions or support, open an issue or contact the maintainers.**
