"""Launch the SceneVoice Studio editor."""

import sys
import tkinter as tk
from pathlib import Path

from .app import Studio


def main():
    root = tk.Tk()
    studio = Studio(root)
    if len(sys.argv) > 1:
        studio.load(Path(sys.argv[1]))
    root.after_idle(studio.setup_on_start)
    root.mainloop()


if __name__ == "__main__":
    main()
