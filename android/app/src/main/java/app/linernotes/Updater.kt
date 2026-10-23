package app.linernotes

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.core.content.FileProvider
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

/**
 * Updates for the copy installed from GitHub (the "direct" build). The Play Store copy never checks:
 * Play updates it. Looks at the latest GitHub release, downloads the app's APK and hands it to Android's installer.
 */
object Updater {
    private const val LATEST = "https://api.github.com/repos/PostModernPost/linernotes/releases/latest"
    private const val ASSET = "LinerNotes-Android.apk"

    val enabled get() = BuildConfig.SELF_UPDATE

    @Volatile var available: String? = null       // newer version, if any
    @Volatile private var apkUrl: String? = null
    @Volatile var state = "idle"                   // idle, downloading, ready, failed
    private var lastCheck = 0L

    fun current(c: Context): String = try { c.packageManager.getPackageInfo(c.packageName, 0).versionName ?: "" } catch (e: Exception) { "" }

    private fun newer(a: String, b: String): Boolean {
        val x = a.trim().removePrefix("v").split(".").map { it.toIntOrNull() ?: 0 }
        val y = b.trim().removePrefix("v").split(".").map { it.toIntOrNull() ?: 0 }
        for (i in 0 until maxOf(x.size, y.size)) {
            val p = x.getOrElse(i) { 0 }; val q = y.getOrElse(i) { 0 }
            if (p != q) return p > q
        }
        return false
    }

    /** Check at most every 6 hours (call from a background thread). */
    fun check(c: Context, force: Boolean = false) {
        if (!enabled) return
        val now = System.currentTimeMillis()
        if (!force && now - lastCheck < 6 * 3600_000L) return
        lastCheck = now
        try {
            val conn = URL(LATEST).openConnection() as HttpURLConnection
            conn.connectTimeout = 8000; conn.readTimeout = 8000
            conn.setRequestProperty("Accept", "application/vnd.github+json")
            val j = JSONObject(conn.inputStream.bufferedReader().readText())
            val tag = j.optString("tag_name").removePrefix("v")
            val assets = j.optJSONArray("assets") ?: return
            var url: String? = null
            for (i in 0 until assets.length()) {
                val a = assets.getJSONObject(i)
                if (a.optString("name") == ASSET) url = a.optString("browser_download_url")
            }
            if (url != null && tag.isNotEmpty() && newer(tag, current(c))) { available = tag; apkUrl = url } else { available = null }
        } catch (e: Exception) { }
    }

    /** Download the update and open Android's installer (call from a background thread). */
    fun install(c: Context, onUi: (Runnable) -> Unit) {
        val url = apkUrl ?: return
        if (Build.VERSION.SDK_INT >= 26 && !c.packageManager.canRequestPackageInstalls()) {
            // Android asks once: allow Liner Notes to install its own updates
            onUi(Runnable {
                try {
                    c.startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:${c.packageName}"))
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                } catch (e: Exception) { }
            })
            return
        }
        state = "downloading"
        try {
            val dir = File(c.cacheDir, "updates").apply { mkdirs(); listFiles()?.forEach { it.delete() } }
            val f = File(dir, "LinerNotes-$available.apk")
            var conn = URL(url).openConnection() as HttpURLConnection
            conn.instanceFollowRedirects = true
            conn.connectTimeout = 10000; conn.readTimeout = 30000
            // GitHub sends release downloads through a redirect to another host
            var hops = 0
            while (conn.responseCode in 300..399 && hops++ < 5) {
                val next = conn.getHeaderField("Location") ?: break
                conn = URL(next).openConnection() as HttpURLConnection
                conn.connectTimeout = 10000; conn.readTimeout = 30000
            }
            conn.inputStream.use { inp -> f.outputStream().use { inp.copyTo(it) } }
            state = "ready"
            val uri = FileProvider.getUriForFile(c, "${c.packageName}.files", f)
            onUi(Runnable {
                try {
                    c.startActivity(Intent(Intent.ACTION_VIEW).setDataAndType(uri, "application/vnd.android.package-archive")
                        .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK))
                } catch (e: Exception) { state = "failed" }
            })
        } catch (e: Exception) { state = "failed" }
    }

    fun json(c: Context): String = JSONObject()
        .put("enabled", enabled).put("current", current(c))
        .put("available", available ?: "").put("state", state).toString()
}
