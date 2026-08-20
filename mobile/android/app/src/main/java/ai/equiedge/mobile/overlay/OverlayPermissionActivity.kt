package ai.equiedge.mobile.overlay

import android.content.Intent
import android.media.projection.MediaProjectionManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat

/**
 * One-time setup screen: grant the overlay permission, grant screen-capture
 * consent, start OverlayService. After this, the floating button persists
 * independently of this Activity — the user never needs to open the app
 * again unless they want to stop the overlay or permissions get revoked.
 *
 * This is intentionally NOT built with the RN JS layer: SYSTEM_ALERT_WINDOW
 * and MediaProjection are both Activity-result-driven native permission
 * flows with no React Native equivalent, so they live in a small native
 * Activity that a host app can launch (e.g. from a RN screen via
 * `NativeModules` or as this library's own launcher activity).
 */
class OverlayPermissionActivity : ComponentActivity() {

    private lateinit var statusText: TextView
    private lateinit var actionButton: Button

    private val overlayPermissionLauncher =
        registerForActivityResult(ActivityResultContracts.StartActivityForResult()) {
            refreshUi()
        }

    private val screenCaptureLauncher =
        registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
            if (result.resultCode == RESULT_OK && result.data != null) {
                val intent = Intent(this, OverlayService::class.java).apply {
                    putExtra(OverlayService.EXTRA_RESULT_CODE, result.resultCode)
                    putExtra(OverlayService.EXTRA_DATA, result.data)
                }
                ContextCompat.startForegroundService(this, intent)
                finish() // overlay now runs independently — nothing left to show here
            } else {
                statusText.text = "Screen capture permission is required for analysis."
            }
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        statusText = TextView(this).apply { textSize = 16f; setPadding(48, 96, 48, 24) }
        actionButton = Button(this).apply { setPadding(48, 24, 48, 24) }
        actionButton.setOnClickListener { onActionClicked() }

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            addView(statusText)
            addView(actionButton)
        }
        setContentView(root)

        refreshUi()
    }

    override fun onResume() {
        super.onResume()
        refreshUi() // catches the user granting overlay permission in Settings and returning
    }

    private fun hasOverlayPermission(): Boolean =
        Build.VERSION.SDK_INT < Build.VERSION_CODES.M || Settings.canDrawOverlays(this)

    private fun refreshUi() {
        if (!hasOverlayPermission()) {
            statusText.text = "EquiEdgeAI needs permission to draw over other apps for the " +
                "floating analysis button."
            actionButton.text = "Grant Overlay Permission"
        } else {
            statusText.text = "Next, allow screen capture so the floating button can analyze " +
                "your table. You'll only be asked once."
            actionButton.text = "Enable Screen Capture"
        }
    }

    private fun onActionClicked() {
        if (!hasOverlayPermission()) {
            val intent = Intent(
                Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:$packageName"),
            )
            overlayPermissionLauncher.launch(intent)
            return
        }

        val projectionManager = getSystemService(MediaProjectionManager::class.java)
        screenCaptureLauncher.launch(projectionManager.createScreenCaptureIntent())
    }
}
