"""Liner Notes' engine inside the Android app, so the phone works without a PC.

MainActivity's Engine starts this once, on its own thread: it serves the same screen the PC serves,
on the phone itself (http://127.0.0.1:<port>), and the app's LinkService records with the phone's mic
and uploads to it, the same way it does for a PC.
"""
import asyncio
import os
import sys


def start(data_dir, here, port, version):
    os.environ["APPDATA"] = data_dir                # settings, history and the log live here
    os.environ["LINERNOTES_HERE"] = here            # display.html and the fonts, copied out of the app
    os.environ["LINERNOTES_ENGINE"] = "android"
    # The log goes to a file (as on the PC) as well as Android's log
    try:
        d = os.path.join(data_dir, "Liner Notes")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, "linernotes.log")
        if os.path.exists(path) and os.path.getsize(path) > 2_000_000:
            os.replace(path, path + ".old")
        f = open(path, "a", encoding="utf-8", buffering=1)

        class Tee:
            def __init__(self, a): self.a = a
            def write(self, s):
                try: f.write(s)
                except Exception: pass
                try: return self.a.write(s)
                except Exception: return len(s)
            def flush(self):
                for x in (f, self.a):
                    try: x.flush()
                    except Exception: pass
        sys.stdout, sys.stderr = Tee(sys.stdout), Tee(sys.stderr)
    except Exception:
        pass
    try:
        import _version
        _version.VERSION = version
    except Exception:
        pass
    import core
    core.CFG["port"] = int(port)
    core.CFG["allow_other_devices"] = False         # only this phone reaches it
    core.CFG["audio_device"] = "phone"
    app = core.build_app(False)

    async def run():
        from aiohttp import web
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", int(port)).start()
        core.log(f"Liner Notes engine running on this phone at http://127.0.0.1:{port}")
        while True:
            await asyncio.sleep(3600)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(run())
    except Exception:
        import traceback
        try:
            core.log("engine stopped:\n" + traceback.format_exc())
        except Exception:
            print(traceback.format_exc(), file=sys.stderr)
        raise
