package ai.equiedge.mobile.overlay

import android.content.Context
import android.graphics.Bitmap
import android.graphics.PixelFormat
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.ImageReader
import android.media.projection.MediaProjection
import android.os.Handler
import android.os.Looper
import java.io.ByteArrayOutputStream

/**
 * One-shot screen capture on top of an already-granted [MediaProjection].
 *
 * Grabs exactly one frame, converts it to PNG bytes, and tears the capture
 * pipeline (ImageReader + VirtualDisplay) down immediately afterwards — it
 * does NOT keep a continuous screen-recording session running between
 * analyses. That matters for both battery (recording continuously would
 * burn power for no reason between taps) and correctness (a stale frame
 * from a previous VirtualDisplay could otherwise leak into the next
 * capture).
 *
 * The [MediaProjection] itself is reused across calls by the caller
 * (OverlayService) — only the one-shot VirtualDisplay/ImageReader pair is
 * per-capture. Re-requesting the system screen-capture consent dialog on
 * every tap would defeat the entire "one-tap analysis" goal.
 */
class ScreenCapture(private val context: Context, private val projection: MediaProjection) {

    private val handler = Handler(Looper.getMainLooper())

    /** Invokes [onResult] with PNG bytes, or null if capture failed/timed out. */
    fun captureOnce(onResult: (ByteArray?) -> Unit) {
        val metrics = context.resources.displayMetrics
        val width = metrics.widthPixels
        val height = metrics.heightPixels
        val density = metrics.densityDpi

        val imageReader = ImageReader.newInstance(width, height, PixelFormat.RGBA_8888, 2)
        var virtualDisplay: VirtualDisplay? = null
        var finished = false

        fun finish(bytes: ByteArray?) {
            if (finished) return
            finished = true
            virtualDisplay?.release()
            imageReader.close()
            onResult(bytes)
        }

        // Safety timeout: if no frame arrives (e.g. the projection died
        // mid-capture), don't hang the overlay's tap gesture forever.
        val timeoutRunnable = Runnable { finish(null) }
        handler.postDelayed(timeoutRunnable, 4_000)

        imageReader.setOnImageAvailableListener({ reader ->
            handler.removeCallbacks(timeoutRunnable)
            val image = reader.acquireLatestImage()
            if (image == null) {
                finish(null)
                return@setOnImageAvailableListener
            }
            try {
                val plane = image.planes[0]
                val pixelStride = plane.pixelStride
                val rowStride = plane.rowStride
                val rowPadding = rowStride - pixelStride * width

                val bitmap = Bitmap.createBitmap(
                    width + rowPadding / pixelStride, height, Bitmap.Config.ARGB_8888
                )
                bitmap.copyPixelsFromBuffer(plane.buffer)
                val cropped = Bitmap.createBitmap(bitmap, 0, 0, width, height)

                val out = ByteArrayOutputStream()
                cropped.compress(Bitmap.CompressFormat.PNG, 100, out)
                bitmap.recycle()
                cropped.recycle()
                finish(out.toByteArray())
            } catch (e: Exception) {
                finish(null)
            } finally {
                image.close()
            }
        }, handler)

        virtualDisplay = projection.createVirtualDisplay(
            "EquiEdgeCapture",
            width, height, density,
            DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
            imageReader.surface,
            /* callback = */ null,
            handler,
        )
    }
}
