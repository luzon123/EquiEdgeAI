package ai.equiedge.mobile.overlay

import android.content.Intent
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod

/**
 * Thin bridge so the RN HomeScreen can launch the native overlay setup flow.
 * SYSTEM_ALERT_WINDOW and MediaProjection consent are both
 * Activity-result-driven native permission dialogs with no RN/JS
 * equivalent — OverlayPermissionActivity handles them and starts
 * OverlayService; this module's only job is starting that Activity.
 */
class OverlayModule(reactContext: ReactApplicationContext) :
    ReactContextBaseJavaModule(reactContext) {

    override fun getName(): String = "OverlayModule"

    @ReactMethod
    fun startOverlaySetup() {
        val intent = Intent(reactApplicationContext, OverlayPermissionActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        reactApplicationContext.startActivity(intent)
    }
}
