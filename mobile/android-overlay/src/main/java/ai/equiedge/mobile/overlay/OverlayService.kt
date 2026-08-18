package ai.equiedge.mobile.overlay

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.content.res.Configuration
import android.graphics.PixelFormat
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.view.Gravity
import android.view.LayoutInflater
import android.view.MotionEvent
import android.view.View
import android.view.ViewConfiguration
import android.view.WindowManager
import android.widget.TextView
import androidx.core.app.NotificationCompat
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import kotlin.math.abs

/**
 * Floating "ANALYZE" overlay that stays visible above other apps (ClubGG)
 * and, on tap, captures the current screen and shows a winrate + action
 * result — the Android counterpart to the iOS Share Extension flow.
 *
 * Lifecycle: started once from OverlayPermissionActivity after the user
 * grants both the SYSTEM_ALERT_WINDOW overlay permission and the
 * MediaProjection screen-capture consent. From then on this Service runs
 * independently of any Activity — closing/backgrounding the host app does
 * not stop it; only explicitly stopping the service (or the OS killing
 * the process) does.
 */
class OverlayService : Service() {

    companion object {
        const val EXTRA_RESULT_CODE = "result_code"
        const val EXTRA_DATA = "data"
        private const val NOTIFICATION_CHANNEL_ID = "equiedge_overlay"
        private const val NOTIFICATION_ID = 1001

        /** Below this drag distance (dp-independent px), a touch is a tap, not a drag —
         * keeps the button from mis-firing analysis on a slightly jittery finger lift. */
        private const val CAPTURE_SETTLE_DELAY_MS = 220L
        private const val RESULT_AUTO_DISMISS_MS = 4_000L
    }

    private lateinit var windowManager: WindowManager
    private lateinit var mediaProjectionManager: MediaProjectionManager
    private val mainHandler = Handler(Looper.getMainLooper())
    private val bgExecutor: ExecutorService = Executors.newSingleThreadExecutor()

    private var mediaProjection: MediaProjection? = null
    private var screenCapture: ScreenCapture? = null

    private var buttonView: View? = null
    private var buttonParams: WindowManager.LayoutParams? = null
    private var resultView: View? = null
    private var isAnalyzing = false

    // MARK: - Lifecycle

    override fun onCreate() {
        super.onCreate()
        windowManager = getSystemService(WINDOW_SERVICE) as WindowManager
        mediaProjectionManager = getSystemService(MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
        createNotificationChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val resultCode = intent?.getIntExtra(EXTRA_RESULT_CODE, 0) ?: 0
        val data = intent?.getParcelableExtra<Intent>(EXTRA_DATA)

        if (resultCode == 0 || data == null) {
            // Started without a fresh consent grant (e.g. system restarted the
            // service) — a MediaProjection token cannot be recreated without
            // user consent, so there is nothing safe to do but stop.
            stopSelf()
            return START_NOT_STICKY
        }

        // Must start the foreground service (with the mediaProjection type on
        // API 29+) BEFORE requesting the MediaProjection instance, or the
        // system throws — this ordering is required, not stylistic.
        startForegroundWithNotification()

        mediaProjection = mediaProjectionManager.getMediaProjection(resultCode, data)
        val projection = mediaProjection
        if (projection == null) {
            stopSelf()
            return START_NOT_STICKY
        }
        projection.registerCallback(object : MediaProjection.Callback() {
            override fun onStop() {
                // User revoked capture permission (or the grant expired) from
                // outside our control (e.g. the system's screen-capture
                // notification). Tear down cleanly rather than crash on the
                // next capture attempt.
                mediaProjection = null
                screenCapture = null
                showResultBubble(null, "Screen capture permission was revoked. Reopen the app.")
            }
        }, mainHandler)

        screenCapture = ScreenCapture(applicationContext, projection)
        if (buttonView == null) addOverlayButton()

        return START_NOT_STICKY
    }

    override fun onDestroy() {
        super.onDestroy()
        buttonView?.let { runCatching { windowManager.removeView(it) } }
        resultView?.let { runCatching { windowManager.removeView(it) } }
        mediaProjection?.stop()
        bgExecutor.shutdownNow()
    }

    override fun onConfigurationChanged(newConfig: Configuration) {
        super.onConfigurationChanged(newConfig)
        // Keep the button on-screen across rotation instead of letting it
        // drift outside the new bounds.
        val params = buttonParams ?: return
        val metrics = resources.displayMetrics
        params.x = params.x.coerceIn(0, metrics.widthPixels - (buttonView?.width ?: 0))
        params.y = params.y.coerceIn(0, metrics.heightPixels - (buttonView?.height ?: 0))
        buttonView?.let { runCatching { windowManager.updateViewLayout(it, params) } }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    // MARK: - Overlay button (drag + tap)

    private fun addOverlayButton() {
        val view = LayoutInflater.from(this).inflate(R.layout.overlay_button, null)
        val params = WindowManager.LayoutParams(
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY, // requires minSdk 26
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE,
            PixelFormat.TRANSLUCENT,
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            x = 24
            y = 160
        }

        val touchSlop = ViewConfiguration.get(this).scaledTouchSlop
        var downRawX = 0f
        var downRawY = 0f
        var downParamX = 0
        var downParamY = 0

        view.setOnTouchListener { v, event ->
            when (event.action) {
                MotionEvent.ACTION_DOWN -> {
                    downRawX = event.rawX
                    downRawY = event.rawY
                    downParamX = params.x
                    downParamY = params.y
                    true
                }
                MotionEvent.ACTION_MOVE -> {
                    val dx = (event.rawX - downRawX).toInt()
                    val dy = (event.rawY - downRawY).toInt()
                    params.x = downParamX + dx
                    params.y = downParamY + dy
                    runCatching { windowManager.updateViewLayout(v, params) }
                    true
                }
                MotionEvent.ACTION_UP -> {
                    val dx = abs(event.rawX - downRawX)
                    val dy = abs(event.rawY - downRawY)
                    if (dx < touchSlop && dy < touchSlop) {
                        onAnalyzeTapped()
                    }
                    true
                }
                else -> false
            }
        }

        windowManager.addView(view, params)
        buttonView = view
        buttonParams = params
    }

    // MARK: - Capture -> upload -> result

    private fun onAnalyzeTapped() {
        if (isAnalyzing) return
        val capture = screenCapture ?: run {
            showResultBubble(null, "Screen capture isn't ready. Reopen the app.")
            return
        }
        isAnalyzing = true

        // The overlay must not appear in its own screenshot — hide it, give
        // the compositor a frame to actually redraw without it, THEN
        // capture. Capturing in the same frame the view was hidden risks
        // grabbing a stale composited frame that still includes the button.
        buttonView?.visibility = View.GONE
        mainHandler.postDelayed({
            capture.captureOnce { pngBytes ->
                buttonView?.visibility = View.VISIBLE
                if (pngBytes == null) {
                    isAnalyzing = false
                    showResultBubble(null, "Couldn't capture the screen. Try again.")
                    return@captureOnce
                }
                bgExecutor.execute {
                    val result = AnalyzeApi.analyze(pngBytes)
                    mainHandler.post {
                        isAnalyzing = false
                        when (result) {
                            is AnalyzeApi.Result.Success ->
                                showResultBubble(result, null)
                            is AnalyzeApi.Result.Failure ->
                                showResultBubble(null, result.message)
                        }
                    }
                }
            }
        }, CAPTURE_SETTLE_DELAY_MS)
    }

    // MARK: - Result bubble

    private fun showResultBubble(success: AnalyzeApi.Result.Success?, errorMessage: String?) {
        removeResultBubble()

        val view = LayoutInflater.from(this).inflate(R.layout.overlay_result, null)
        val winrateText = view.findViewById<TextView>(R.id.winrateText)
        val actionText = view.findViewById<TextView>(R.id.actionText)

        if (success != null) {
            winrateText.visibility = View.VISIBLE
            winrateText.text = "${Math.round(success.winrate)}%"
            actionText.text = success.action
            actionText.setTextColor(
                if (success.action == "FOLD") 0xFFE5484D.toInt() else 0xFF3DD68C.toInt()
            )
        } else {
            winrateText.visibility = View.GONE
            actionText.text = errorMessage ?: "Analysis failed. Try again."
            actionText.setTextColor(0xFF8B97A6.toInt())
        }

        val anchor = buttonParams
        val params = WindowManager.LayoutParams(
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE,
            PixelFormat.TRANSLUCENT,
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            x = anchor?.x ?: 24
            y = (anchor?.y ?: 160) + 140
        }

        view.setOnClickListener { removeResultBubble() }
        windowManager.addView(view, params)
        resultView = view
        mainHandler.postDelayed({ removeResultBubble() }, RESULT_AUTO_DISMISS_MS)
    }

    private fun removeResultBubble() {
        resultView?.let { runCatching { windowManager.removeView(it) } }
        resultView = null
    }

    // MARK: - Foreground notification (required while capturing the screen)

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val channel = NotificationChannel(
            NOTIFICATION_CHANNEL_ID, "EquiEdgeAI Overlay", NotificationManager.IMPORTANCE_MIN,
        ).apply { description = "Keeps the floating analysis button available while you play." }
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }

    private fun startForegroundWithNotification() {
        val notification: Notification = NotificationCompat.Builder(this, NOTIFICATION_CHANNEL_ID)
            .setContentTitle("EquiEdgeAI is running")
            .setContentText("Tap the floating button to analyze a hand.")
            .setSmallIcon(android.R.drawable.ic_menu_view) // replace with a real app icon asset
            .setPriority(NotificationCompat.PRIORITY_MIN)
            .setOngoing(true)
            .build()

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(
                NOTIFICATION_ID, notification,
                android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION,
            )
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }
}
