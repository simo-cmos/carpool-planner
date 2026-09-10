"""
Drivers Manager Project
========================
Entry point — launches the tkinter GUI.

Usage:
    python -m legacy.main
"""

import tkinter as tk
from legacy.gui import DriversManagerApp


def main():
    root = tk.Tk()
    app = DriversManagerApp(root)          # noqa: F841
    root.mainloop()


if __name__ == "__main__":
    main()
