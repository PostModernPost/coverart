package app.linernotes

import android.Manifest
import android.annotation.SuppressLint
import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.Message
import android.view.View
import android.view.WindowInsets
import android.view.WindowInsetsController
import android.view.WindowManager
import android.webkit.CookieManager
import android.webkit.JavascriptInterface
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.core.content.FileProvider
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.net.URL
import java.util.concurrent.Executors

/**
 * The phone app: finds Liner Notes on the PC (over USB, or on the Wi-Fi), connects, and shows the same
 * screen the PC shows, full screen. The microphone, keeping the screen on, waking it and sharing are
 * handled here (the page calls window.LinerNotesApp), so they also work over Wi-Fi.
 */
class MainActivity : Activity() {

    private lateinit var web: WebView
    private val main = Handler(Looper.getMainLooper())
    private val io = Executors.newSingleThreadExecutor()
    private var server: String? = null           // the PC page currently shown
    private var finder: Finder? = null
    private val found = linkedMapOf<String, String>()   // url -> PC name
    private var pairing: Pairing.Start? = null
    private var pairUrl: String? = null
    private var dark = false
    private var askedMic = false

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (Build.VERSION.SDK_INT >= 27) { setShowWhenLocked(true); setTurnScreenOn(true) }
        web = WebView(this)
        web.setBackgroundColor(Color.BLACK)
        setContentView(web)
        fullScreen()

        with(web.settings) {
            javaScriptEnabled = true
            domStorageEnabled = true
            mediaPlaybackRequiresUserGesture = false
            setSupportMultipleWindows(true)
            userAgentString = "$userAgentString ${Link.UA_TAG}"
        }
        CookieManager.getInstance().setAcceptCookie(true)
        web.addJavascriptInterface(Bridge(), "LinerNotesApp")
        web.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean = openOutside(request.url)
            @Deprecated("old WebView")
            override fun shouldOverrideUrlLoading(view: WebView, url: String): Boolean = openOutside(Uri.parse(url))
        }
        web.webChromeClient = object : WebChromeClient() {
            // links that open a new tab (Spotify, Discogs, Buy me a coffee...) open in the phone's browser
            override fun onCreateWindow(view: WebView, isDialog: Boolean, isUserGesture: Boolean, resultMsg: Message): Boolean {
                val probe = WebView(this@MainActivity)
                probe.webViewClient = object : WebViewClient() {
                    override fun shouldOverrideUrlLoading(v: WebView, request: WebResourceRequest): Boolean {
                        openExternal(request.url); v.destroy(); return true
                    }
                }
                (resultMsg.obj as WebView.WebViewTransport).webView = probe
                resultMsg.sendToTarget()
                return true
            }
        }
        web.setDownloadListener { url, _, _, _, _ -> openExternal(Uri.parse(url)) }

        LinkService.onState = { j -> main.post { onPcState(j) } }
        showSetup()
        connect()
        io.execute { Updater.check(this) }
    }

    // ---------------------------------------------------------------- finding and connecting to the PC
    /**
     * At start: Liner Notes on a PC if there's one this phone knows (the PC used last time, one over USB,
     * or one on the Wi-Fi it was connected to before). Otherwise the phone works on its own.
     */
    private fun connect(auto: Boolean = true) {
        say(JSONObject().put("view", "searching"))
        io.execute {
            val saved = Link.savedUrl(this)
            // 1. the PC used last time, 2. a PC over USB, 3. look on the Wi-Fi
            for (u in listOfNotNull(saved, Link.USB_URL).distinct()) {
                if (Link.alive(this, u)) {
                    if (Link.trusted(this, u)) { main.post { open(u) }; return@execute }
                    if (!auto || !Link.soloChosen(this)) { main.post { startPairing(u, null) }; return@execute }
                }
            }
            main.post { search(auto) }
        }
    }

    /** auto: at start (no PC found means on its own); otherwise the person asked to connect to a PC. */
    private fun search(auto: Boolean = false) {
        found.clear()
        say(JSONObject().put("view", "searching"))
        finder?.stop()
        val gen = Any(); searchGen = gen
        finder = Finder(this) { name, url ->
            if (server != null || pairing != null || searchGen !== gen) return@Finder
            found[url] = name
            if (auto) {
                // one this phone was connected to before: straight there
                io.execute { if (Link.token(this, url) != null && Link.trusted(this, url)) main.post { if (server == null && searchGen === gen) open(url) } }
            } else {
                say(JSONObject().put("view", "found").put("list", JSONArray(found.map { JSONObject().put("name", it.value).put("url", it.key) })))
            }
        }.also { it.start() }
        if (auto) {
            main.postDelayed({
                if (server != null || pairing != null || searchGen !== gen) return@postDelayed
                if (found.isNotEmpty() && !Link.soloChosen(this))       // a PC that isn't connected yet: offer it
                    say(JSONObject().put("view", "found").put("solo", true)
                        .put("list", JSONArray(found.map { JSONObject().put("name", it.value).put("url", it.key) })))
                else goSolo()
            }, 4000)
        } else {
            // nothing after a while: show the help
            main.postDelayed({ if (server == null && pairing == null && found.isEmpty() && searchGen === gen) say(JSONObject().put("view", "notfound")) }, 9000)
        }
    }
    private var searchGen: Any? = null

    // ---------------------------------------------------------------- on its own: the engine on this phone
    private fun goSolo() {
        finder?.stop()
        searchGen = null
        pairing = null
        Link.setSoloChosen(this, true)
        if (server == null) say(JSONObject().put("view", "starting"))
        Engine.start(this)
        io.execute {
            val ok = Engine.waitReady(this)
            main.post {
                if (!ok) { say(JSONObject().put("view", "error").put("msg", "Liner Notes couldn't start on this phone.")); return@post }
                open(Engine.URL)
            }
        }
    }

    /** While on its own, look every 30 s for a PC this phone knows (Wi-Fi or USB) and switch to it. */
    private val lookForPc = object : Runnable {
        override fun run() {
            if (!Engine.isLocal(server)) return
            io.execute {
                val saved = Link.savedUrl(this@MainActivity)
                for (u in listOfNotNull(saved, Link.USB_URL).distinct()) {
                    if (Link.alive(this@MainActivity, u) && Link.trusted(this@MainActivity, u)) {
                        main.post { if (Engine.isLocal(server)) open(u) }
                        return@execute
                    }
                }
                main.postDelayed(this, 30_000)
            }
        }
    }

    /** The PC stopped answering: after a minute, carry on without it. */
    private val pcGone = Runnable {
        val s = server
        if (s != null && !Engine.isLocal(s) && !LinkService.reachable) goSolo()
    }

    private fun pick(url: String, name: String?) {
        finder?.stop()
        io.execute {
            if (!Link.alive(this, url)) { main.post { say(JSONObject().put("view", "error").put("msg", "Couldn't reach Liner Notes at that address.")) }; return@execute }
            if (Link.trusted(this, url)) main.post { open(url) } else main.post { startPairing(url, name) }
        }
    }

    private fun startPairing(url: String, name: String?) {
        finder?.stop()
        io.execute {
            val p = Pairing.start(this, url)
            main.post {
                if (p == null) { say(JSONObject().put("view", "error").put("msg", "Liner Notes on the PC didn't answer. Is it up to date?")); return@post }
                pairing = p; pairUrl = url
                say(JSONObject().put("view", "pair").put("code", p.code).put("name", name ?: found[url] ?: "your PC"))
                pollPairing()
            }
        }
    }

    private fun pollPairing() {
        val p = pairing ?: return
        val url = pairUrl ?: return
        io.execute {
            val state = Pairing.check(this, url, p.id)
            main.post {
                if (pairing !== p) return@post
                when (state) {
                    "approved" -> { pairing = null; open(url) }
                    "denied" -> { pairing = null; say(JSONObject().put("view", "denied")) }
                    "expired" -> { pairing = null; say(JSONObject().put("view", "error").put("msg", "That took too long. Try again.")) }
                    else -> main.postDelayed({ pollPairing() }, 1500)
                }
            }
        }
    }

    private fun open(url: String) {
        finder?.stop()
        searchGen = null
        server = url
        main.removeCallbacks(lookForPc); main.removeCallbacks(pcGone)
        if (Engine.isLocal(url)) main.postDelayed(lookForPc, 30_000)
        else Link.saveUrl(this, url)
        Link.token(this, url)?.let { t ->
            CookieManager.getInstance().setCookie(url, "ln_device=$t; Max-Age=315360000; Path=/")
            CookieManager.getInstance().flush()
        }
        web.loadUrl(url.trimEnd('/') + "/")
        askNotifications()
        LinkService.start(this, url)
    }

    private fun showSetup() {
        server = null
        web.loadUrl("file:///android_asset/setup.html")
    }

    /** Send the setup page what to show (it may still be loading: it asks for the latest state when ready). */
    private var lastSay: JSONObject? = null
    private fun say(state: JSONObject) {
        lastSay = state
        if (server == null) web.evaluateJavascript("window.ln && ln.show(${JSONObject.quote(state.toString())})", null)
    }

    // ---------------------------------------------------------------- the PC's state, from LinkService
    private fun onPcState(j: JSONObject?) {
        if (server == null) return
        if (j == null && !Engine.isLocal(server)) { main.removeCallbacks(pcGone); main.postDelayed(pcGone, 40_000) }
        if (j != null) {
            val micOn = j.optJSONObject("mic")?.optString("mode", "off") != "off"
            if (micOn && !askedMic && checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
                askedMic = true
                requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), 2)   // the PC listens through this phone
            }
        }
        if (LinkService.screenOffWanted) { if (!dark) goDark() }
        else if (j != null && j.optJSONObject("track")?.optString("status") == "playing") {
            // A song is playing: the screen stays on, whatever the page did (on a plain charger nothing else holds it)
            if (dark || !keptOn) keepOn(true)
        }
    }

    private fun askNotifications() {
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED)
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == 2 && grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED) {
            server?.let { LinkService.stop(this); LinkService.start(this, it) }      // restart so it may use the mic
        }
    }

    // ---------------------------------------------------------------- the screen
    private var keptOn = false
    private fun keepOn(on: Boolean) {
        keptOn = on
        if (on) {
            dark = false
            window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
            window.attributes = window.attributes.apply { screenBrightness = WindowManager.LayoutParams.BRIGHTNESS_OVERRIDE_NONE }
        } else {
            window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        }
    }

    /** Nothing playing for a while: go black and let the phone's own screen timeout switch it off. */
    private fun goDark() {
        dark = true
        keepOn(false)
        window.attributes = window.attributes.apply { screenBrightness = 0f }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        if (!LinkService.screenOffWanted) { keepOn(true) }   // LinkService woke the screen for the next song
    }

    override fun onResume() {
        super.onResume()
        fullScreen()
        if (dark && !LinkService.screenOffWanted) keepOn(true)
        io.execute { Updater.check(this) }
    }

    @Suppress("DEPRECATION")
    private fun fullScreen() {
        if (Build.VERSION.SDK_INT >= 30) {
            window.setDecorFitsSystemWindows(false)
            window.insetsController?.let {
                it.hide(WindowInsets.Type.statusBars() or WindowInsets.Type.navigationBars())
                it.systemBarsBehavior = WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
            }
        } else {
            window.decorView.systemUiVisibility = (View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY or View.SYSTEM_UI_FLAG_FULLSCREEN or
                    View.SYSTEM_UI_FLAG_HIDE_NAVIGATION or View.SYSTEM_UI_FLAG_LAYOUT_STABLE or
                    View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION or View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN)
        }
    }

    // ---------------------------------------------------------------- links, back button, sharing
    private fun openOutside(uri: Uri): Boolean {
        val s = server
        if (uri.scheme == "file") return false
        if (s != null && (uri.scheme == "http" || uri.scheme == "https") && uri.host == Link.host(s)) return false
        openExternal(uri)
        return true
    }

    private fun openExternal(uri: Uri) {
        try { startActivity(Intent(Intent.ACTION_VIEW, uri).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) } catch (e: Exception) { }
    }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        if (server == null) { super.onBackPressed(); return }
        // close an open page (Settings, a song...) first, like Escape on the PC
        web.evaluateJavascript("(function(){var p=document.querySelector('.panel.open');if(p){p.classList.remove('open');return 1}return 0})()") { r ->
            if (r == "1") return@evaluateJavascript
            AlertDialog.Builder(this)
                .setTitle("Liner Notes")
                .setItems(arrayOf("Keep showing it", if (Engine.isLocal(server)) "Connect to a PC" else "Connect to a different PC", "Close the app")) { _, which ->
                    when (which) {
                        1 -> switchPc()
                        2 -> { LinkService.stop(this); finish() }
                    }
                }.show()
        }
    }

    private fun switchPc() {
        LinkService.stop(this)
        main.removeCallbacks(lookForPc); main.removeCallbacks(pcGone)
        if (!Engine.isLocal(server)) Link.forget(this)
        showSetup(); search()
    }

    private fun shareText(title: String, text: String, url: String) {
        val i = Intent(Intent.ACTION_SEND).setType("text/plain")
            .putExtra(Intent.EXTRA_SUBJECT, title)
            .putExtra(Intent.EXTRA_TEXT, "$text $url")
        startActivity(Intent.createChooser(i, "Share").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }

    private fun shareImage(url: String, name: String) {
        io.execute {
            try {
                val dir = File(cacheDir, "shared").apply { mkdirs() }
                val f = File(dir, name.replace(Regex("[^\\w.\\-]"), "_"))
                val conn = URL(url).openConnection()
                server?.let { s -> Link.token(this, s)?.let { conn.setRequestProperty("X-LinerNotes-Device", it) } }
                conn.getInputStream().use { inp -> f.outputStream().use { inp.copyTo(it) } }
                val uri = FileProvider.getUriForFile(this, "$packageName.files", f)
                val i = Intent(Intent.ACTION_SEND).setType("image/png").putExtra(Intent.EXTRA_STREAM, uri)
                    .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                main.post { startActivity(Intent.createChooser(i, "Share").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
            } catch (e: Exception) { }
        }
    }

    /** What the page (setup page or the PC's screen) can ask the app to do. */
    inner class Bridge {
        @JavascriptInterface fun keepAwake(on: Boolean) = main.post {
            if (on) { LinkService.screenOffWanted = false; keepOn(true) } else keepOn(false)     // a tap on the dark screen wakes it
        }.let { }
        @JavascriptInterface fun screenOff() = main.post { goDark() }.let { }
        @JavascriptInterface fun share(title: String?, text: String?, url: String?) = main.post { shareText(title ?: "", text ?: "", url ?: "") }.let { }
        @JavascriptInterface fun shareImage(url: String, name: String) = this@MainActivity.shareImage(url, name)
        @JavascriptInterface fun openUrl(url: String) = main.post { if (url.startsWith("https://")) openExternal(Uri.parse(url)) }.let { }
        @JavascriptInterface fun mode(): String = if (Engine.isLocal(server)) "solo" else "pc"
        @JavascriptInterface fun solo() = main.post { goSolo() }.let { }
        @JavascriptInterface fun switchPc() = main.post { this@MainActivity.switchPc() }.let { }
        // updates for the copy installed from GitHub
        @JavascriptInterface fun appUpdate(): String = Updater.json(this@MainActivity)
        @JavascriptInterface fun checkAppUpdate() = io.execute { Updater.check(this@MainActivity, force = true) }
        @JavascriptInterface fun installAppUpdate() { Thread { Updater.install(this@MainActivity) { r -> main.post(r) } }.start() }
        @JavascriptInterface fun version(): String = try { packageManager.getPackageInfo(packageName, 0).versionName ?: "" } catch (e: Exception) { "" }
        // setup page
        @JavascriptInterface fun ready() = main.post { lastSay?.let { say(it) } }.let { }
        @JavascriptInterface fun retry() = main.post { pairing = null; connect(auto = false) }.let { }
        @JavascriptInterface fun connectTo(url: String, name: String?) = main.post { pick(url, name) }.let { }
        @JavascriptInterface fun manual(address: String) = main.post {
            var a = address.trim()
            if (a.isEmpty()) return@post
            if (!a.startsWith("http")) a = "http://$a"
            if (Uri.parse(a).port == -1) a = a.trimEnd('/') + ":8765"
            pick(a, null)
        }.let { }
    }

    override fun onDestroy() {
        finder?.stop()
        LinkService.onState = null
        super.onDestroy()
    }
}
