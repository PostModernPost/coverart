package app.linernotes

import android.content.Context
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.net.wifi.WifiManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors

/** Where Liner Notes on the PC is, and the token this phone got when it was connected over Wi-Fi. */
object Link {
    const val USB_URL = "http://127.0.0.1:8765"     // over USB the PC app points the phone's localhost at itself
    const val UA_TAG = "LinerNotesApp/1"

    private const val PREFS = "link"

    fun prefs(c: Context) = c.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    fun savedUrl(c: Context): String? = prefs(c).getString("url", null)
    fun saveUrl(c: Context, url: String) = prefs(c).edit().putString("url", url).apply()
    fun forget(c: Context) = prefs(c).edit().remove("url").apply()
    /** The person chose to use the phone on its own (or it started that way): don't keep offering new PCs. */
    fun soloChosen(c: Context) = prefs(c).getBoolean("solo", false)
    fun setSoloChosen(c: Context, on: Boolean) = prefs(c).edit().putBoolean("solo", on).apply()

    fun token(c: Context, url: String): String? = prefs(c).getString("token:" + host(url), null)
    fun saveToken(c: Context, url: String, token: String) = prefs(c).edit().putString("token:" + host(url), token).apply()
    fun dropToken(c: Context, url: String) = prefs(c).edit().remove("token:" + host(url)).apply()

    fun host(url: String): String = try { URL(url).host } catch (e: Exception) { url }
    fun isUsb(url: String) = host(url) == "127.0.0.1" || host(url) == "localhost"

    /** A simple blocking HTTP call (run it off the main thread). Returns (status, body) or null if unreachable. */
    fun call(c: Context, url: String, path: String, method: String = "GET", body: String? = null,
             timeoutMs: Int = 2500, contentType: String = "application/json", bytes: ByteArray? = null): Pair<Int, String>? {
        return try {
            val conn = URL(url.trimEnd('/') + path).openConnection() as HttpURLConnection
            conn.connectTimeout = timeoutMs
            conn.readTimeout = timeoutMs * 2
            conn.requestMethod = method
            conn.setRequestProperty("User-Agent", "Mozilla/5.0 (Linux; Android ${Build.VERSION.RELEASE}) $UA_TAG")
            token(c, url)?.let { conn.setRequestProperty("X-LinerNotes-Device", it) }
            if (body != null || bytes != null) {
                conn.doOutput = true
                conn.setRequestProperty("Content-Type", contentType)
                conn.outputStream.use { it.write(bytes ?: body!!.toByteArray()) }
            }
            val code = conn.responseCode
            val text = (if (code < 400) conn.inputStream else conn.errorStream)?.bufferedReader()?.use { it.readText() } ?: ""
            conn.disconnect()
            Pair(code, text)
        } catch (e: Exception) {
            null
        }
    }

    /** Is Liner Notes answering at this address? */
    fun alive(c: Context, url: String): Boolean {
        val r = call(c, url, "/api/now", timeoutMs = 1500) ?: return false
        return r.first == 200 && r.second.contains("\"version\"")
    }

    /** Can this phone change settings there? Over USB always; over Wi-Fi only once it's connected (paired). */
    fun trusted(c: Context, url: String): Boolean {
        if (isUsb(url)) return true
        if (token(c, url) == null) return false
        val r = call(c, url, "/api/devices") ?: return true      // unreachable right now: keep the token
        if (r.first == 403) { dropToken(c, url); return false }  // the PC disconnected this phone
        return true
    }

    fun deviceName(): String {
        val m = Build.MODEL ?: "Android phone"
        val maker = Build.MANUFACTURER ?: ""
        return if (m.lowercase().startsWith(maker.lowercase())) m else "$maker $m".trim()
    }
}

/** Finds Liner Notes on the Wi-Fi: the PC app announces itself as _linernotes._tcp (Bonjour/mDNS). */
class Finder(private val ctx: Context, private val onFound: (name: String, url: String) -> Unit) {
    private val nsd = ctx.getSystemService(Context.NSD_SERVICE) as NsdManager
    private var listener: NsdManager.DiscoveryListener? = null
    private var lock: WifiManager.MulticastLock? = null
    private val main = Handler(Looper.getMainLooper())
    private val resolver = Executors.newSingleThreadExecutor()

    fun start() {
        stop()
        try {
            val wifi = ctx.applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
            lock = wifi.createMulticastLock("linernotes").apply { setReferenceCounted(false); acquire() }
        } catch (e: Exception) { }
        val l = object : NsdManager.DiscoveryListener {
            override fun onDiscoveryStarted(serviceType: String) {}
            override fun onDiscoveryStopped(serviceType: String) {}
            override fun onStartDiscoveryFailed(serviceType: String, errorCode: Int) {}
            override fun onStopDiscoveryFailed(serviceType: String, errorCode: Int) {}
            override fun onServiceLost(service: NsdServiceInfo) {}
            override fun onServiceFound(service: NsdServiceInfo) {
                resolver.execute { resolve(service) }
            }
        }
        listener = l
        try { nsd.discoverServices("_linernotes._tcp.", NsdManager.PROTOCOL_DNS_SD, l) } catch (e: Exception) { }
    }

    @Suppress("DEPRECATION")
    private fun resolve(service: NsdServiceInfo) {
        val done = java.util.concurrent.CountDownLatch(1)
        try {
            nsd.resolveService(service, object : NsdManager.ResolveListener {
                override fun onResolveFailed(serviceInfo: NsdServiceInfo, errorCode: Int) { done.countDown() }
                override fun onServiceResolved(info: NsdServiceInfo) {
                    val host = info.host?.hostAddress
                    if (host != null && !host.contains(":")) {
                        val name = info.attributes["name"]?.let { String(it) } ?: info.serviceName.removePrefix("Liner Notes on ")
                        val url = "http://$host:${info.port}"
                        main.post { onFound(name, url) }
                    }
                    done.countDown()
                }
            })
            done.await(5, java.util.concurrent.TimeUnit.SECONDS)   // one resolve at a time (older Androids allow only one)
        } catch (e: Exception) { }
    }

    fun stop() {
        listener?.let { try { nsd.stopServiceDiscovery(it) } catch (e: Exception) { } }
        listener = null
        try { lock?.release() } catch (e: Exception) { }
        lock = null
    }
}

/** Connecting this phone over Wi-Fi: ask the PC, show the code, wait for the OK on the PC. */
object Pairing {
    data class Start(val id: String, val code: String)

    fun start(c: Context, url: String): Start? {
        val r = Link.call(c, url, "/api/pair/start", "POST", JSONObject().put("name", Link.deviceName()).toString()) ?: return null
        if (r.first != 200) return null
        val j = JSONObject(r.second)
        return Start(j.getString("id"), j.getString("code"))
    }

    /** "pending", "approved" (token saved), "denied" or "expired". */
    fun check(c: Context, url: String, id: String): String {
        val r = Link.call(c, url, "/api/pair/status?id=" + java.net.URLEncoder.encode(id, "UTF-8")) ?: return "pending"
        val j = try { JSONObject(r.second) } catch (e: Exception) { return "pending" }
        val state = j.optString("state", "pending")
        if (state == "approved") j.optString("token").takeIf { it.isNotEmpty() }?.let { Link.saveToken(c, url, it) }
        return state
    }
}
