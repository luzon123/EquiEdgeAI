package ai.equiedge.mobile.overlay

import android.content.Context
import android.graphics.Bitmap
import android.graphics.PixelFormat
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.Image
import android.media.ImageReader
import android.media.projection.MediaProjection
import android.os.Handler
import android.os.Looper
import android.util.Log
import java.io.ByteArrayOutputStream

private const val TAG = "EquiEdgeAnalyze"

/**
 * Screen capture on top of an already-granted [MediaProjection].
 *
 * IMPORTANT (Android 14+): MediaProjection#createVirtualDisplay() throws a
 * SecurityException if called more than once on the same MediaProjection
 * instance ("Don't take multiple captures by invoking
 * MediaProjection#createVirtualDisplay multiple times on the same
 * instance" — https://developer.android.com/media/grow/media-projection).
 * This class therefore creates exactly ONE VirtualDisplay/ImageReader pair
 * for the entire lifetime of the granted [projection] — once in [init],
 * released once via [release] — and serves every subsequent ANALYZE tap
 * from that same persistent pipeline instead of tearing it down and
 * recreating it per tap. Recreating it per tap (the previous
 * implementation) was the confirmed root cause of a crash on the second
 * ANALYZE press.
 *
 * Frames arrive continuously while the pipeline is alive (screen mirroring
 * behaves like screen recording, emitting a new frame whenever the
 * mirrored content changes) but are acquired-and-closed at near-zero cost
 * unless a capture is actually pending — only the one frame requested via
 * [captureOnce] gets decoded into a Bitmap and encoded to PNG.
 *
 * Everything in this class (the ImageReader listener, [captureOnce],
 * timeouts) runs on the same main-thread Looper, so there is no real
 * concurrency to guard against pendingCallback with locks/volatile.
 */
class ScreenCapture(context: Context, private val projection: MediaProjection) {

    private val handler = Handler(Looper.getMainLooper())
    private val metrics = context.resources.displayMetrics
    private val width = metrics.widthPixels
    private val height = metrics.heightPixels

    private val imageReader = ImageReader.newInstance(width, height, PixelFormat.RGBA_8888, 2)
    private var virtualDisplay: VirtualDisplay? = null
    private var pendingCallback: ((ByteArray?) -> Unit)? = null

    private val timeoutRunnable = Runnable {
        Log.w(TAG, "[ANALYZE] capture timed out waiting for a frame")
        completePending(null)
    }

    init {
        imageReader.setOnImageAvailableListener({ reader -> onFrame(reader) }, handler)
        virtualDisplay = projection.createVirtualDisplay(
            "EquiEdgeCapture",
            width, height, metrics.densityDpi,
            DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
            imageReader.surface,
            /* callback = */ null,
            handler,
        )
        Log.d(TAG, "[ANALYZE] persistent capture pipeline created: ${width}x$height")
    }

    /** Invokes [onResult] with PNG bytes, or null if capture failed/timed out/already busy. */
    fun captureOnce(onResult: (ByteArray?) -> Unit) {
        if (virtualDisplay == null) {
            Log.e(TAG, "[ANALYZE][ERROR] captureOnce called after release()")
            onResult(null)
            return
        }
        if (pendingCallback != null) {
            Log.w(TAG, "[ANALYZE] capture already in progress, ignoring extra request")
            onResult(null)
            return
        }
        pendingCallback = onResult
        Log.d(TAG, "[ANALYZE] waiting for next frame")
        handler.postDelayed(timeoutRunnable, 4_000)
    }

    private fun completePending(bytes: ByteArray?) {
        handler.removeCallbacks(timeoutRunnable)
        val cb = pendingCallback ?: return
        pendingCallback = null
        cb(bytes)
    }

    private fun onFrame(reader: ImageReader) {
        val image = reader.acquireLatestImage() ?: return
        if (pendingCallback == null) {
            // Just the display mirroring continuing in the background with no
            // ANALYZE request waiting — drop it cheaply without decoding.
            image.close()
            return
        }
        try {
            val bytes = encodePng(image)
            Log.d(TAG, "[ANALYZE] image received, encoded ${bytes.size} bytes")
            completePending(bytes)
        } catch (e: Exception) {
            Log.e(TAG, "[ANALYZE][ERROR] frame encode failed: ${e.javaClass.simpleName}: ${e.message}")
            completePending(null)
        } finally {
            image.close()
        }
    }

    private fun encodePng(image: Image): ByteArray {
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
        return out.toByteArray()
    }

    /** Resize the persistent virtual display if the mirrored content's size changes
     * (e.g. rotation) — see MediaProjection.Callback#onCapturedContentResize. */
    fun resize(newWidth: Int, newHeight: Int) {
        runCatching { virtualDisplay?.resize(newWidth, newHeight, metrics.densityDpi) }
            .onFailure { Log.e(TAG, "[ANALYZE][ERROR] virtual display resize failed: ${it.message}") }
    }

    /** Tears down the persistent VirtualDisplay/ImageReader. Safe to call more than once
     * (idempotent) — called from OverlayService.onDestroy() AND from the MediaProjection's
     * onStop callback, whichever happens first. */
    fun release() {
        if (virtualDisplay == null) return
        completePending(null)
        virtualDisplay?.release()
        virtualDisplay = null
        imageReader.close()
        Log.d(TAG, "[ANALYZE] capture pipeline released")
    }
}
