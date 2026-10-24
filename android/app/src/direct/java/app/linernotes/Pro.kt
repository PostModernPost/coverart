package app.linernotes

import android.app.Activity
import android.content.Context

/** The copy from GitHub has everything unlocked (Liner Notes Plus is sold only through Google Play). */
object Pro {
    const val ENABLED = false
    var state = "idle"
    fun unlocked(c: Context) = true
    fun start(c: Context) {}
    fun refresh() {}
    fun buy(a: Activity, plan: String) {}
    fun json(c: Context): String = """{"enabled":false,"unlocked":true,"state":"idle","plans":[]}"""
}
