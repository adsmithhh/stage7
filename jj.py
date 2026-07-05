from __future__ import annotations

from runtime.api import ensure_daily_summary_folder, run_application


def main() -> None:
    """
    Main entry point for the simulation.
    Acts as a bootstrap wrapper to ensure filesystem integrity (daily folders)
    before launching the core application runtime.
    """
    # 1. Ensure the daily summary folder exists (e.g., 'data/summaries/05.25')
    summary_path = ensure_daily_summary_folder()
    print(f"--- [STARTUP] Daily Summary Folder Ready: {summary_path.name} ---")
    
    # 2. Launch the application
    run_application()


if __name__ == "__main__":
    main()
