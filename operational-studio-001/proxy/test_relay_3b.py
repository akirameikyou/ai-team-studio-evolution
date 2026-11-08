"""A test relay for 3-B: this folder's relay code with its own exchange folder, without proxy/.env, on port 8797.

Run from operational-studio-001/proxy:
    python -X utf8 test_relay_3b.py <exchange folder for the test>      (e.g. %TEMP%\\ats-3b-test)
It never reads proxy/.env (so no AI API is called) and never uses port 8787.
It refuses a relay's own exchange folder: this worktree's, or any folder named proxy/exchange (such as the
operational one in another worktree), so a test record is never created next to a real one.
Stop it with Ctrl+C. The record stays in <exchange folder>/_record until you delete the folder.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def refused(ex):
    """The reason this folder must not be used for a test, or None."""
    if ex == (HERE / 'exchange').resolve():
        return "this is this relay's own exchange folder"
    if ex.name.lower() == 'exchange' and ex.parent.name.lower() == 'proxy':
        return "this looks like a relay's own exchange folder (proxy/exchange), for example the operational one"
    return None


if __name__ == '__main__':
    if len(sys.argv) != 2:
        sys.exit('usage: python -X utf8 test_relay_3b.py <exchange folder for the test>')
    ex = Path(sys.argv[1]).resolve()
    why = refused(ex)
    if why:
        sys.exit(f'refused: {why}: {ex}\ngive a separate folder for the test, e.g. %TEMP%\\ats-3b-test')
    sys.path.insert(0, str(HERE))
    import studio_proxy as sp  # noqa: E402

    sp.EXCHANGE = ex
    sp.load_env = lambda: None          # proxy/.env is never read
    ex.mkdir(parents=True, exist_ok=True)
    print(f'[test relay 3-B] exchange={ex} port=8797 (.env not read)', flush=True)
    sp.main(['--port', '8797'])
