package app.linernotes

import android.content.Context
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import java.io.File

/**
 * Liner Notes' engine running on the phone itself, for when there's no PC: the same Python code as
 * the Windows app (app/core.py), serving the same screen at http://127.0.0.1:8766.
 * The PC uses 8765, which a USB cable forwards to the phone, so the two never clash.
 */
object Engine {
    const val PORT = 8766
    const val URL = "http://127.0.0.1:$PORT"
    @Volatile private var started = false

    fun isLocal(url: String?) = url != null && url.trimEnd('/') == URL

    fun start(c: Context) {
        if (started) return
        synchronized(this) {
            if (started) return
            started = true
            val ctx = c.applicationContext
            Thread({
                try {
                    val here = File(ctx.filesDir, "engine")
                    copyAssets(ctx, here)
                    if (!Python.isStarted()) Python.start(AndroidPlatform(ctx))
                    Python.getInstance().getModule("android_main")
                        .callAttr("start", ctx.filesDir.absolutePath, here.absolutePath, PORT, Updater.current(ctx))
                } catch (e: Throwable) {
                    android.util.Log.e("LinerNotes", "engine failed", e)
                    started = false
                }
            }, "engine").apply { isDaemon = true; start() }
        }
    }

    /** Wait until the engine answers (first start takes a few seconds while Python loads). */
    fun waitReady(c: Context, timeoutMs: Long = 30_000): Boolean {
        val end = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < end) {
            if (Link.call(c, URL, "/api/now", timeoutMs = 1500)?.first == 200) return true
            Thread.sleep(400)
        }
        return false
    }

    /** display.html and the fonts, fresh for each version of the app. */
    private fun copyAssets(c: Context, dir: File) {
        val stamp = File(dir, ".version")
        val ver = Updater.current(c)
        if (stamp.exists() && stamp.readText() == ver) return
        dir.mkdirs()
        fun copy(path: String) {
            val list = c.assets.list(path) ?: emptyArray()
            if (list.isEmpty()) {
                val out = File(dir, path.removePrefix("engine/").removePrefix("engine"))
                out.parentFile?.mkdirs()
                c.assets.open(path).use { inp -> out.outputStream().use { inp.copyTo(it) } }
            } else list.forEach { copy("$path/$it") }
        }
        copy("engine")
        stamp.writeText(ver)
    }
}
