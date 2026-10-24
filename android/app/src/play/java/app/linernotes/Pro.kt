package app.linernotes

import android.app.Activity
import android.content.Context
import com.android.billingclient.api.AcknowledgePurchaseParams
import com.android.billingclient.api.BillingClient
import com.android.billingclient.api.BillingClient.BillingResponseCode
import com.android.billingclient.api.BillingClient.ProductType
import com.android.billingclient.api.BillingClientStateListener
import com.android.billingclient.api.BillingFlowParams
import com.android.billingclient.api.BillingResult
import com.android.billingclient.api.PendingPurchasesParams
import com.android.billingclient.api.ProductDetails
import com.android.billingclient.api.Purchase
import com.android.billingclient.api.QueryProductDetailsParams
import com.android.billingclient.api.QueryPurchasesParams
import org.json.JSONArray
import org.json.JSONObject

/**
 * Liner Notes Plus, on Google Play: $1 a month, $10 a year, or $25 once (prices are set in Play Console).
 * It unlocks lyrics, the song page, the shelf and history, the accounts, the side-end warning and waking
 * the screen. Play keeps the purchases; this asks it what this Google account bought.
 * In Play Console: a subscription "liner_notes_plus" with base plans "monthly" and "yearly", and a one-time
 * product "liner_notes_plus_lifetime".
 */
object Pro {
    private const val SUB = "liner_notes_plus"
    private const val LIFETIME = "liner_notes_plus_lifetime"
    const val ENABLED = true

    private var app: Context? = null
    private var client: BillingClient? = null
    @Volatile private var sub: ProductDetails? = null
    @Volatile private var once: ProductDetails? = null
    /** idle, buying, pending (Play is waiting for the payment), failed */
    @Volatile var state = "idle"

    private fun prefs(c: Context) = c.getSharedPreferences("pro", Context.MODE_PRIVATE)
    /** What Play said last time, so the app knows offline too. */
    fun unlocked(c: Context): Boolean = prefs(c).getBoolean("unlocked", false)

    fun start(c: Context) {
        val ctx = c.applicationContext
        app = ctx
        client?.let { if (it.isReady) { refresh(); return } }
        val bc = BillingClient.newBuilder(ctx)
            .setListener { result, purchases ->
                when (result.responseCode) {
                    BillingResponseCode.OK -> { state = "idle"; purchases?.forEach { handle(it) } }
                    BillingResponseCode.USER_CANCELED -> state = "idle"
                    BillingResponseCode.ITEM_ALREADY_OWNED -> { state = "idle"; refresh() }
                    else -> state = "failed"
                }
            }
            .enablePendingPurchases(PendingPurchasesParams.newBuilder().enableOneTimeProducts().build())
            .enableAutoServiceReconnection()
            .build()
        client = bc
        bc.startConnection(object : BillingClientStateListener {
            override fun onBillingSetupFinished(result: BillingResult) {
                if (result.responseCode == BillingResponseCode.OK) { loadProducts(); refresh() }
            }
            override fun onBillingServiceDisconnected() {}
        })
    }

    private fun loadProducts() {
        val bc = client ?: return
        for ((id, type) in listOf(SUB to ProductType.SUBS, LIFETIME to ProductType.INAPP)) {
            val params = QueryProductDetailsParams.newBuilder().setProductList(listOf(
                QueryProductDetailsParams.Product.newBuilder().setProductId(id).setProductType(type).build())).build()
            bc.queryProductDetailsAsync(params) { result, details ->
                if (result.responseCode != BillingResponseCode.OK) return@queryProductDetailsAsync
                val pd = details.productDetailsList.firstOrNull() ?: return@queryProductDetailsAsync
                if (type == ProductType.SUBS) sub = pd else once = pd
            }
        }
    }

    /** Ask Play what this account owns (at start, on resume, and for "Restore purchase"). */
    fun refresh() {
        val bc = client ?: return
        val found = mutableListOf<Purchase>()
        var left = 2
        for (type in listOf(ProductType.SUBS, ProductType.INAPP)) {
            bc.queryPurchasesAsync(QueryPurchasesParams.newBuilder().setProductType(type).build()) { result, purchases ->
                synchronized(found) {
                    if (result.responseCode == BillingResponseCode.OK) found += purchases
                    left--
                    if (left == 0) {
                        found.forEach { handle(it) }
                        // only an answer from Play for both kinds can take the unlock away
                        app?.let { c -> prefs(c).edit().putBoolean("unlocked", found.any { it.purchaseState == Purchase.PurchaseState.PURCHASED }).apply() }
                    }
                }
            }
        }
    }

    private fun handle(p: Purchase) {
        val c = app ?: return
        when (p.purchaseState) {
            Purchase.PurchaseState.PURCHASED -> {
                prefs(c).edit().putBoolean("unlocked", true).apply()
                if (state == "pending") state = "idle"
                // Play refunds a purchase that isn't acknowledged within 3 days
                if (!p.isAcknowledged) client?.acknowledgePurchase(
                    AcknowledgePurchaseParams.newBuilder().setPurchaseToken(p.purchaseToken).build()) { }
            }
            Purchase.PurchaseState.PENDING -> state = "pending"
            else -> {}
        }
    }

    /** plan: "monthly", "yearly" or "lifetime". */
    fun buy(a: Activity, plan: String) {
        val bc = client ?: return
        val pp = if (plan == "lifetime") {
            val pd = once ?: run { state = "failed"; return }
            BillingFlowParams.ProductDetailsParams.newBuilder().setProductDetails(pd).build()
        } else {
            val pd = sub ?: run { state = "failed"; return }
            val offer = pd.subscriptionOfferDetails?.firstOrNull { it.basePlanId == plan && it.offerId == null }
                ?: run { state = "failed"; return }
            BillingFlowParams.ProductDetailsParams.newBuilder().setProductDetails(pd).setOfferToken(offer.offerToken).build()
        }
        state = "buying"
        val r = bc.launchBillingFlow(a, BillingFlowParams.newBuilder().setProductDetailsParamsList(listOf(pp)).build())
        if (r.responseCode != BillingResponseCode.OK) state = "failed"
    }

    private fun price(plan: String): String? = if (plan == "lifetime") {
        once?.oneTimePurchaseOfferDetails?.formattedPrice
    } else {
        sub?.subscriptionOfferDetails?.firstOrNull { it.basePlanId == plan && it.offerId == null }
            ?.pricingPhases?.pricingPhaseList?.lastOrNull()?.formattedPrice
    }

    /** For the page: is it unlocked, and the three plans with Play's prices (in the buyer's currency). */
    fun json(c: Context): String {
        val plans = JSONArray()
        for ((id, fallback) in listOf("monthly" to "$1", "yearly" to "$10", "lifetime" to "$25"))
            plans.put(JSONObject().put("id", id).put("price", price(id) ?: fallback).put("ready", price(id) != null))
        return JSONObject().put("enabled", true).put("unlocked", unlocked(c)).put("state", state).put("plans", plans).toString()
    }
}
