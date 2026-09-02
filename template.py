from pathlib import Path

# Folders
folders = [
    "static",
    "templates",
    "tools",
]

# Files
files = [
    "static/script.js",
    "static/style.css",

    "templates/index.html",

    "tools/__init__.py",
    "tools/flight_tool.py",
    "tools/tavily_tool.py",

    ".dockerignore",
    ".gitignore",
    "Dockerfile",
    "LICENSE",
    "README.md",
    "app.py",
    "backend.py",
    "demo.excalidraw",
]

# Create folders if they don't exist
for folder in folders:
    Path(folder).mkdir(parents=True, exist_ok=True)

# Create empty files only if they don't exist
for file in files:
    path = Path(file)

    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        print(f"Created: {file}")
    else:
        print(f"Skipped:  {file}")

print("\nDone.")


# To run the code : uvicorn app:app --reload --host 127.0.0.1 --port 8000
